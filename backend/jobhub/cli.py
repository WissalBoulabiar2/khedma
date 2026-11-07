"""Command line: python -m jobhub.cli <command>

  init-db                     create tables and the search index
  sources                     list sources and their health
  collect [-s KEY ...] [--force]
                              run a collection now (all enabled sources by default)
  schedule                    run collections every JOBHUB_COLLECT_INTERVAL_HOURS (blocking)
  stats                       catalogue statistics
"""
from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import func, select


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobhub")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sub.add_parser("sources")
    c = sub.add_parser("collect")
    c.add_argument("-s", "--source", action="append", help="source key (repeatable)")
    c.add_argument("--force", action="store_true", help="ignore failure backoff")
    sub.add_parser("schedule")
    sub.add_parser("stats")
    b = sub.add_parser("build-static", help="export the site as static files")
    b.add_argument("--out", default="_site")
    b.add_argument("--base-path", default="", help="URL prefix, e.g. /my-repo for a GitHub Pages project site")
    b.add_argument("--site-url", default="", help="public URL (canonical links, sitemap)")
    e = sub.add_parser("export-db", help="write a compact single-file copy of the database")
    e.add_argument("path")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    from .db import init_db, session_scope
    from .models import CollectionRun, Company, Job, Listing, Source
    init_db()

    if args.cmd == "init-db":
        print("database ready")
    elif args.cmd == "sources":
        from .sources import load_sources, sync_sources
        configs = load_sources()
        with session_scope() as s:
            sync_sources(s, configs)
            for cfg in configs:
                src = s.get(Source, cfg.key)
                active = s.scalar(select(func.count()).select_from(Listing).where(
                    Listing.source_key == cfg.key, Listing.is_active.is_(True)))
                state = "enabled " if cfg.enabled else "disabled"
                print(f"{cfg.key:26} {cfg.type:17} {state} active={active:<5} last={src.last_status or '-':8} "
                      f"{(src.last_error or src.disabled_reason or '')[:90]}")
    elif args.cmd == "collect":
        from .pipeline import run_all
        runs = run_all(only=args.source, force=args.force)
        if runs is None:
            print("another collection is already running", file=sys.stderr)
            return 1
        for r in runs:
            print(f"{r.source_key:26} {r.status:8} seen={r.seen} new={r.created} merged={r.merged} "
                  f"updated={r.updated} rejected={r.rejected} closed={r.closed} errors={r.item_errors} "
                  f"requests={r.requests} {(r.error or '')[:120]}")
        return 0 if all(r.status in ("success", "partial", "skipped") for r in runs) else 2
    elif args.cmd == "schedule":
        from .scheduler import run_forever
        run_forever()
    elif args.cmd == "build-static":
        from pathlib import Path
        from .static_site import build_static
        with session_scope() as s:
            print(build_static(s, Path(args.out), args.base_path, args.site_url))
    elif args.cmd == "export-db":
        from pathlib import Path
        from .db import engine
        target = Path(args.path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        # VACUUM INTO folds the WAL in and produces one clean, compact file.
        # It can't run inside a transaction, so use the raw (autocommit) connection.
        raw = engine().raw_connection()
        try:
            raw.cursor().execute("VACUUM INTO ?", (target.as_posix(),))
        finally:
            raw.close()
        print(f"exported {target} ({target.stat().st_size // 1024} KiB)")
    elif args.cmd == "stats":
        with session_scope() as s:
            print("active jobs   ", s.scalar(select(func.count()).select_from(Job).where(Job.is_active.is_(True))))
            print("all jobs      ", s.scalar(select(func.count()).select_from(Job)))
            print("listings      ", s.scalar(select(func.count()).select_from(Listing)))
            print("companies     ", s.scalar(select(func.count()).select_from(Company)))
            print("hiring now    ", s.scalar(select(func.count()).select_from(Company).where(Company.active_jobs_count > 0)))
            print("runs          ", s.scalar(select(func.count()).select_from(CollectionRun)))
            for dom, n in s.execute(select(Job.domain, func.count()).where(Job.is_active.is_(True))
                                    .group_by(Job.domain).order_by(func.count().desc())).all():
                print(f"  {dom:30} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
