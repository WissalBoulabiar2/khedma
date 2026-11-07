"""Adapters against real pages captured from each source (tests/fixtures)."""
import json

import httpx
import pytest

from conftest import fixture_bytes
from jobhub.collectors.base import CollectContext, RawJob, SeenListing, build_collector
from jobhub.collectors.adapters.jsonld import find_job_postings
from jobhub.collectors.adapters.keejob import parse_detail, parse_index
from jobhub.normalize import normalize


def run(collector, fetcher, known=frozenset()):
    ctx = CollectContext(fetcher=fetcher, known_ids=frozenset(known))
    return list(collector.collect(ctx)), ctx


# ---------------------------------------------------------------- keejob --

def test_keejob_index_and_detail_parsing():
    idx = parse_index(fixture_bytes("keejob_index.txt").decode())
    assert len(idx) >= 20
    first = idx[0]
    assert first["id"].isdigit() and first["md_url"].endswith(".md") and first["published"]
    d = parse_detail(fixture_bytes("keejob_detail.md").decode())
    assert d["title"] == "Infrastructure & Security Officer"
    assert d["fields"]["entreprise"] == "BANK ABC"
    assert d["fields"]["expérience"] == "Entre 2 et 5 ans"
    assert d["company_url"].startswith("https://www.keejob.com/offres-emploi/entreprises/")
    assert d["active"] is True
    assert "Vulnerability Management" in d["description_md"] and "Postulez" not in d["description_md"]


def test_keejob_collector(web):
    idx_text = fixture_bytes("keejob_index.txt").decode()
    idx = parse_index(idx_text)
    web.add("https://www.keejob.com/llms-full.txt", idx_text, content_type="text/plain")
    web.add(idx[0]["md_url"], fixture_bytes("keejob_detail.md"), content_type="text/markdown")
    web.add_fixture("https://www.keejob.com/offres-emploi/entreprises/11980/abc-bank/", "keejob_company.html")
    c = build_collector("keejob", "keejob", "Keejob", {})
    items, ctx = run(c, web.fetcher(), known={idx[1]["id"]})
    raws = [i for i in items if isinstance(i, RawJob)]
    seen = [i for i in items if isinstance(i, SeenListing)]
    assert [s.external_id for s in seen] == [idx[1]["id"]]
    assert len(raws) == 1  # other detail pages 404 in this fake web -> skipped, not fatal
    r = raws[0]
    assert r.company_name == "BANK ABC" and r.company_website == "http://www.bank-abc.com"
    assert r.url.endswith("/") and not r.url.endswith(".md/")
    n = normalize(r)
    assert n.governorate == "tunis" and n.contracts == ["cdi"] and n.workplace == "sur-site"
    assert n.experience_level == "junior" and n.domain == "informatique"
    assert "postuler" not in " ".join(web.calls)


def test_keejob_empty_index_is_a_source_failure(web):
    web.add("https://www.keejob.com/llms-full.txt", "# nothing\n", content_type="text/plain")
    with pytest.raises(Exception):
        run(build_collector("keejob", "keejob", "Keejob", {}), web.fetcher())


# -------------------------------------------------------- workable network --

def test_workable_search(web):
    page1 = json.loads(fixture_bytes("workable_search.json"))

    def api(request: httpx.Request):
        if request.url.params.get("pageToken"):
            return httpx.Response(200, json={"jobs": [page1["jobs"][0]], "nextPageToken": None})
        return httpx.Response(200, json=page1)

    web.routes["https://jobs.workable.com/api/v1/jobs?*"] = api
    c = build_collector("workable_search", "wk", "Workable", {"location": "Tunisia"})
    items, _ = run(c, web.fetcher())
    assert len(items) == len(page1["jobs"])  # duplicate on page 2 ignored
    assert c.run_complete
    dentons = items[0]
    n = normalize(dentons)
    assert n.company_name == "Dentons" and n.governorate == "tunis" and n.workplace == "hybride"
    remote = [normalize(i) for i in items if i.workplace_raw == "remote"]
    assert remote and all(r.workplace == "teletravail" for r in remote)


