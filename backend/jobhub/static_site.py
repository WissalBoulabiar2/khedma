"""Static site export: database -> plain HTML/JSON for free static hosting.

Builds the same pages as the dynamic server (same templates), except that
the catalogue's search and filters run in the browser on ``data/jobs.json``.

    python -m jobhub.cli build-static --out _site --base-path /my-repo --site-url https://me.github.io/my-repo
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit
from xml.sax.saxutils import escape as xml_escape

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload
from starlette.datastructures import QueryParams

from . import queries
from .models import Company, Job, Listing
from .normalize.locations import GOVERNORATES, governorate_choices
from .normalize.taxonomy import (CONTRACT_TYPES, DOMAIN_LABELS, EXPERIENCE_LEVELS, SPECIALIZATION_DOMAIN,
                                 SPECIALIZATION_LABELS, WORKPLACES)
from .normalize.text import fold, utcnow
from .search import POSTED_WITHIN
from .web import SITE_NAME, templates

STATIC_DIR = Path(__file__).parent / "static"
# Closed offers keep their page (with a "no longer active" notice) for a while,
# so links shared recently don't break.
KEEP_CLOSED_PAGES = timedelta(days=30)
_LINK = re.compile(r'(\s(?:href|src|action)=")(/[^"]*)"')


def rewrite_links(html: str, base: str) -> str:
    """Root-relative links -> base path, with trailing slashes for page URLs
    (static hosts serve /emplois/ from emplois/index.html)."""
    def fix(m: re.Match[str]) -> str:
        url = m.group(2)
        if url.startswith("//"):  # protocol-relative: external
            return m.group(0)
        path, query, frag = re.match(r"([^?#]*)(\?[^#]*)?(#.*)?$", url).groups()
        if not path.endswith("/") and "." not in path.rsplit("/", 1)[-1]:
            path += "/"
        return f'{m.group(1)}{base}{path}{query or ""}{frag or ""}"'
    return _LINK.sub(fix, html)


class _Builder:
    def __init__(self, s: Session, out: Path, base_path: str, site_url: str):
        self.s, self.out = s, out
        self.base = "/" + base_path.strip("/") if base_path.strip("/") else ""
        self.site_url = site_url.rstrip("/")
        self.build_id = utcnow().strftime("%Y%m%d%H%M")
        parts = urlsplit(self.site_url or "https://localhost")
        self.request = lambda path: SimpleNamespace(
            url=SimpleNamespace(path=path, scheme=parts.scheme, netloc=parts.netloc), query_params=QueryParams(""))
        self.pages: list[str] = []

    def render(self, rel: str, template: str, url_path: str, **ctx: Any) -> None:
        ctx.update(request=self.request(url_path), static_mode=True, base_path=self.base, build_id=self.build_id,
                   canonical_root=self.site_url)
        html = templates.env.get_template(template).render(**ctx)
        html = rewrite_links(html, self.base)
        target = self.out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
        if rel.endswith("index.html"):
            self.pages.append(url_path)

    def job_index(self, jobs: list[Job]) -> dict[str, Any]:
        rows = []
        for j in jobs:
            rows.append({
                "i": j.id, "s": j.slug, "t": j.title,
                "c": j.company.name if j.company else None, "cs": j.company.slug if j.company else None,
                "lg": j.company.logo_url if j.company else None,
                "l": j.location_label, "g": j.governorate, "ci": j.city, "d": j.domain, "sp": j.specialization,
                "ct": j.contract_list, "e": j.experience_level, "w": j.workplace,
                "p": (j.published_at or j.first_seen_at).isoformat() + "Z",
                "k": j.skills or [],
                # Start of the description, for keyword search (full text is on the job page).
                "x": fold(j.description_text or "")[:600],
            })
        companies = {j.company.slug: j.company.name for j in jobs if j.company}
        return {
            "site_name": SITE_NAME, "build": self.build_id, "generated_at": utcnow().isoformat() + "Z",
            "labels": {"domain": DOMAIN_LABELS, "specialization": SPECIALIZATION_LABELS, "governorate": GOVERNORATES,
                       "contract": CONTRACT_TYPES, "experience": EXPERIENCE_LEVELS, "workplace": WORKPLACES,
                       "posted_within": POSTED_WITHIN},
            "spec_domain": SPECIALIZATION_DOMAIN,
            "governorate_order": [slug for slug, _ in governorate_choices()],
            "companies": companies,
            "jobs": rows,
        }

    def build(self) -> dict[str, int]:
        s, now = self.s, utcnow()
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        shutil.copytree(STATIC_DIR, self.out / "static")

        active = list(s.scalars(select(Job).where(Job.is_active.is_(True))
                                .options(selectinload(Job.company))
                                .order_by(func.coalesce(Job.published_at, Job.first_seen_at).desc(), Job.id.desc())))
        recent_closed = list(s.scalars(select(Job).where(Job.is_active.is_(False), Job.closed_at >= now - KEEP_CLOSED_PAGES)
                                       .options(selectinload(Job.company))))

        self.render("index.html", "home.html", "/", stats=queries.stats(s), latest=active[:9],
                    domains=queries.domain_counts(s), companies=queries.featured_companies(s, 12),
                    governorates=queries.governorate_counts(s)[:12])
        self.render("emplois/index.html", "catalogue_static.html", "/emplois")
        (self.out / "data").mkdir()
        (self.out / "data" / "jobs.json").write_text(
            json.dumps(self.job_index(active), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

        for job in active + recent_closed:
            job = s.scalar(select(Job).where(Job.id == job.id).options(
                selectinload(Job.company), selectinload(Job.listings).selectinload(Listing.source)))
            listings = sorted(job.listings, key=lambda l: (not l.is_active, l.first_seen_at))
            self.render(f"emplois/{job.id}-{job.slug}/index.html", "job.html", f"/emplois/{job.id}-{job.slug}",
                        job=job, listings=listings, similar=queries.similar_jobs(s, job, 6))

        hiring = list(s.scalars(select(Company).where(Company.active_jobs_count > 0)
                                .order_by(Company.active_jobs_count.desc(), Company.name)))
        self.render("entreprises/index.html", "companies.html", "/entreprises", companies=hiring, total=len(hiring),
                    page=1, pages=1, q="", show_all=False)
        with_pages = list(s.scalars(select(Company).where(or_(
            Company.active_jobs_count > 0,
            Company.id.in_(select(Job.company_id).where(Job.closed_at >= now - KEEP_CLOSED_PAGES))))))
        for c in with_pages:
            self.render(f"entreprises/{c.slug}/index.html", "company.html", f"/entreprises/{c.slug}", company=c,
                        jobs=queries.company_jobs(s, c), past_jobs=queries.company_jobs(s, c, active=False, limit=20))

        self.render("sources/index.html", "sources.html", "/sources", sources=queries.sources_health(s), stats=queries.stats(s))
        self.render("404.html", "404.html", "/404")

        (self.out / ".nojekyll").write_text("")  # GitHub Pages: serve files as-is
        if self.site_url:
            urls = "".join(f"<url><loc>{xml_escape(self.site_url + p.rstrip('/') + '/')}</loc></url>"
                           for p in self.pages if p != "/404")
            (self.out / "sitemap.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                + urls + "</urlset>", encoding="utf-8")
            (self.out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {self.site_url}/sitemap.xml\n")
        return {"active_jobs": len(active), "job_pages": len(active) + len(recent_closed),
                "company_pages": len(with_pages), "files": sum(1 for _ in self.out.rglob("*") if _.is_file())}


def build_static(s: Session, out: Path, base_path: str = "", site_url: str = "") -> dict[str, int]:
    return _Builder(s, out, base_path, site_url).build()
