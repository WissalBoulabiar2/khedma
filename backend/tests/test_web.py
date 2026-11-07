"""HTTP layer: JSON API and server-rendered pages."""
import pytest
from fastapi.testclient import TestClient

from jobhub.collectors.base import RawJob
from jobhub.db import session_scope
from jobhub.pipeline import refresh_job_states, run_source
from jobhub.sources import sync_sources
from jobhub.normalize.text import utcnow

from test_pipeline import FEEDS, cfg


@pytest.fixture
def client(db, web):
    FEEDS.clear()
    c = cfg("a")
    with session_scope() as s:
        sync_sources(s, [c])
    FEEDS["a"] = [
        RawJob(external_id="1", url="https://src.tn/1", apply_url="https://src.tn/1/apply", title="Développeur Python",
               company_name="Acme", company_website="https://acme.tn", location_raw="Sfax", country="TN",
               contract_raw=["CDI"], experience_raw="Entre 2 et 5 ans", published_at=utcnow().isoformat(),
               description_html="<p>Django et <script>alert(1)</script>PostgreSQL</p>"),
        RawJob(external_id="2", url="https://src.tn/2", title="Comptable", company_name="Beta", location_raw="Tunis",
               country="TN", contract_raw=["SIVP"], published_at=utcnow().isoformat(), description_text="Sage, Excel"),
    ]
    run_source(c, web.fetcher())
    with session_scope() as s:
        refresh_job_states(s, utcnow())
    from jobhub.main import app
    with TestClient(app) as tc:
        yield tc


def test_api_jobs_and_filters(client):
    r = client.get("/api/jobs").json()
    assert r["total"] == 2 and {f["value"] for f in r["facets"]["governorate"]} == {"sfax", "tunis"}
    r = client.get("/api/jobs", params={"contract": "sivp"}).json()
    assert [j["title"] for j in r["results"]] == ["Comptable"]
    r = client.get("/api/jobs", params={"q": "django"}).json()
    assert r["total"] == 1
    r = client.get("/api/jobs", params=[("experience", "junior"), ("governorate", "sfax")]).json()
    assert r["total"] == 1


def test_api_job_detail_sanitized(client):
    jid = client.get("/api/jobs", params={"q": "python"}).json()["results"][0]["id"]
    d = client.get(f"/api/jobs/{jid}").json()
    assert d["apply_url"] == "https://src.tn/1/apply"
    assert "<script" not in d["description_html"]
    assert d["sources"][0]["source"]["key"] == "a"
    assert client.get("/api/jobs/99999").status_code == 404


def test_api_companies_stats_taxonomy_sources(client):
    comps = client.get("/api/companies").json()
    assert comps["total"] == 2
    acme = client.get("/api/companies/acme").json()
    assert acme["website"] == "https://acme.tn" and len(acme["jobs"]) == 1
    st = client.get("/api/stats").json()
    assert st["active_jobs"] == 2 and st["hiring_companies"] == 2
    assert len(client.get("/api/taxonomy").json()["governorates"]) == 24
    assert client.get("/api/sources").json()[0]["active_offers"] == 2


def test_admin_requires_token(client):
    assert client.post("/api/admin/collect").status_code == 403


def test_pages_render(client):
    home = client.get("/")
    assert home.status_code == 200 and "Développeur Python" in home.text
    cat = client.get("/emplois?governorate=sfax&contract=cdi")
    assert cat.status_code == 200 and "1</strong> offre" in cat.text and "Comptable" not in cat.text
    jid = client.get("/api/jobs", params={"q": "python"}).json()["results"][0]["id"]
    r = client.get(f"/emplois/{jid}", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"].startswith(f"/emplois/{jid}-")
    page = client.get(r.headers["location"])
    assert page.status_code == 200 and "https://src.tn/1/apply" in page.text and "<script>alert" not in page.text
    assert client.get("/entreprises").status_code == 200
    assert "Acme" in client.get("/entreprises/acme").text
    assert client.get("/sources").status_code == 200
    assert client.get("/emplois/abc").status_code == 404
    assert client.get("/nope").status_code == 404


def test_bad_filter_values_are_ignored(client):
    r = client.get("/emplois?governorate=<script>&contract=xx&page=-3&sort=evil")
    assert r.status_code == 200 and "<script>&" not in r.text
