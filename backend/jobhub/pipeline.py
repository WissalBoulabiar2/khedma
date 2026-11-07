"""Collection pipeline: collect -> normalize -> deduplicate -> store -> lifecycle.

Failure model
* An item that fails to fetch/parse is counted (``item_errors``) and skipped.
* A source that fails (network down, format changed, blocked) gets a failed
  run; nothing is closed for it, and it backs off exponentially.
* Offers are only closed from a *successful complete* snapshot, and only
  when the snapshot is not suspiciously small (protection against a source
  returning a truncated or empty page).
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from .collectors.base import CollectContext, Collector, RawJob, SeenListing
from .collectors.http import AccessBlocked, PoliteFetcher, RobotsDisallowed
from .config import BACKEND_DIR, settings
from .db import SessionLocal, fts_upsert, session_scope
from .models import CollectionRun, Company, Job, Listing, Source
from .normalize import NormalizedJob, Rejected, normalize, title_key
from .normalize.locations import GOVERNORATES
from .normalize.text import sanitize_html, slugify, utcnow
from .sources import SourceConfig, load_sources, sync_sources

log = logging.getLogger(__name__)

COMMIT_EVERY = 25
# A complete snapshot smaller than this share of the previously active
# listings is treated as suspicious: nothing gets closed.
MIN_SNAPSHOT_RATIO = 0.5
LIVENESS_CHECKS_PER_RUN = 40
RECHECK_AFTER = timedelta(days=3)
UNSEEN_GRACE = timedelta(days=2)
MAX_AGE_WITHOUT_EXPIRY = timedelta(days=90)
DUPLICATE_WINDOW = timedelta(days=30)
TITLE_SIMILARITY = 0.88


# ----------------------------------------------------------------- companies --

def get_or_create_company(session: Session, n: NormalizedJob) -> Company | None:
    if not n.company_key or not n.company_name:
        return None
    company = session.scalar(select(Company).where(Company.key == n.company_key))
    if company is None:
        base = slugify(n.company_name, 100)
        slug, i = base, 2
        while session.scalar(select(Company.id).where(Company.slug == slug)) is not None:
            slug, i = f"{base}-{i}", i + 1
        company = Company(key=n.company_key, name=n.company_name, slug=slug)
        session.add(company)
    # Fill gaps only; never overwrite curated data with a weaker source.
    if n.company_website and not company.website:
        company.website = n.company_website
    if n.company_logo and not company.logo_url:
        company.logo_url = n.company_logo
    if n.company_description and not company.description:
        company.description = sanitize_html(n.company_description)
    if n.company_source_url and not company.source_profile_url:
        company.source_profile_url = n.company_source_url
    session.flush()
    return company


# ------------------------------------------------------------------- dedupe --

def _similar(a: str, b: str) -> float:
    ka, kb = title_key(a), title_key(b)
    # "Niveau 1" vs "Niveau 2", "Lot 3" vs "Lot 4": different offers.
    if set(re.findall(r"\d+", ka)) != set(re.findall(r"\d+", kb)):
        return 0.0
    ta, tb = " ".join(sorted(ka.split())), " ".join(sorted(kb.split()))
    return SequenceMatcher(None, ta, tb).ratio()


def find_duplicate(session: Session, n: NormalizedJob, company: Company | None, now: datetime,
                   source_key: str | None = None) -> Job | None:
    """Same offer already stored (from another source, or re-posted)?

    1. exact fingerprint (company key + normalized title + governorate);
    2. same company, very similar title, compatible governorate, dates close,
       and not already listed (active) by the same source - one board
       publishing two similar ads means two openings.
    Closed jobs only match if they closed recently (a re-post months later is
    a new offer).
    """
    recent = now - DUPLICATE_WINDOW
    alive = or_(Job.is_active.is_(True), Job.closed_at >= recent)
    job = session.scalar(select(Job).where(Job.fingerprint == n.fingerprint, alive).order_by(Job.id.desc()).limit(1))
    if job is not None:
        return job
    if company is None:
        return None
    ref = n.published_at or now
    candidates = session.scalars(select(Job).where(Job.company_id == company.id, alive)).all()
    for cand in candidates:
        if n.governorate and cand.governorate and n.governorate != cand.governorate:
            continue
        cdate = cand.published_at or cand.first_seen_at
        if abs((cdate - ref).days) > DUPLICATE_WINDOW.days:
            continue
        if source_key and any(l.source_key == source_key and l.is_active for l in cand.listings):
            continue
        if _similar(cand.title, n.title) >= TITLE_SIMILARITY:
            return cand
    return None


# ---------------------------------------------------------------- upserting --

def _apply(job: Job, n: NormalizedJob, company: Company | None) -> None:
    job.title = n.title
    job.slug = slugify(f"{n.title} {company.name if company else ''}", 110)
    job.company_id = company.id if company else None
    job.description_html = n.description_html
    job.description_text = n.description_text
    job.skills = n.skills
    job.domain, job.specialization = n.domain, n.specialization
    job.governorate, job.city, job.location_label = n.governorate, n.city, n.location_label
    job.workplace = n.workplace
    job.contracts = ("|" + "|".join(n.contracts) + "|") if n.contracts else ""
    job.experience_level, job.experience_years = n.experience_level, n.experience_years
    job.education = n.education
    job.published_at = n.published_at or job.published_at
    job.expires_at = n.expires_at
    job.apply_url, job.source_url = n.apply_url, n.url
    job.fingerprint = n.fingerprint


def _enrich(job: Job, n: NormalizedJob) -> None:
    """A second source for the same offer may know things the first did not."""
    if not job.governorate and n.governorate:
        job.governorate, job.city, job.location_label = n.governorate, n.city, n.location_label
    if not job.workplace and n.workplace:
        job.workplace = n.workplace
    if not job.contracts and n.contracts:
        job.contracts = "|" + "|".join(n.contracts) + "|"
    if not job.experience_level and n.experience_level:
        job.experience_level, job.experience_years = n.experience_level, n.experience_years
    if len(n.description_text) > len(job.description_text or "") * 1.5:
        job.description_html, job.description_text = n.description_html, n.description_text
    for s in n.skills:
        if s not in (job.skills or []):
            job.skills = [*(job.skills or []), s][:25]
    if n.published_at and (not job.published_at or n.published_at < job.published_at):
        job.published_at = n.published_at


def index_job(session: Session, job: Job) -> None:
    company = session.get(Company, job.company_id) if job.company_id else None
    loc = " ".join(x for x in [job.city, GOVERNORATES.get(job.governorate or ""), job.location_label] if x)
    fts_upsert(session, job.id, title=job.title, company=company.name if company else "",
               skills=" ".join(job.skills or []), location=loc, description=job.description_text or "")


@dataclass
class UpsertResult:
    action: str  # created | updated | unchanged | merged


def upsert(session: Session, source_key: str, raw: RawJob, n: NormalizedJob, now: datetime) -> UpsertResult:
    listing = session.scalar(select(Listing).where(Listing.source_key == source_key, Listing.external_id == raw.external_id))
    company = get_or_create_company(session, n)
    if listing is not None:
        listing.last_seen_at = now
        reopened = not listing.is_active
        listing.is_active, listing.closed_at, listing.close_reason = True, None, None
        job = listing.job
        if listing.content_hash == n.content_hash and not reopened:
            return UpsertResult("unchanged")
        listing.content_hash, listing.url, listing.apply_url = n.content_hash, n.url, n.apply_url
        if job.source_url == listing.url or len(job.listings) == 1:
            _apply(job, n, company)
        else:
            _enrich(job, n)
        job.last_seen_at = now
        session.flush()
        index_job(session, job)
        return UpsertResult("updated")

    job = find_duplicate(session, n, company, now, source_key)
    action = "merged"
    if job is None:
        job = Job(first_seen_at=now, last_seen_at=now, is_active=True)
        _apply(job, n, company)
        session.add(job)
        action = "created"
    else:
        _enrich(job, n)
        job.last_seen_at = now
    session.flush()
    session.add(Listing(
        source_key=source_key, external_id=raw.external_id, job_id=job.id, url=n.url, apply_url=n.apply_url,
        content_hash=n.content_hash, first_seen_at=now, last_seen_at=now, is_active=True,
        raw={"title": raw.title, "company": raw.company_name, "location": raw.location_raw,
             "contract": raw.contract_raw, "experience": raw.experience_raw, "categories": raw.categories_raw},
    ))
    session.flush()
    index_job(session, job)
    return UpsertResult(action)


def mark_seen(session: Session, source_key: str, external_id: str, now: datetime) -> bool:
    listing = session.scalar(select(Listing).where(Listing.source_key == source_key, Listing.external_id == external_id))
    if listing is None:
        return False
    listing.last_seen_at = now
    if not listing.is_active:
        listing.is_active, listing.closed_at, listing.close_reason = True, None, None
    return True


def close_listing(listing: Listing, reason: str, now: datetime) -> None:
    listing.is_active = False
    listing.closed_at = now
    listing.close_reason = reason


# ---------------------------------------------------------------- lifecycle --

def check_unseen(session: Session, source: SourceConfig, collector: Collector, fetcher: PoliteFetcher,
                 run_started: datetime, now: datetime) -> int:
    """For feed-style sources: verify offers that dropped out of the feed."""
    closed = 0
    q = (select(Listing).where(Listing.source_key == source.key, Listing.is_active.is_(True),
                               Listing.last_seen_at < run_started - UNSEEN_GRACE,
                               or_(Listing.last_checked_at.is_(None), Listing.last_checked_at < now - RECHECK_AFTER))
         .order_by(Listing.last_checked_at.is_not(None), Listing.last_checked_at).limit(LIVENESS_CHECKS_PER_RUN))
    for listing in session.scalars(q).all():
        try:
            alive = collector.check_alive(fetcher, listing.url)
        except (AccessBlocked, RobotsDisallowed):
            break
        listing.last_checked_at = now
        if alive is False:
            close_listing(listing, "gone_from_source", now)
            closed += 1
        elif alive is None and listing.last_seen_at < now - timedelta(days=settings.stale_after_days):
            close_listing(listing, "stale", now)
            closed += 1
    return closed


def refresh_job_states(session: Session, now: datetime, snapshot_sources: frozenset[str] = frozenset()) -> dict[str, int]:
    """Job is active iff it has an active listing and is not expired/too old.

    ``snapshot_sources``: sources whose listings are confirmed on every run;
    an offer they still list is not closed for being old (some companies
    keep a role open for months).
    """
    # Expired or too-old offers close all their listings.
    for job in session.scalars(select(Job).where(Job.is_active.is_(True), or_(
            Job.expires_at < now,
            (Job.expires_at.is_(None)) & (func.coalesce(Job.published_at, Job.first_seen_at) < now - MAX_AGE_WITHOUT_EXPIRY)))).all():
        expired = job.expires_at is not None and job.expires_at < now
        if not expired and any(l.is_active and l.source_key in snapshot_sources for l in job.listings):
            continue
        for listing in job.listings:
            if listing.is_active:
                close_listing(listing, "expired" if job.expires_at and job.expires_at < now else "too_old", now)
    session.flush()
    has_active = select(Listing.id).where(Listing.job_id == Job.id, Listing.is_active.is_(True)).exists()
    closed = session.execute(update(Job).where(Job.is_active.is_(True), ~has_active)
                             .values(is_active=False, closed_at=now).execution_options(synchronize_session=False)).rowcount
    reopened = session.execute(update(Job).where(Job.is_active.is_(False), has_active)
                               .values(is_active=True, closed_at=None).execution_options(synchronize_session=False)).rowcount
    # Company directory counters.
    counts = dict(session.execute(select(Job.company_id, func.count()).where(Job.is_active.is_(True), Job.company_id.is_not(None))
                                  .group_by(Job.company_id)).all())
    session.execute(update(Company).values(active_jobs_count=0).execution_options(synchronize_session=False))
    for cid, cnt in counts.items():
        session.execute(update(Company).where(Company.id == cid).values(active_jobs_count=cnt)
                        .execution_options(synchronize_session=False))
    session.expire_all()
    return {"jobs_closed": closed or 0, "jobs_reopened": reopened or 0}


# ------------------------------------------------------------------ running --

def _backoff(failures: int, blocked: bool) -> timedelta:
    if blocked:
        return timedelta(hours=24)
    if failures <= 1:
        return timedelta(0)  # retry at the next scheduled cycle
    return min(timedelta(hours=settings.collect_interval_hours) * 2 ** (failures - 1), timedelta(hours=24))


def run_source(cfg: SourceConfig, fetcher: PoliteFetcher, *, force: bool = False) -> CollectionRun:
    now = utcnow()
    with session_scope() as session:
        source = session.get(Source, cfg.key)
        assert source is not None, "sync_sources() must run first"
        run = CollectionRun(source_key=cfg.key, started_at=now)
        session.add(run)
        if not cfg.enabled:
            run.status, run.finished_at, run.error = "skipped", now, source.disabled_reason
            return run
        if not force and source.next_run_after and source.next_run_after > now:
            run.status, run.finished_at = "skipped", now
            run.error = f"backing off until {source.next_run_after:%Y-%m-%d %H:%M} UTC after failures"
            return run
        session.flush()
        run_id = run.id
        known = frozenset(session.scalars(select(Listing.external_id).where(Listing.source_key == cfg.key)).all())
        known_profiles = frozenset(p for p in session.scalars(select(Company.source_profile_url)).all() if p)
        prev_active = session.scalar(select(func.count()).select_from(Listing)
                                     .where(Listing.source_key == cfg.key, Listing.is_active.is_(True))) or 0

    collector = cfg.build()
    ctx = CollectContext(fetcher=fetcher, known_ids=known, known_company_profiles=known_profiles, max_details=cfg.max_details)
    requests_before = fetcher.request_count
    counts = {"seen": 0, "created": 0, "updated": 0, "merged": 0, "rejected": 0, "closed": 0}
    seen_ids: set[str] = set()
    status, error = "success", None
    t0 = time.monotonic()

    # Long-lived session with periodic commits: one bad item never rolls back the rest.
    s = SessionLocal()
    try:
        pending = 0
        for item in collector.collect(ctx):
            ts = utcnow()
            if isinstance(item, SeenListing):
                if mark_seen(s, cfg.key, item.external_id, ts):
                    seen_ids.add(item.external_id)
                    counts["seen"] += 1
            else:
                counts["seen"] += 1
                seen_ids.add(item.external_id)
                try:
                    n = normalize(item, now=ts)
                except Rejected as exc:
                    counts["rejected"] += 1
                    log.debug("%s rejected %s: %s", cfg.key, item.url, exc)
                    continue
                except Exception as exc:  # parser bug on one item must not kill the run
                    ctx.item_error(f"{cfg.key}: normalize {item.url}: {exc!r}")
                    continue
                try:
                    with s.begin_nested():
                        res = upsert(s, cfg.key, item, n, ts)
                    if res.action in counts:
                        counts[res.action] += 1
                except Exception as exc:
                    ctx.item_error(f"{cfg.key}: store {item.url}: {exc!r}")
                    continue
            pending += 1
            if pending >= COMMIT_EVERY:
                s.commit()
                pending = 0
        s.commit()

        # Close offers that disappeared from a complete snapshot.
        if collector.complete_snapshot and collector.run_complete:
            active = s.scalars(select(Listing).where(Listing.source_key == cfg.key, Listing.is_active.is_(True))).all()
            missing = [l for l in active if l.external_id not in seen_ids]
            if prev_active >= 10 and len(seen_ids) < prev_active * MIN_SNAPSHOT_RATIO:
                status = "partial"
                error = (f"snapshot suspiciously small ({len(seen_ids)} offers vs {prev_active} active before); "
                         "no offer was closed")
            else:
                for l in missing:
                    close_listing(l, "removed_from_source", utcnow())
                counts["closed"] += len(missing)
        else:
            counts["closed"] += check_unseen(s, cfg, collector, fetcher, now, utcnow())
        s.commit()
        if ctx.item_errors and status == "success":
            status = "partial"
    except (AccessBlocked, RobotsDisallowed) as exc:
        s.rollback()
        status, error = "blocked", str(exc)
    except Exception as exc:
        s.rollback()
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        log.exception("source %s failed", cfg.key)
    finally:
        s.close()

    with session_scope() as session:
        run = session.get(CollectionRun, run_id)
        source = session.get(Source, cfg.key)
        finished = utcnow()
        run.finished_at, run.status, run.error = finished, status, error
        for k, v in counts.items():
            setattr(run, k, v)
        run.item_errors = len(ctx.item_errors)
        run.error_samples = ctx.item_errors[:10] or None
        run.requests = fetcher.request_count - requests_before
        source.last_run_at, source.last_status = finished, status
        if status in ("success", "partial"):
            source.last_success_at, source.consecutive_failures, source.next_run_after = finished, 0, None
            source.last_error = error or (ctx.item_errors[0] if ctx.item_errors else None)
        else:
            source.consecutive_failures = (source.consecutive_failures or 0) + 1
            source.last_error = error
            delay = _backoff(source.consecutive_failures, status == "blocked")
            source.next_run_after = finished + delay if delay else None
        log.info("source %s: %s in %.1fs %s", cfg.key, status, time.monotonic() - t0, counts)
        return run


# ------------------------------------------------------------ orchestration --

class RunLock:
    """Cross-process lock so the scheduler and a manual run never overlap."""

    def __init__(self, path: Path | None = None, stale_after: timedelta = timedelta(hours=3)):
        self.path = path or (BACKEND_DIR / "data" / "collect.lock")
        self.stale_after = stale_after
        self._fd: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime > self.stale_after.total_seconds():
            self.path.unlink(missing_ok=True)  # left over by a crashed run
        try:
            self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self._fd, str(os.getpid()).encode())
            return True
        except FileExistsError:
            return False

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
            self.path.unlink(missing_ok=True)


def run_all(*, only: list[str] | None = None, force: bool = False, fetcher: PoliteFetcher | None = None,
            configs: list[SourceConfig] | None = None) -> list[CollectionRun] | None:
    lock = RunLock()
    if not lock.acquire():
        log.warning("another collection is running; skipping")
        return None
    own_fetcher = fetcher is None
    fetcher = fetcher or PoliteFetcher()
    try:
        configs = configs if configs is not None else load_sources()
        with session_scope() as session:
            sync_sources(session, configs)
        runs = []
        for cfg in configs:
            if only and cfg.key not in only:
                continue
            if not cfg.enabled and not only:
                continue
            runs.append(run_source(cfg, fetcher, force=force or bool(only)))
        snapshots = frozenset(c.key for c in configs if c.build().complete_snapshot)
        with session_scope() as session:
            stats = refresh_job_states(session, utcnow(), snapshots)
        log.info("lifecycle: %s", stats)
        return runs
    finally:
        if own_fetcher:
            fetcher.close()
        lock.release()
