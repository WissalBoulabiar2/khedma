"""Polite HTTP client shared by all collectors.

Guarantees, for every request a collector makes:
* robots.txt of the target host is checked first (cached for 24h);
* requests to the same host are spaced out (min interval or Crawl-delay);
* transient failures (timeouts, 429, 5xx) are retried with backoff;
* anti-bot challenges / access restrictions are detected and reported as
  ``AccessBlocked`` - they are never worked around.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from ..config import settings
from .robots import RobotsPolicy

log = logging.getLogger(__name__)

ROBOTS_TTL_SECONDS = 24 * 3600
MAX_CRAWL_DELAY = 30.0
MAX_RETRY_AFTER = 60.0

_CHALLENGE_MARKERS = (
    "<title>just a moment...</title>",
    "challenges.cloudflare.com",
    "cf-browser-verification",
    "g-recaptcha",
    "h-captcha",
    "captcha-delivery.com",  # DataDome
    "_incapsula_resource",
    "px-captcha",            # PerimeterX
)


class FetchError(Exception):
    """A request failed after retries (network error, unexpected status...)."""

    def __init__(self, message: str, status: int | None = None, url: str | None = None):
        super().__init__(message)
        self.status = status
        self.url = url


class NotFound(FetchError):
    """404 / 410 - the resource no longer exists (used for liveness checks)."""


class RobotsDisallowed(FetchError):
    """robots.txt forbids fetching this URL for our user-agent."""


class AccessBlocked(FetchError):
    """The site requires auth or presents an anti-bot challenge. We stop here."""


@dataclass
class _HostState:
    last_request: float = 0.0
    robots: RobotsPolicy | None = None
    robots_fetched_at: float = 0.0


class PoliteFetcher:
    def __init__(
        self,
        *,
        user_agent: str | None = None,
        robots_token: str | None = None,
        min_interval: float | None = None,
        timeout: float | None = None,
        max_attempts: int = 3,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.user_agent = user_agent or settings.user_agent
        self.robots_token = robots_token or settings.robots_token
        self.min_interval = settings.min_request_interval if min_interval is None else min_interval
        self.max_attempts = max_attempts
        self._sleep = sleep
        self._clock = clock
        self._hosts: dict[str, _HostState] = {}
        self._lock = threading.Lock()
        self.request_count = 0
        self._client = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Language": "fr,en;q=0.8"},
            timeout=timeout or settings.request_timeout,
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteFetcher":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- robots -------------------------------------------------------
    def _host_key(self, url: str) -> str:
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}".lower()

    def _state(self, host: str) -> _HostState:
        with self._lock:
            return self._hosts.setdefault(host, _HostState())

    def robots_for(self, url: str) -> RobotsPolicy:
        host = self._host_key(url)
        st = self._state(host)
        now = self._clock()
        if st.robots is not None and now - st.robots_fetched_at < ROBOTS_TTL_SECONDS:
            return st.robots
        robots_url = host + "/robots.txt"
        try:
            self._throttle(st, None)
            resp = self._client.get(robots_url)
            self.request_count += 1
            if resp.status_code == 200:
                policy = RobotsPolicy.parse(resp.text)
            elif resp.status_code in (404, 410):
                policy = RobotsPolicy.allowing_all()
            elif resp.status_code in (401, 403):
                # Access to robots.txt itself is restricted (often an anti-bot
                # wall).  Be conservative: treat the whole host as off-limits.
                policy = RobotsPolicy.denying_all()
            else:  # 5xx and others: unreachable, assume complete disallow (RFC 9309 2.3.1.4)
                policy = RobotsPolicy.denying_all()
        except httpx.HTTPError as exc:
            log.warning("robots.txt unreachable for %s: %s", host, exc)
            policy = RobotsPolicy.denying_all()
        st.robots, st.robots_fetched_at = policy, now
        return policy

    def is_allowed(self, url: str) -> bool:
        return self.robots_for(url).can_fetch(self.robots_token, url)

    # -- throttling -----------------------------------------------------
    def _throttle(self, st: _HostState, policy: RobotsPolicy | None) -> None:
        interval = self.min_interval
        if policy is not None:
            delay = policy.crawl_delay(self.robots_token)
            if delay:
                interval = max(interval, min(delay, MAX_CRAWL_DELAY))
        wait = st.last_request + interval - self._clock()
        if wait > 0:
            self._sleep(wait)
        st.last_request = self._clock()

    # -- requests -------------------------------------------------------
    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        policy = self.robots_for(url)
        if not policy.can_fetch(self.robots_token, url):
            raise RobotsDisallowed(f"robots.txt disallows {url}", url=url)
        st = self._state(self._host_key(url))
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._throttle(st, policy)
            try:
                resp = self._client.request(method, url, **kwargs)
                self.request_count += 1
            except httpx.TimeoutException as exc:
                last_exc = FetchError(f"timeout fetching {url}: {exc}", url=url)
            except httpx.HTTPError as exc:
                last_exc = FetchError(f"network error fetching {url}: {exc}", url=url)
            else:
                if _looks_like_challenge(resp):
                    raise AccessBlocked(
                        f"anti-bot challenge or access restriction at {url} (HTTP {resp.status_code})",
                        status=resp.status_code, url=url)
                if resp.status_code in (401, 403):
                    raise AccessBlocked(f"access denied at {url} (HTTP {resp.status_code})",
                                        status=resp.status_code, url=url)
                if resp.status_code in (404, 410):
                    raise NotFound(f"not found: {url}", status=resp.status_code, url=url)
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = FetchError(f"HTTP {resp.status_code} at {url}", status=resp.status_code, url=url)
                    retry_after = _retry_after(resp)
                    if attempt < self.max_attempts:
                        self._sleep(retry_after if retry_after is not None else 2.0 * 2 ** (attempt - 1))
                    continue
                if resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code} at {url}", status=resp.status_code, url=url)
                return resp
            if attempt < self.max_attempts:
                self._sleep(2.0 * 2 ** (attempt - 1))
        assert last_exc is not None
        raise last_exc

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def get_text(self, url: str, **kwargs: Any) -> str:
        return self.get(url, **kwargs).text

    def get_json(self, url: str, **kwargs: Any) -> Any:
        resp = self.get(url, headers={"Accept": "application/json"}, **kwargs)
        try:
            return resp.json()
        except ValueError as exc:
            raise FetchError(f"invalid JSON from {url}: {exc}", url=url) from exc


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return min(float(raw), MAX_RETRY_AFTER)
    except ValueError:
        return None


def _looks_like_challenge(resp: httpx.Response) -> bool:
    if resp.headers.get("cf-mitigated") == "challenge":
        return True
    if resp.status_code not in (200, 403, 429, 503):
        return False
    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype:
        return False
    head = resp.text[:20000].lower()
    if resp.status_code == 200:
        # Only the unmistakable interstitial page counts on a 200.
        return "<title>just a moment...</title>" in head
    return any(m in head for m in _CHALLENGE_MARKERS)
