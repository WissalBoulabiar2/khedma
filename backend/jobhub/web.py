"""Server-rendered pages (Jinja2).  SEO-friendly, fast, work without JS."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.datastructures import QueryParams

from . import queries
from .api import DB, job_query_params
from .models import Company, Job, Listing
from .normalize.locations import GOVERNORATES, governorate_choices
from .normalize.taxonomy import (CONTRACT_TYPES, DOMAIN_LABELS, DOMAINS, EXPERIENCE_LEVELS, SPECIALIZATION_LABELS,
                                 WORKPLACES)
from .normalize.text import utcnow
from .search import POSTED_WITHIN, SORTS, JobQuery, search_jobs

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter(include_in_schema=False)

SITE_NAME = os.environ.get("JOBHUB_SITE_NAME", "Khedma")

_FR_MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
_FR_MONTHS_LONG = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
                   "novembre", "décembre"]


def frdate(dt: datetime | None, long: bool = False) -> str:
    if not dt:
        return ""
    months = _FR_MONTHS_LONG if long else _FR_MONTHS
    return f"{dt.day} {months[dt.month - 1]} {dt.year}"


def reldate(dt: datetime | None) -> str:
    if not dt:
        return ""
    days = (utcnow().date() - dt.date()).days
    if days <= 0:
        return "aujourd'hui"
    if days == 1:
        return "hier"
    if days < 7:
        return f"il y a {days} jours"
    if days < 31:
        w = days // 7
        return f"il y a {w} semaine{'s' if w > 1 else ''}"
    return frdate(dt)


def fmt_int(n: int | None) -> str:
    return f"{n or 0:,}".replace(",", " ")


def initials(name: str | None) -> str:
    words = [w for w in (name or "?").replace("-", " ").split() if w[:1].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def url_with(params: QueryParams, path: str = "/emplois", **changes: Any) -> str:
    """Current query string with some keys replaced/removed (None removes).

    ``toggle_<key>=value`` adds/removes one value of a multi-valued key.
    """
    items = [(k, v) for k, v in params.multi_items() if v != ""]
    for key, val in changes.items():
        if key.startswith("toggle_"):
            k = key[len("toggle_"):]
            if (k, val) in items:
                items = [(a, b) for a, b in items if (a, b) != (k, val)]
            else:
                items.append((k, val))
        else:
            items = [(a, b) for a, b in items if a != key]
            if val is not None:
                items.append((key, str(val)))
    if not any(k == "page" for k in changes):
        items = [(a, b) for a, b in items if a != "page"]
    qs = urlencode(items)
    return f"{path}?{qs}" if qs else path


templates.env.filters.update(frdate=frdate, reldate=reldate, fmt_int=fmt_int, initials=initials)
templates.env.globals.update(
    SITE_NAME=SITE_NAME, DOMAIN_LABELS=DOMAIN_LABELS, SPECIALIZATION_LABELS=SPECIALIZATION_LABELS,
    GOVERNORATES=GOVERNORATES, CONTRACT_TYPES=CONTRACT_TYPES, EXPERIENCE_LEVELS=EXPERIENCE_LEVELS,
    WORKPLACES=WORKPLACES, POSTED_WITHIN=POSTED_WITHIN, SORTS=SORTS, url_with=url_with,
    governorate_choices=governorate_choices(), DOMAINS=DOMAINS,
)


def render(request: Request, name: str, status_code: int = 200, **ctx: Any) -> HTMLResponse:
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


@router.get("/", response_class=HTMLResponse)
def home(request: Request, s: DB):
    return render(request, "home.html", stats=queries.stats(s), latest=queries.latest_jobs(s, 9),
                  domains=queries.domain_counts(s), companies=queries.featured_companies(s, 12),
                  governorates=queries.governorate_counts(s)[:12])


@router.get("/emplois", response_class=HTMLResponse)
def catalogue(request: Request, s: DB, jq: JobQuery = Depends(job_query_params)):
    res = search_jobs(s, jq)
    q = jq.cleaned()
    chips = []
    p = request.query_params
    if q.q:
        chips.append((f"« {q.q} »", url_with(p, q=None)))
    if q.domain:
        chips.append((DOMAIN_LABELS[q.domain], url_with(p, domain=None, specialization=None)))
    if q.specialization:
        chips.append((SPECIALIZATION_LABELS[q.specialization], url_with(p, specialization=None)))
    if q.governorate:
        chips.append((GOVERNORATES[q.governorate], url_with(p, governorate=None, city=None)))
    if q.city:
        chips.append((q.city, url_with(p, city=None)))
    for c in q.contract:
        chips.append((CONTRACT_TYPES[c], url_with(p, toggle_contract=c)))
    for e in q.experience:
        chips.append((EXPERIENCE_LEVELS[e].split(" (")[0], url_with(p, toggle_experience=e)))
    for w in q.workplace:
        chips.append((WORKPLACES[w], url_with(p, toggle_workplace=w)))
    if q.posted_within:
        chips.append((POSTED_WITHIN[q.posted_within], url_with(p, posted_within=None)))
    if q.company:
        company = s.scalar(select(Company).where(Company.slug == q.company))
        chips.append((company.name if company else q.company, url_with(p, company=None)))
    return render(request, "catalogue.html", res=res, q=q, chips=chips)


@router.get("/emplois/{job_ref}", response_class=HTMLResponse)
def job_page(request: Request, s: DB, job_ref: str):
    job_id = job_ref.split("-", 1)[0]
    if not job_id.isdigit():
        return not_found(request)
    job = s.scalar(select(Job).where(Job.id == int(job_id))
                   .options(selectinload(Job.company), selectinload(Job.listings).selectinload(Listing.source)))
    if job is None:
        return not_found(request)
    canonical = f"{job.id}-{job.slug}"
    if job_ref != canonical:
        return RedirectResponse(f"/emplois/{canonical}", status_code=301)
    listings = sorted(job.listings, key=lambda l: (not l.is_active, l.first_seen_at))
    return render(request, "job.html", job=job, listings=listings, similar=queries.similar_jobs(s, job, 6))


@router.get("/entreprises", response_class=HTMLResponse)
def companies(request: Request, s: DB, q: str | None = None, tous: int = 0, page: int = 1):
    rows, total = queries.list_companies(s, (q or "").strip() or None, hiring_only=not tous, page=page, per_page=36)
    pages = max(1, -(-total // 36))
    return render(request, "companies.html", companies=rows, total=total, page=max(1, page), pages=pages, q=q or "",
                  show_all=bool(tous))


@router.get("/entreprises/{slug}", response_class=HTMLResponse)
def company_page(request: Request, s: DB, slug: str):
    c = s.scalar(select(Company).where(Company.slug == slug))
    if c is None:
        return not_found(request)
    return render(request, "company.html", company=c, jobs=queries.company_jobs(s, c),
                  past_jobs=queries.company_jobs(s, c, active=False, limit=20))


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, s: DB):
    return render(request, "sources.html", sources=queries.sources_health(s), stats=queries.stats(s))


def not_found(request: Request) -> HTMLResponse:
    return render(request, "404.html", status_code=404)
