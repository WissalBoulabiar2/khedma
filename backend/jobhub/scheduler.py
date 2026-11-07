"""Periodic collection (every JOBHUB_COLLECT_INTERVAL_HOURS, default 2h).

Two ways to run it:
* inside the web process: set JOBHUB_SCHEDULER_ENABLED=1 (see main.py);
* as a dedicated worker: ``python -m jobhub.cli schedule`` (recommended in
  production, so web workers stay stateless).
A file lock (pipeline.RunLock) prevents overlapping runs in both cases.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler

from .config import settings

log = logging.getLogger(__name__)


def _job() -> None:
    from .pipeline import run_all
    try:
        runs = run_all()
        if runs is not None:
            log.info("scheduled collection done: %s", {r.source_key: r.status for r in runs})
    except Exception:  # never let the scheduler thread die
        log.exception("scheduled collection crashed")


def _add(scheduler, first_run_delay: timedelta) -> None:
    scheduler.add_job(_job, "interval", hours=settings.collect_interval_hours, id="collect",
                      next_run_time=datetime.now() + first_run_delay, max_instances=1, coalesce=True,
                      misfire_grace_time=3600)


def start_background(first_run_delay: timedelta = timedelta(seconds=30)) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(daemon=True)
    _add(scheduler, first_run_delay)
    scheduler.start()
    log.info("collection scheduler started (every %sh)", settings.collect_interval_hours)
    return scheduler


def run_forever() -> None:
    scheduler = BlockingScheduler()
    _add(scheduler, timedelta(seconds=1))
    log.info("collection worker started (every %sh)", settings.collect_interval_hours)
    scheduler.start()
