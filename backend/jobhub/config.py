"""Runtime settings, read from environment variables (prefix JOBHUB_)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get(
        "JOBHUB_DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'data' / 'jobhub.db').as_posix()}"))
    sources_file: Path = field(default_factory=lambda: Path(os.environ.get(
        "JOBHUB_SOURCES_FILE", str(BACKEND_DIR / "sources.yaml"))))
    # Identify ourselves honestly; site owners can contact / block us by this token.
    user_agent: str = field(default_factory=lambda: os.environ.get(
        "JOBHUB_USER_AGENT", "TunisieJobsBot/1.0 (job aggregator; respects robots.txt)"))
    robots_token: str = field(default_factory=lambda: os.environ.get("JOBHUB_ROBOTS_TOKEN", "TunisieJobsBot"))
    min_request_interval: float = field(default_factory=lambda: float(os.environ.get("JOBHUB_MIN_REQUEST_INTERVAL", "1.0")))
    request_timeout: float = field(default_factory=lambda: float(os.environ.get("JOBHUB_REQUEST_TIMEOUT", "30")))
    scheduler_enabled: bool = field(default_factory=lambda: _bool("JOBHUB_SCHEDULER_ENABLED", False))
    collect_interval_hours: float = field(default_factory=lambda: float(os.environ.get("JOBHUB_COLLECT_INTERVAL_HOURS", "2")))
    admin_token: str | None = field(default_factory=lambda: os.environ.get("JOBHUB_ADMIN_TOKEN") or None)
    # A listing not confirmed by its source for this many days is considered closed
    # (used for sources that only expose their latest offers, e.g. RSS feeds).
    stale_after_days: int = field(default_factory=lambda: int(os.environ.get("JOBHUB_STALE_AFTER_DAYS", "45")))


settings = Settings()
