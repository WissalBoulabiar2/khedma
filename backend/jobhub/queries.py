"""Read-side queries shared by the JSON API and the HTML pages."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .models import CollectionRun, Company, Job, Listing, Source
from .normalize.locations import GOVERNORATES
from .normalize.taxonomy import DOMAIN_LABELS, DOMAINS
from .normalize.text import fold, utcnow


def stats(s: Session) -> dict[str, Any]:
    now = utcnow()
    active = Job.is_active.is_(True)
    first_seen = func.coalesce(Job.published_at, Job.first_seen_at)
    return {
        "active_jobs": s.scalar(select(func.count()).select_from(Job).where(active)) or 0,
        "total_jobs": s.scalar(select(func.count()).select_from(Job)) or 0,
        "companies": s.scalar(select(func.count()).select_from(Company)) or 0,
        "hiring_companies": s.scalar(select(func.count()).select_from(Company).where(Company.active_jobs_count > 0)) or 0,
        "new_24h": s.scalar(select(func.count()).select_from(Job).where(active, Job.first_seen_at >= now - timedelta(days=1))) or 0,
        "new_7d": s.scalar(select(func.count()).select_from(Job).where(active, first_seen >= now - timedelta(days=7))) or 0,
        "active_sources": s.scalar(select(func.count()).select_from(Source).where(Source.enabled.is_(True))) or 0,
        "last_update": s.scalar(select(func.max(Source.last_success_at))),
    }


def domain_counts(s: Session) -> list[dict[str, Any]]:
    counts = dict(s.execute(select(Job.domain, func.count()).where(Job.is_active.is_(True)).group_by(Job.domain)).all())
    return [{"value": d, "label": DOMAIN_LABELS[d], "count": counts.get(d, 0),
             "specializations": [{"value": k, "label": v[0]} for k, v in DOMAINS[d][1].items()]}
            for d in DOMAINS if counts.get(d)]


def governorate_counts(s: Session) -> list[dict[str, Any]]:
    counts = s.execute(select(Job.governorate, func.count()).where(Job.is_active.is_(True), Job.governorate.is_not(None))
                       .group_by(Job.governorate).order_by(func.count().desc())).all()
    return [{"value": g, "label": GOVERNORATES.get(g, g), "count": n} for g, n in counts]


def latest_jobs(s: Session, limit: int = 12) -> list[Job]:
    return list(s.scalars(select(Job).where(Job.is_active.is_(True)).options(selectinload(Job.company))
                          .order_by(func.coalesce(Job.published_at, Job.first_seen_at).desc(), Job.id.desc()).limit(limit)))


def featured_companies(s: Session, limit: int = 12) -> list[Company]:
    return list(s.scalars(select(Company).where(Company.active_jobs_count > 0)
                          .order_by(Company.active_jobs_count.desc(), Company.name).limit(limit)))


def list_companies(s: Session, q: str | None, hiring_only: bool, page: int, per_page: int = 30) -> tuple[list[Company], int]:
    stmt = select(Company)
    if hiring_only:
        stmt = stmt.where(Company.active_jobs_count > 0)
    if q:
        stmt = stmt.where(Company.key.like(f"%{fold(q)[:100]}%") | Company.name.ilike(f"%{q[:100]}%"))
    total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(1, page)
    rows = s.scalars(stmt.order_by(Company.active_jobs_count.desc(), Company.name)
                     .offset((page - 1) * per_page).limit(per_page)).all()
    return list(rows), total


def company_jobs(s: Session, company: Company, active: bool = True, limit: int = 200) -> list[Job]:
    return list(s.scalars(select(Job).where(Job.company_id == company.id, Job.is_active.is_(active))
                          .options(selectinload(Job.company))
                          .order_by(func.coalesce(Job.published_at, Job.first_seen_at).desc()).limit(limit)))


def similar_jobs(s: Session, job: Job, limit: int = 5) -> list[Job]:
    stmt = (select(Job).where(Job.is_active.is_(True), Job.id != job.id, Job.specialization == job.specialization)
            .options(selectinload(Job.company)))
    if job.governorate:
        stmt = stmt.order_by((Job.governorate == job.governorate).desc())
    stmt = stmt.order_by(func.coalesce(Job.published_at, Job.first_seen_at).desc()).limit(limit)
    return list(s.scalars(stmt))


def sources_health(s: Session) -> list[dict[str, Any]]:
    active_counts = dict(s.execute(select(Listing.source_key, func.count()).where(Listing.is_active.is_(True))
                                   .group_by(Listing.source_key)).all())
    out = []
    for src in s.scalars(select(Source).order_by(Source.enabled.desc(), Source.name)):
        last_run = s.scalar(select(CollectionRun).where(CollectionRun.source_key == src.key, CollectionRun.status != "skipped")
                            .order_by(CollectionRun.started_at.desc()).limit(1))
        out.append({
            "key": src.key, "name": src.name, "type": src.type, "homepage": src.homepage, "enabled": src.enabled,
            "disabled_reason": src.disabled_reason, "active_offers": active_counts.get(src.key, 0),
            "last_status": src.last_status, "last_run_at": src.last_run_at, "last_success_at": src.last_success_at,
            "last_error": src.last_error, "consecutive_failures": src.consecutive_failures,
            "last_run": None if last_run is None else {
                "status": last_run.status, "seen": last_run.seen, "created": last_run.created, "merged": last_run.merged,
                "closed": last_run.closed, "rejected": last_run.rejected, "item_errors": last_run.item_errors,
                "requests": last_run.requests, "started_at": last_run.started_at, "finished_at": last_run.finished_at},
        })
    return out
