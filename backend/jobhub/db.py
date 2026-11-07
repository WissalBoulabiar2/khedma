"""Engine/session setup and the SQLite full-text index."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def make_engine(url: str) -> Engine:
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        if ":memory:" not in url and url.startswith("sqlite:///"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _rec):  # pragma: no cover - trivial
            # Let SQLAlchemy own transactions so SAVEPOINTs (begin_nested) work
            # with pysqlite (see SQLAlchemy docs, "Serializable isolation / Savepoints").
            dbapi_conn.isolation_level = None
            cur = dbapi_conn.cursor()
            if ":memory:" not in url:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        @event.listens_for(engine, "begin")
        def _begin(conn):  # pragma: no cover - trivial
            conn.exec_driver_sql("BEGIN")
    return engine


def configure(url: str | None = None) -> Engine:
    global _engine, _SessionLocal
    _engine = make_engine(url or settings.database_url)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def engine() -> Engine:
    if _engine is None:
        configure()
    assert _engine is not None
    return _engine


def SessionLocal() -> Session:
    if _SessionLocal is None:
        configure()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def has_fts(eng: Engine | None = None) -> bool:
    return (eng or engine()).dialect.name == "sqlite"


def init_db(eng: Engine | None = None) -> None:
    eng = eng or engine()
    Base.metadata.create_all(eng)
    if has_fts(eng):
        with eng.begin() as conn:
            conn.execute(text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5("
                "title, company, skills, location, description, "
                "tokenize = 'unicode61 remove_diacritics 2', prefix = '2 3 4')"))


def fts_upsert(session: Session, job_id: int, *, title: str, company: str, skills: str, location: str, description: str) -> None:
    if session.get_bind().dialect.name != "sqlite":
        return
    session.execute(text("DELETE FROM jobs_fts WHERE rowid = :id"), {"id": job_id})
    session.execute(text(
        "INSERT INTO jobs_fts(rowid, title, company, skills, location, description) "
        "VALUES (:id, :title, :company, :skills, :location, :description)"),
        {"id": job_id, "title": title, "company": company, "skills": skills, "location": location,
         "description": description[:20000]})


def fts_delete(session: Session, job_id: int) -> None:
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("DELETE FROM jobs_fts WHERE rowid = :id"), {"id": job_id})
