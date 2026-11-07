"""JSON API (/api)."""
from __future__ import annotations

import threading
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import queries
from .config import settings
from .db import SessionLocal
from .models import Company, Job, Listing
from .normalize.locations import governorate_choices
from .normalize.taxonomy import CONTRACT_TYPES, DOMAINS, EXPERIENCE_LEVELS, WORKPLACES
from .search import POSTED_WITHIN, JobQuery, search_jobs
from .serializers import company_brief, job_detail, job_summary

router = APIRouter(prefix="/api", tags=["api"])


def get_session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


DB = Annotated[Session, Depends(get_session)]


def job_query_params(
    q: str | None = None, domain: str | None = None, specialization: str | None = None,
    governorate: str | None = None, city: str | None = None,
    contract: Annotated[list[str], Query()] = [],  # noqa: B006 - FastAPI copies defaults
    experience: Annotated[list[str], Query()] = [],  # noqa: B006
    workplace: Annotated[list[str], Query()] = [],  # noqa: B006
    posted_within: str | None = None, company: str | None = None, sort: str = "recent",
    page: int = 1, per_page: int = 20,
) -> JobQuery:
    return JobQuery(q=q, domain=domain, specialization=specialization, governorate=governorate, city=city,
                    contract=contract, experience=experience, workplace=workplace, posted_within=posted_within,
                    company=company, sort=sort, page=page, per_page=per_page)


@router.get("/health")
def health(s: DB) -> dict[str, Any]:
    st = queries.stats(s)
    return {"status": "ok", "active_jobs": st["active_jobs"], "last_update": st["last_update"]}


@router.get("/jobs")
def list_jobs(s: DB, jq: Annotated[JobQuery, Depends(job_query_params)], facets: bool = True) -> dict[str, Any]:
    res = search_jobs(s, jq, with_facets=facets)
    return {"total": res.total, "page": res.page, "per_page": res.per_page, "pages": res.pages,
            "results": [job_summary(j) for j in res.jobs], "facets": res.facets}


@router.get("/jobs/{job_id}")
def get_job(s: DB, job_id: int) -> dict[str, Any]:
    job = s.scalar(select(Job).where(Job.id == job_id)
                   .options(selectinload(Job.company), selectinload(Job.listings).selectinload(Listing.source)))
    if job is None:
        raise HTTPException(404, "job not found")
    return job_detail(job)


@router.get("/companies")
def get_companies(s: DB, q: str | None = None, hiring: bool = True, page: int = 1, per_page: int = 30) -> dict[str, Any]:
    per_page = max(1, min(per_page, 100))
    rows, total = queries.list_companies(s, q, hiring, page, per_page)
    return {"total": total, "page": page, "per_page": per_page, "results": [company_brief(c) for c in rows]}


@router.get("/companies/{slug}")
def get_company(s: DB, slug: str) -> dict[str, Any]:
    c = s.scalar(select(Company).where(Company.slug == slug))
    if c is None:
        raise HTTPException(404, "company not found")
    d = company_brief(c) | {"description": c.description, "source_profile_url": c.source_profile_url}
    d["jobs"] = [job_summary(j) for j in queries.company_jobs(s, c)]
    return d


@router.get("/stats")
def get_stats(s: DB) -> dict[str, Any]:
    return queries.stats(s) | {"domains": queries.domain_counts(s), "governorates": queries.governorate_counts(s)}


@router.get("/taxonomy")
def taxonomy() -> dict[str, Any]:
    return {
        "domains": [{"value": d, "label": label, "specializations": [{"value": k, "label": v[0]} for k, v in specs.items()]}
                    for d, (label, specs) in DOMAINS.items()],
        "governorates": [{"value": k, "label": v} for k, v in governorate_choices()],
        "contracts": [{"value": k, "label": v} for k, v in CONTRACT_TYPES.items()],
        "experience": [{"value": k, "label": v} for k, v in EXPERIENCE_LEVELS.items()],
        "workplaces": [{"value": k, "label": v} for k, v in WORKPLACES.items()],
        "posted_within": [{"value": k, "label": v} for k, v in POSTED_WITHIN.items()],
    }


@router.get("/sources")
def get_sources(s: DB) -> list[dict[str, Any]]:
    return queries.sources_health(s)


_collect_thread: threading.Thread | None = None


@router.post("/admin/collect", status_code=202)
def trigger_collect(x_admin_token: Annotated[str | None, Header()] = None, source: str | None = None) -> dict[str, Any]:
    """Start a collection in the background. Requires JOBHUB_ADMIN_TOKEN."""
    global _collect_thread
    if not settings.admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(403, "admin token required")
    if _collect_thread is not None and _collect_thread.is_alive():
        raise HTTPException(409, "a collection is already running")
    from .pipeline import run_all
    _collect_thread = threading.Thread(target=run_all, kwargs={"only": [source] if source else None}, daemon=True)
    _collect_thread.start()
    return {"status": "started", "source": source}
