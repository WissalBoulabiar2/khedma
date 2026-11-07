"""End-to-end pipeline behaviour on a temporary SQLite database."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from jobhub.collectors.base import Collector, RawJob, SeenListing, register
from jobhub.collectors.http import AccessBlocked, FetchError
from jobhub.db import session_scope
from jobhub.models import CollectionRun, Company, Job, Listing, Source
from jobhub.normalize.text import utcnow
from jobhub.pipeline import refresh_job_states, run_all, run_source
from jobhub.search import JobQuery, search_jobs
from jobhub.sources import SourceConfig, sync_sources

# Fake source behaviour, keyed by source key: list of items or an exception.
FEEDS: dict[str, object] = {}


@register("fake_snapshot")
class FakeSnapshot(Collector):
    complete_snapshot = True

    def collect(self, ctx):
        feed = FEEDS[self.key]
        if isinstance(feed, Exception):
            raise feed
        for item in feed:
            if isinstance(item, Exception):
                raise item
            if isinstance(item, RawJob) and item.external_id in ctx.known_ids and self.options.get("seen_only"):
                yield SeenListing(item.external_id)
            else:
                yield item


@register("fake_feed")
class FakeFeed(FakeSnapshot):
    complete_snapshot = False

    def check_alive(self, fetcher, url):
        return not url.endswith("/gone")


def job(ext, title="Développeur Python", company="Acme", loc="Sfax", **kw):
    return RawJob(external_id=ext, url=f"https://src.tn/{ext}", title=title, company_name=company,
                  description_html=f"<p>{title} chez {company}.</p>", location_raw=loc, country="TN",
                  contract_raw=kw.pop("contract", ["CDI"]), published_at=kw.pop("published", utcnow().date().isoformat()), **kw)


def cfg(key, type_="fake_snapshot", **opts):
    return SourceConfig(key=key, name=key.title(), type=type_, options=opts)


@pytest.fixture
def sources(db, web):
    configs = [cfg("a"), cfg("b"), cfg("feed", "fake_feed")]
    with session_scope() as s:
        sync_sources(s, configs)
    FEEDS.clear()
    fetcher = web.fetcher()
    return {c.key: c for c in configs}, fetcher


def count(model, *where):
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def finish():
    with session_scope() as s:
        refresh_job_states(s, utcnow())


def test_create_then_unchanged(sources):
    c, f = sources
    FEEDS["a"] = [job("1"), job("2", "Comptable", "Beta", "Tunis")]
    r = run_source(c["a"], f)
    assert (r.status, r.created, r.seen) == ("success", 2, 2)
    r = run_source(c["a"], f)
    assert (r.created, r.updated, r.closed) == (0, 0, 0)
    assert count(Job) == 2 and count(Company) == 2


def test_rejects_foreign_offers(sources):
    c, f = sources
    FEEDS["a"] = [job("1"), RawJob(external_id="x", url="https://src.tn/x", title="Dev", country="FR", location_raw="Paris")]
    r = run_source(c["a"], f)
    assert (r.created, r.rejected) == (1, 1)


def test_cross_source_duplicates_are_merged(sources):
    c, f = sources
    FEEDS["a"] = [job("1", "Développeur Python (H/F)", "ACME SARL")]
    FEEDS["b"] = [job("zz", "Developpeur Python", "Acme Tunisie")]
    run_source(c["a"], f)
    r = run_source(c["b"], f)
    assert (r.created, r.merged) == (0, 1)
    assert count(Job) == 1 and count(Listing) == 2


def test_fuzzy_duplicate_same_company(sources):
    c, f = sources
    FEEDS["a"] = [job("1", "Ingénieur DevOps Senior", "Acme", "Tunis")]
    FEEDS["b"] = [job("2", "Ingénieur DevOps Sénior -", "Acme", "Les Berges du Lac")]
    run_source(c["a"], f)
    assert run_source(c["b"], f).merged == 1


def test_different_offers_same_company_not_merged(sources):
    c, f = sources
    FEEDS["a"] = [job("1", "Comptable", "Acme"), job("2", "Commercial terrain", "Acme")]
    assert run_source(c["a"], f).created == 2


def test_snapshot_closes_removed_offers_and_job_survives_on_other_source(sources):
    c, f = sources
    FEEDS["a"] = [job(str(i), f"Poste numero {i} specialiste", "Co") for i in range(12)]
    FEEDS["b"] = [job("b0", "Poste numero 0 specialiste", "Co")]
    run_source(c["a"], f)
    run_source(c["b"], f)
    FEEDS["a"] = FEEDS["a"][1:]  # offer 0 removed from source a (but still on b)
    FEEDS["a"] = FEEDS["a"][:-1]  # offer 11 removed everywhere
    r = run_source(c["a"], f)
    assert r.closed == 2
    finish()
    assert count(Job, Job.is_active.is_(True)) == 11
    with session_scope() as s:
        assert s.scalar(select(Job.is_active).where(Job.title == "Poste numero 11 specialiste")) is False


def test_suspicious_small_snapshot_closes_nothing(sources):
    c, f = sources
    FEEDS["a"] = [job(str(i), f"Poste {i} expert technique", "Co") for i in range(20)]
    run_source(c["a"], f)
    FEEDS["a"] = FEEDS["a"][:3]
    r = run_source(c["a"], f)
    assert r.status == "partial" and r.closed == 0 and "suspiciously" in r.error
    assert count(Listing, Listing.is_active.is_(True)) == 20


def test_source_failure_keeps_data_and_backs_off(sources):
    c, f = sources
    FEEDS["a"] = [job("1")]
    run_source(c["a"], f)
    FEEDS["a"] = FetchError("HTTP 500")
    r1 = run_source(c["a"], f)
    assert r1.status == "failed" and count(Listing, Listing.is_active.is_(True)) == 1
    r2 = run_source(c["a"], f)   # 2nd failure -> exponential backoff starts
    assert r2.status == "failed"
    with session_scope() as s:
        src = s.get(Source, "a")
        assert src.consecutive_failures == 2 and src.next_run_after > utcnow()
    assert run_source(c["a"], f).status == "skipped"
    FEEDS["a"] = [job("1")]
    assert run_source(c["a"], f, force=True).status == "success"
    with session_scope() as s:
        assert s.get(Source, "a").consecutive_failures == 0


def test_failure_mid_stream_rolls_back_nothing_committed_wrongly(sources):
    c, f = sources
    FEEDS["a"] = [job(str(i), f"Offre {i} analyste", "Co") for i in range(10)]
    run_source(c["a"], f)
    FEEDS["a"] = [job("0", "Offre 0 analyste", "Co"), FetchError("connection reset")]
    r = run_source(c["a"], f)
    assert r.status == "failed" and r.closed == 0
    assert count(Listing, Listing.is_active.is_(True)) == 10


def test_blocked_source(sources):
    c, f = sources
    FEEDS["a"] = AccessBlocked("challenge")
    r = run_source(c["a"], f)
    assert r.status == "blocked"
    with session_scope() as s:
        assert s.get(Source, "a").next_run_after > utcnow() + timedelta(hours=20)


def test_seen_listing_reactivates_closed_offer(sources):
    c, f = sources
    FEEDS["a"] = [job(str(i), f"Poste {i} gestionnaire", "Co") for i in range(3)]
    run_source(c["a"], f)
    FEEDS["a"] = FEEDS["a"][:2]
    run_source(c["a"], f)
    finish()
    assert count(Job, Job.is_active.is_(True)) == 2
    FEEDS["a"] = [SeenListing("0"), SeenListing("1"), SeenListing("2")]
    run_source(c["a"], f)
    finish()
    assert count(Job, Job.is_active.is_(True)) == 3


def test_feed_source_liveness_and_expiry(sources):
    c, f = sources
    old = (utcnow() - timedelta(days=5)).date().isoformat()
    FEEDS["feed"] = [job("ok", "Offre toujours en ligne", published=old), job("gone", "Offre retiree", published=old),
                     job("exp", "Offre expiree", expires_at=(utcnow() - timedelta(days=1)).isoformat())]
    run_source(c["feed"], f)
    with session_scope() as s:  # pretend the first two dropped out of the feed 3 days ago
        for l in s.scalars(select(Listing)).all():
            if l.external_id in ("ok", "gone"):
                l.last_seen_at = utcnow() - timedelta(days=3)
    FEEDS["feed"] = []
    r = run_source(c["feed"], f)
    assert r.closed == 1  # "gone" (liveness check said so)
    finish()
    with session_scope() as s:
        active = set(s.scalars(select(Job.title).where(Job.is_active.is_(True))).all())
    assert active == {"Offre toujours en ligne"}  # expired offer closed by lifecycle


def test_old_offer_still_listed_by_snapshot_source_stays_open(sources):
    c, f = sources
    old = (utcnow() - timedelta(days=200)).date().isoformat()
    FEEDS["a"] = [job("1", "Poste ouvert longtemps", published=old)]
    FEEDS["feed"] = [job("2", "Vieille annonce de flux", published=old)]
    run_source(c["a"], f)
    run_source(c["feed"], f)
    with session_scope() as s:
        refresh_job_states(s, utcnow(), snapshot_sources=frozenset({"a"}))
        active = set(s.scalars(select(Job.title).where(Job.is_active.is_(True))).all())
    assert active == {"Poste ouvert longtemps"}


def test_run_all_and_search(sources):
    c, f = sources
    FEEDS["a"] = [job("1", "Développeur Python Django", "Acme", "Sfax", contract=["CDI"]),
                  job("2", "Comptable senior", "Beta", "Tunis", contract=["CDD"]),
                  job("3", "Stage PFE Data Science", "Gamma", "Ariana", contract=["Stage"])]
    FEEDS["b"], FEEDS["feed"] = [], []
    runs = run_all(fetcher=f, configs=list(c.values()))
    assert [r.status for r in runs] == ["success", "success", "success"]
    with session_scope() as s:
        res = search_jobs(s, JobQuery(q="pyth"))
        assert [j.title for j in res.jobs] == ["Développeur Python Django"]
        assert search_jobs(s, JobQuery(q="developpeur")).total == 1  # accent-insensitive
        assert search_jobs(s, JobQuery(governorate="tunis")).total == 1
        assert search_jobs(s, JobQuery(contract=["cdd", "stage"])).total == 2
        assert search_jobs(s, JobQuery(experience=["debutant"])).total == 1
        assert search_jobs(s, JobQuery(domain="finance-comptabilite")).total == 1
        res = search_jobs(s, JobQuery(contract=["cdi"]))
        gov = {f["value"]: f["count"] for f in res.facets["governorate"]}
        assert gov == {"sfax": 1}
        contracts = {f["value"]: f["count"] for f in res.facets["contract"]}
        assert contracts == {"cdi": 1, "cdd": 1, "stage": 1}  # own facet ignores own filter
        assert search_jobs(s, JobQuery(q='"; DROP TABLE jobs; --')).total == 0
        assert search_jobs(s, JobQuery(governorate="nowhere", page=-5, per_page=10**6)).total == 3
    assert count(CollectionRun) == 3


def test_run_lock_prevents_overlap(sources, tmp_path):
    from jobhub import pipeline
    lock = pipeline.RunLock()
    assert lock.acquire()
    try:
        c, f = sources
        assert run_all(fetcher=f, configs=list(c.values())) is None
    finally:
        lock.release()
