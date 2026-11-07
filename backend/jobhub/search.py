"""Job search and faceted filtering."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.orm import Session, selectinload

from .models import Company, Job
from .normalize.locations import GOVERNORATES
from .normalize.taxonomy import CONTRACT_TYPES, DOMAIN_LABELS, EXPERIENCE_LEVELS, SPECIALIZATION_LABELS, WORKPLACES
from .normalize.text import fold, utcnow

POSTED_WITHIN = {"1": "Dernières 24 h", "3": "3 derniers jours", "7": "7 derniers jours", "30": "30 derniers jours"}
SORTS = {"recent": "Plus récentes", "relevance": "Pertinence"}
MAX_PER_PAGE = 100


@dataclass
class JobQuery:
    q: str | None = None
    domain: str | None = None
    specialization: str | None = None
    governorate: str | None = None
    city: str | None = None
    contract: list[str] = field(default_factory=list)
    experience: list[str] = field(default_factory=list)
    workplace: list[str] = field(default_factory=list)
    posted_within: str | None = None
    company: str | None = None  # company slug
    include_closed: bool = False
    sort: str = "recent"
    page: int = 1
    per_page: int = 20

    def cleaned(self) -> "JobQuery":
        """Drop unknown values so a crafted URL can't produce odd SQL or errors."""
        return replace(
            self,
            q=(self.q or "").strip()[:200] or None,
            domain=self.domain if self.domain in DOMAIN_LABELS else None,
            specialization=self.specialization if self.specialization in SPECIALIZATION_LABELS else None,
            governorate=self.governorate if self.governorate in GOVERNORATES else None,
            city=(self.city or "").strip()[:80] or None,
            contract=[c for c in self.contract if c in CONTRACT_TYPES],
            experience=[e for e in self.experience if e in EXPERIENCE_LEVELS],
            workplace=[w for w in self.workplace if w in WORKPLACES],
            posted_within=self.posted_within if self.posted_within in POSTED_WITHIN else None,
            company=(self.company or "").strip()[:200] or None,
            sort=self.sort if self.sort in SORTS else "recent",
            page=max(1, min(self.page, 10_000)),
            per_page=max(1, min(self.per_page, MAX_PER_PAGE)),
        )


@dataclass
class SearchResult:
    jobs: list[Job]
    total: int
    page: int
    per_page: int
    facets: dict[str, list[dict[str, Any]]]

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.per_page))


def _fts_query(q: str) -> str | None:
    # Each word becomes a quoted prefix term; all terms must match (AND).
    words = re.findall(r"[\w+#.]+", fold(q))
    words = [w.strip(".") for w in words if len(w.strip(".")) >= 1][:12]
    if not words:
        return None
    return " ".join(f'"{w}"*' for w in words)


def _uses_fts(session: Session) -> bool:
    return session.get_bind().dialect.name == "sqlite"


def _apply_filters(session: Session, stmt: Select, q: JobQuery, skip: str | None = None) -> Select:
    if not q.include_closed:
        stmt = stmt.where(Job.is_active.is_(True))
    if q.q:
        if _uses_fts(session):
            match = _fts_query(q.q)
            if match:
                stmt = stmt.where(Job.id.in_(select(text("rowid")).select_from(text("jobs_fts"))
                                             .where(text("jobs_fts MATCH :m")))).params(m=match)
        else:
            like = f"%{q.q}%"
            stmt = stmt.where(or_(Job.title.ilike(like), Job.description_text.ilike(like)))
    if q.domain and skip != "domain":
        stmt = stmt.where(Job.domain == q.domain)
    if q.specialization and skip != "specialization":
        stmt = stmt.where(Job.specialization == q.specialization)
    if q.governorate and skip != "governorate":
        stmt = stmt.where(Job.governorate == q.governorate)
    if q.city and skip != "city":
        stmt = stmt.where(Job.city == q.city)
    if q.contract and skip != "contract":
        stmt = stmt.where(or_(*[Job.contracts.like(f"%|{c}|%") for c in q.contract]))
    if q.experience and skip != "experience":
        stmt = stmt.where(Job.experience_level.in_(q.experience))
    if q.workplace and skip != "workplace":
        stmt = stmt.where(Job.workplace.in_(q.workplace))
    if q.posted_within and skip != "posted_within":
        since = utcnow() - timedelta(days=int(q.posted_within))
        stmt = stmt.where(func.coalesce(Job.published_at, Job.first_seen_at) >= since)
    if q.company:
        stmt = stmt.where(Job.company_id.in_(select(Company.id).where(Company.slug == q.company)))
    return stmt


def _facet(session: Session, q: JobQuery, column, name: str, labels: dict[str, str]) -> list[dict[str, Any]]:
    stmt = _apply_filters(session, select(column, func.count()).select_from(Job), q, skip=name).group_by(column)
    counts = {k: v for k, v in session.execute(stmt).all() if k}
    return [{"value": k, "label": labels.get(k, k), "count": counts[k]}
            for k in sorted(counts, key=lambda k: (-counts[k], labels.get(k, k)))]


def facets(session: Session, q: JobQuery) -> dict[str, list[dict[str, Any]]]:
    out = {
        "domain": _facet(session, q, Job.domain, "domain", DOMAIN_LABELS),
        "governorate": _facet(session, q, Job.governorate, "governorate", GOVERNORATES),
        "experience": _facet(session, q, Job.experience_level, "experience", EXPERIENCE_LEVELS),
        "workplace": _facet(session, q, Job.workplace, "workplace", WORKPLACES),
    }
    # The domain filter stays applied, so specializations are those of the chosen domain.
    out["specialization"] = _facet(session, q, Job.specialization, "specialization", SPECIALIZATION_LABELS)
    if q.governorate:
        out["city"] = _facet(session, q, Job.city, "city", {})
    contract_counts = []
    base = _apply_filters(session, select(func.count()).select_from(Job), q, skip="contract")
    for slug, label in CONTRACT_TYPES.items():
        n = session.scalar(base.where(Job.contracts.like(f"%|{slug}|%"))) or 0
        if n:
            contract_counts.append({"value": slug, "label": label, "count": n})
    out["contract"] = sorted(contract_counts, key=lambda d: -d["count"])
    return out


def search_jobs(session: Session, query: JobQuery, *, with_facets: bool = True) -> SearchResult:
    q = query.cleaned()
    stmt = _apply_filters(session, select(Job), q)
    total = session.scalar(_apply_filters(session, select(func.count()).select_from(Job), q)) or 0
    date_order = func.coalesce(Job.published_at, Job.first_seen_at).desc()
    if q.sort == "relevance" and q.q and _uses_fts(session) and (match := _fts_query(q.q)):
        rank = (select(text("bm25(jobs_fts, 10.0, 6.0, 4.0, 3.0, 1.0)")).select_from(text("jobs_fts"))
                .where(text("jobs_fts.rowid = jobs.id")).where(text("jobs_fts MATCH :m2"))
                .scalar_subquery())
        stmt = stmt.order_by(rank, date_order).params(m2=match)
    else:
        stmt = stmt.order_by(date_order, Job.id.desc())
    stmt = stmt.options(selectinload(Job.company)).offset((q.page - 1) * q.per_page).limit(q.per_page)
    jobs = list(session.scalars(stmt).unique().all())
    return SearchResult(jobs=jobs, total=total, page=q.page, per_page=q.per_page,
                        facets=facets(session, q) if with_facets else {})