# ------------------------------------------------------------------- ATS --

def test_lever_keeps_only_tunisia(web):
    web.add("https://api.lever.co/v0/postings/Yassir?*", fixture_bytes("lever_yassir.json"), content_type="application/json")
    items, _ = run(build_collector("lever", "lv", "Yassir", {"board": "Yassir"}), web.fetcher())
    assert len(items) == 3
    for r in items:
        n = normalize(r)
        assert n.governorate == "tunis" and n.company_name == "Yassir"


def test_recruitee(web):
    web.add("https://ozeol.recruitee.com/api/offers/", fixture_bytes("recruitee_ozeol.json"), content_type="application/json")
    items, _ = run(build_collector("recruitee", "rc", "Ozeol", {"board": "ozeol"}), web.fetcher())
    assert len(items) == 3
    n = normalize(items[0])
    assert n.governorate == "sousse" and n.contracts == ["cdi"]


def test_greenhouse_filters_non_tunisian(web):
    web.add("https://boards-api.greenhouse.io/v1/boards/alxafrica/jobs?*", fixture_bytes("greenhouse_alxafrica.json"),
            content_type="application/json")
    items, _ = run(build_collector("greenhouse", "gh", "ALX", {"board": "alxafrica"}), web.fetcher())
    assert items == []  # ALX currently has no Tunis offers


def test_ats_requires_board():
    with pytest.raises(ValueError):
        build_collector("lever", "x", "X", {})


# ---------------------------------------------------- generic JSON-LD (feed) --

def test_jsonld_extraction():
    p = find_job_postings(fixture_bytes("tunisietravail_detail.html").decode())
    assert len(p) == 1 and p[0]["title"] == "Ingénieur Production"


def test_jsonld_feed_collector(web):
    from lxml import etree
    feed = fixture_bytes("tunisietravail_feed.xml")
    links = [i.findtext("link") for i in etree.fromstring(feed).iter("item")]
    web.add("https://www.tunisietravail.net/feed/", feed, content_type="application/rss+xml")
    web.add_fixture(links[0], "tunisietravail_detail.html")
    c = build_collector("jsonld", "tt", "Tunisie Travail", {
        "feed_url": "https://www.tunisietravail.net/feed/", "title_exclude_pattern": "^concours",
        "content_xpath": '//div[contains(@class,"PostContent")]', "country": "TN"})
    items, ctx = run(c, web.fetcher())
    assert len(items) == 1
    r = items[0]
    assert r.title == "Ingénieur Production" and r.company_name == "Industrie Multinational"
    assert r.company_website is None  # sameAs pointed to the board itself
    n = normalize(r)
    assert n.contracts == [] and n.governorate == "tunis"
    assert len(n.description_text) > 300  # full text from the page, not the truncated JSON-LD
    assert not any("concours" in c.lower() for c in web.calls)


def test_jsonld_requires_discovery_option():
    with pytest.raises(ValueError):
        build_collector("jsonld", "x", "X", {})


# --------------------------------------------------------------- farojob --

def test_farojob(web):
    web.add_fixture("https://www.farojob.net/jobs/", "farojob_list.html")
    url = "https://www.farojob.net/jobs/zitouna-tamkeen-recrute-chef-service-reporting/"
    web.add_fixture(url, "farojob_detail.html")
    c = build_collector("farojob", "fj", "Farojob", {"max_pages": 2})
    items, ctx = run(c, web.fetcher())
    raws = [i for i in items if isinstance(i, RawJob)]
    assert len(raws) == 1  # other detail pages 404 here
    r = raws[0]
    assert r.title == "Chef Service Reporting" and r.company_name == "Zitouna Tamkeen"
    n = normalize(r)
    assert n.governorate == "tunis" and n.published_at.month == 10
    assert "microfinance islamique" in n.description_text
