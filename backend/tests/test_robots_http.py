import httpx
import pytest

from jobhub.collectors.http import AccessBlocked, FetchError, NotFound, PoliteFetcher, RobotsDisallowed
from jobhub.collectors.robots import RobotsPolicy

KEEJOB_LIKE = """
User-agent: *
Disallow: /api/
Disallow: /recruiter/*
Allow: /recruiter/$
Disallow: /offres-emploi/*/postuler/
Disallow: /*paginate_by=

User-agent: BadBot
Disallow: /
"""


def test_wildcards_and_anchors():
    p = RobotsPolicy.parse(KEEJOB_LIKE)
    ua = "TunisieJobsBot"
    assert p.can_fetch(ua, "https://x.tn/offres-emploi/123/dev/")
    assert not p.can_fetch(ua, "https://x.tn/offres-emploi/123/dev/postuler/")
    assert not p.can_fetch(ua, "https://x.tn/api/jobs")
    assert p.can_fetch(ua, "https://x.tn/recruiter/")          # Allow ... $ (longer match wins)
    assert not p.can_fetch(ua, "https://x.tn/recruiter/login")
    assert not p.can_fetch(ua, "https://x.tn/offres-emploi/?paginate_by=50")
    assert p.can_fetch(ua, "https://x.tn/robots.txt")


def test_specific_group_overrides_star():
    p = RobotsPolicy.parse(KEEJOB_LIKE)
    assert not p.can_fetch("BadBot/2.0", "https://x.tn/offres-emploi/")


def test_empty_disallow_and_crawl_delay():
    p = RobotsPolicy.parse("User-agent: *\nDisallow:\nCrawl-delay: 4\n")
    assert p.can_fetch("a", "https://x.tn/anything")
    assert p.crawl_delay("a") == 4


def test_grouped_user_agents_share_rules():
    p = RobotsPolicy.parse("User-agent: A\nUser-agent: TunisieJobsBot\nDisallow: /private\n\nUser-agent: *\nDisallow: /\n")
    assert p.can_fetch("TunisieJobsBot/1.0", "https://x.tn/jobs")
    assert not p.can_fetch("TunisieJobsBot/1.0", "https://x.tn/private/1")
    assert not p.can_fetch("Other", "https://x.tn/jobs")


def test_smartrecruiters_style_robots_blocks_us():
    p = RobotsPolicy.parse("User-agent: LinkedInBot\nAllow: /v1/companies/\nUser-agent: *\nDisallow: /\n")
    assert not p.can_fetch("TunisieJobsBot", "https://api.smartrecruiters.com/v1/companies/X/postings")


def test_fetcher_refuses_disallowed_url(web):
    web.add("https://site.tn/robots.txt", "User-agent: *\nDisallow: /private/\n", content_type="text/plain")
    web.add("https://site.tn/private/x", "secret")
    f = web.fetcher()
    with pytest.raises(RobotsDisallowed):
        f.get("https://site.tn/private/x")
    assert "https://site.tn/private/x" not in web.calls


def test_robots_403_means_stay_out(web):
    web.add("https://walled.tn/robots.txt", "denied", status=403)
    with pytest.raises(RobotsDisallowed):
        web.fetcher().get("https://walled.tn/jobs")


def test_cloudflare_challenge_is_reported_not_bypassed(web):
    web.add("https://cf.tn/jobs", "<html><head><title>Just a moment...</title></head>"
            "<script src='https://challenges.cloudflare.com/x'></script></html>", status=403)
    f = web.fetcher()
    with pytest.raises(AccessBlocked):
        f.get("https://cf.tn/jobs")
    assert web.calls.count("https://cf.tn/jobs") == 1  # no retry loop


def test_transient_errors_are_retried(web):
    attempts = {"n": 0}

    def flaky(request):
        attempts["n"] += 1
        return httpx.Response(503 if attempts["n"] < 3 else 200, text="ok")

    web.routes["https://flaky.tn/x"] = flaky
    assert web.fetcher().get_text("https://flaky.tn/x") == "ok"
    assert attempts["n"] == 3


def test_retries_exhausted_raise(web):
    web.add("https://down.tn/x", "err", status=500)
    with pytest.raises(FetchError):
        web.fetcher(max_attempts=2).get("https://down.tn/x")


def test_not_found(web):
    with pytest.raises(NotFound):
        web.fetcher().get("https://site.tn/missing")


def test_per_host_throttling_respects_crawl_delay():
    now = {"t": 0.0}
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        now["t"] += s

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 5\n")
        return httpx.Response(200, text="ok")

    f = PoliteFetcher(transport=httpx.MockTransport(handler), min_interval=1, sleep=sleep, clock=lambda: now["t"])
    f.get("https://slow.tn/a")
    f.get("https://slow.tn/b")
    assert sum(sleeps) >= 5
