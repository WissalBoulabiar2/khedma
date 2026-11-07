from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

import httpx
import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeWeb:
    """httpx transport serving canned responses; unknown robots.txt -> 404."""

    def __init__(self) -> None:
        self.routes: dict[str, Callable[[httpx.Request], httpx.Response] | tuple] = {}
        self.calls: list[str] = []

    def add(self, url: str, body: bytes | str = b"", status: int = 200, content_type: str = "text/html; charset=utf-8",
            headers: dict | None = None) -> None:
        if isinstance(body, str):
            body = body.encode()
        self.routes[url] = (status, body, {"content-type": content_type, **(headers or {})})

    def add_fixture(self, url: str, name: str, content_type: str = "text/html; charset=utf-8") -> None:
        self.add(url, fixture_bytes(name), content_type=content_type)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        route = self.routes.get(url) or self.routes.get(url.split("?")[0] + "?*")
        if route is None and url.split("?")[0] in self.routes and "?" in url:
            route = self.routes[url.split("?")[0]]
        if route is None:
            if url.endswith("/robots.txt"):
                return httpx.Response(404, text="")
            return httpx.Response(404, text="not found")
        if callable(route):
            return route(request)
        status, body, headers = route
        return httpx.Response(status, content=body, headers=headers)

    def fetcher(self, **kw):
        from jobhub.collectors.http import PoliteFetcher
        return PoliteFetcher(transport=httpx.MockTransport(self.handler), min_interval=0, sleep=lambda s: None, **kw)


@pytest.fixture
def web() -> FakeWeb:
    return FakeWeb()


@pytest.fixture
def db(tmp_path, monkeypatch):
    from jobhub import db as dbmod
    from jobhub import pipeline
    dbmod.configure(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    dbmod.init_db()
    real_lock = pipeline.RunLock
    monkeypatch.setattr(pipeline, "RunLock", lambda: real_lock(tmp_path / "collect.lock"))
    yield dbmod
    dbmod.engine().dispose()
