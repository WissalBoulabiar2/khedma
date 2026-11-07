"""Static export used for free hosting (GitHub Pages)."""
import json

from jobhub.static_site import build_static, rewrite_links
from jobhub.db import session_scope

from test_web import client  # noqa: F401  (fixture: DB seeded with two jobs)


def test_rewrite_links():
    html = ('<a href="/">h</a><a href="/emplois?domain=x">c</a><a href="/emplois/1-dev">j</a>'
            '<link href="/static/css/app.css?v=1"><a href="https://ext.tn/a">e</a><a href="//cdn.x/y">p</a>'
            '<form action="/emplois"></form><a href="/sources#top">s</a>')
    out = rewrite_links(html, "/khedma")
    assert 'href="/khedma/"' in out
    assert 'href="/khedma/emplois/?domain=x"' in out
    assert 'href="/khedma/emplois/1-dev/"' in out
    assert 'href="/khedma/static/css/app.css?v=1"' in out
    assert 'href="https://ext.tn/a"' in out and 'href="//cdn.x/y"' in out
    assert 'action="/khedma/emplois/"' in out
    assert 'href="/khedma/sources/#top"' in out
    assert 'href="/emplois/' not in rewrite_links('<a href="/emplois/">x</a>', "/b")


def test_build_static(client, tmp_path):  # noqa: F811
    out = tmp_path / "site"
    with session_scope() as s:
        stats = build_static(s, out, "/khedma", "https://me.github.io/khedma")
    assert stats["active_jobs"] == 2
    for rel in ["index.html", "emplois/index.html", "entreprises/index.html", "entreprises/acme/index.html",
                "sources/index.html", "404.html", "data/jobs.json", "static/js/catalogue.js", "sitemap.xml", ".nojekyll"]:
        assert (out / rel).exists(), rel
    index = json.loads((out / "data" / "jobs.json").read_text(encoding="utf-8"))
    assert {j["t"] for j in index["jobs"]} == {"Développeur Python", "Comptable"}
    assert index["labels"]["governorate"]["sfax"] == "Sfax"
    job_pages = list((out / "emplois").glob("*-*/index.html"))
    assert len(job_pages) == 2
    page = job_pages[0].read_text(encoding="utf-8")
    assert 'href="/khedma/static/css/app.css' in page
    assert 'rel="canonical" href="https://me.github.io/khedma/emplois/' in page
    assert "/api/docs" not in (out / "index.html").read_text(encoding="utf-8")
    assert "https://me.github.io/khedma/emplois/" in (out / "sitemap.xml").read_text(encoding="utf-8")
