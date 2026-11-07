"""Keejob.com - Tunisian job board.

Keejob publishes machine-readable views intended for automated readers
(see https://www.keejob.com/llms.txt):
* ``/llms-full.txt``: one line per *active* offer -> complete snapshot;
* ``/offres-emploi/<id>/<slug>.md``: each offer in Markdown.
Only those documents (plus public company pages for the website link) are
read; ``/api/``, application and account paths are disallowed by robots.txt
and never requested.
"""
from __future__ import annotations

import re
from typing import Iterator
from urllib.parse import urljoin

import lxml.html
import markdown as md

from ..base import CollectContext, Collector, RawJob, SeenListing, register
from ..http import FetchError, NotFound, PoliteFetcher

_INDEX_LINE = re.compile(r"^- \[(?P<title>.+?)\]\((?P<url>https?://[^)\s]+?/offres-emploi/(?P<id>\d+)/[^)\s]+?\.md)\):\s*(?P<meta>.*)$")
_FIELD = re.compile(r"^- \*\*(?P<k>[^*]+)\*\*\s*:\s*(?P<v>.+)$")
_MD_LINK = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<url>[^)]+)\)")


def parse_index(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = _INDEX_LINE.match(line.strip())
        if not m:
            continue
        parts = [p.strip() for p in m.group("meta").split("·")]
        published = None
        if parts and (pm := re.search(r"(\d{4}-\d{2}-\d{2})", parts[-1])):
            published = pm.group(1)
            parts = parts[:-1]
        out.append({
            "id": m.group("id"),
            "title": m.group("title"),
            "md_url": m.group("url"),
            "company": parts[0] if parts else None,
            "location": parts[1] if len(parts) > 1 else None,
            "published": published,
        })
    return out


def parse_detail(text: str) -> dict:
    """Parse the Markdown detail document into fields + description HTML."""
    lines = text.splitlines()
    title = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("#") else None
    fields: dict[str, str] = {}
    company_url = None
    body_start = None
    for i, line in enumerate(lines):
        m = _FIELD.match(line.strip())
        if m:
            key, val = m.group("k").strip().lower(), m.group("v").strip()
            link = _MD_LINK.search(val)
            if link:
                if key.startswith("entreprise"):
                    company_url = link.group("url")
                val = link.group("text")
            fields[key] = val
        if line.strip().lower().startswith("## description"):
            body_start = i + 1
            break
    body = ""
    if body_start is not None:
        rest = lines[body_start:]
        end = next((j for j, ln in enumerate(rest) if ln.strip().lower().startswith("## postuler")), len(rest))
        body = "\n".join(rest[:end]).strip()
    apply = re.search(r"Postulez[^:]*:\s*(https?://\S+)", text)
    external_apply = re.search(r"(?:Postuler sur le site|Lien de candidature|Candidature externe)[^:]*:\s*(https?://\S+)", text, re.I)
    return {
        "title": title,
        "fields": fields,
        "company_url": company_url,
        "description_md": body,
        "apply_url": (external_apply or apply).group(1) if (external_apply or apply) else None,
        "active": "offre active" in fields.get("statut", "").lower() if "statut" in fields else None,
    }


def _md_to_html(text: str) -> str:
    # Keejob's Markdown sometimes contains stray emphasis markers ("**__**").
    text = re.sub(r"\*\*\s*_*\s*\*\*|\*\*__\*\*", "", text)
    return md.markdown(text, extensions=["sane_lists"])


@register("keejob")
class KeejobCollector(Collector):
    complete_snapshot = True

    def __init__(self, key, name, options=None):
        super().__init__(key, name, options)
        self.base = self.options.get("base_url", "https://www.keejob.com").rstrip("/")
        self.fetch_company_pages = bool(self.options.get("fetch_company_pages", True))

    def collect(self, ctx: CollectContext) -> Iterator[RawJob | SeenListing]:
        index = parse_index(ctx.fetcher.get_text(f"{self.base}/llms-full.txt"))
        if not index:
            raise FetchError("Keejob index is empty or its format changed")
        company_cache: dict[str, str | None] = {}
        for item in index:
            if item["id"] in ctx.known_ids:
                yield SeenListing(item["id"])
                continue
            if not ctx.can_fetch_detail():
                continue  # picked up next run; not reported as seen (it isn't stored yet)
            try:
                ctx.note_detail()
                detail = parse_detail(ctx.fetcher.get_text(item["md_url"]))
            except NotFound:
                continue
            except FetchError as exc:
                ctx.item_error(f"keejob {item['id']}: {exc}")
                continue
            f = detail["fields"]
            html_url = item["md_url"][:-3] + "/"
            website = None
            if self.fetch_company_pages and detail["company_url"]:
                cu = detail["company_url"]
                if cu not in company_cache:
                    company_cache[cu] = None if cu in ctx.known_company_profiles else self._company_website(ctx.fetcher, cu)
                website = company_cache[cu]
            yield RawJob(
                external_id=item["id"],
                url=html_url,
                apply_url=detail["apply_url"] or html_url,
                title=detail["title"] or item["title"],
                company_name=f.get("entreprise") or item["company"],
                company_source_url=detail["company_url"],
                company_website=website,
                description_html=_md_to_html(detail["description_md"]) if detail["description_md"] else None,
                location_raw=f.get("lieu") or item["location"],
                # Not forced to TN: Keejob also lists offers abroad; the
                # location text ("Tunis, Tunisie" / "Nouadhibou, Mauritanie") decides.
                country=None,
                workplace_raw=f.get("mode de travail"),
                contract_raw=[c.strip() for c in re.split(r"[,/]", f.get("type de contrat", "")) if c.strip()],
                experience_raw=f.get("expérience") or f.get("experience"),
                education_raw=f.get("niveau d'études") or f.get("niveau d'etudes"),
                categories_raw=[f["métiers"]] if f.get("métiers") else [],
                skills_raw=[],
                published_at=f.get("date de publication") or item["published"],
                expires_at=f.get("date d'expiration"),
                language="fr",
                extra={"langues": f.get("langues")} if f.get("langues") else {},
            )

    def _company_website(self, fetcher: PoliteFetcher, url: str) -> str | None:
        try:
            doc = lxml.html.fromstring(fetcher.get_text(url))
        except FetchError:
            return None
        for a in doc.xpath('//a[starts-with(@href,"http")]'):
            href = a.get("href", "")
            host = re.sub(r"^https?://", "", href).split("/")[0].lower()
            if not host or "keejob" in host or any(s in host for s in (
                    "facebook.", "linkedin.", "twitter.", "x.com", "instagram.", "youtube.", "google.", "apple.com", "tiktok.")):
                continue
            # The company's site is rendered with its URL as link text.
            if a.text_content().strip().rstrip("/") in (href.rstrip("/"), host):
                return urljoin(url, href)
        return None

    def check_alive(self, fetcher: PoliteFetcher, url: str) -> bool | None:
        try:
            text = fetcher.get_text(url.rstrip("/") + ".md")
        except NotFound:
            return False
        except FetchError:
            return None
        return parse_detail(text)["active"]
