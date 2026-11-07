"""Database schema.

    Source 1─* Listing *─1 Job *─1 Company
    Source 1─* CollectionRun

* ``Listing``  = one offer as published by one source (external id, URL).
* ``Job``      = the deduplicated offer shown to users; it may be backed by
                 several listings (same offer on Keejob and on an ATS).
* ``Company``  = employer, merged on a normalized name key.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .normalize.text import utcnow


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(60))
    homepage: Mapped[str | None] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_status: Mapped[str | None] = mapped_column(String(20))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    next_run_after: Mapped[datetime | None] = mapped_column(DateTime)  # backoff after failures

    listings: Mapped[list["Listing"]] = relationship(back_populates="source")


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(300))
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    website: Mapped[str | None] = mapped_column(String(500))
    logo_url: Mapped[str | None] = mapped_column(String(800))
    description: Mapped[str | None] = mapped_column(Text)
    source_profile_url: Mapped[str | None] = mapped_column(String(800))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    # Denormalized for directory sorting; refreshed after each collection.
    active_jobs_count: Mapped[int] = mapped_column(Integer, default=0, index=True)

    jobs: Mapped[list["Job"]] = relationship(back_populates="company")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(300))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), index=True)
    description_html: Mapped[str | None] = mapped_column(Text)
    description_text: Mapped[str] = mapped_column(Text, default="")
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    domain: Mapped[str] = mapped_column(String(60), index=True)
    specialization: Mapped[str] = mapped_column(String(60), index=True)
    governorate: Mapped[str | None] = mapped_column(String(40), index=True)
    city: Mapped[str | None] = mapped_column(String(80), index=True)
    location_label: Mapped[str] = mapped_column(String(200))
    workplace: Mapped[str | None] = mapped_column(String(20), index=True)
    # Pipe-delimited ("|cdi|sivp|") so a single LIKE filter works on any SQL backend.
    contracts: Mapped[str] = mapped_column(String(120), default="")
    experience_level: Mapped[str | None] = mapped_column(String(20), index=True)
    experience_years: Mapped[float | None] = mapped_column(Float)
    education: Mapped[str | None] = mapped_column(String(200))
    published_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    apply_url: Mapped[str] = mapped_column(String(1000))
    source_url: Mapped[str] = mapped_column(String(1000))
    fingerprint: Mapped[str] = mapped_column(String(40), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    company: Mapped[Company | None] = relationship(back_populates="jobs")
    listings: Mapped[list["Listing"]] = relationship(back_populates="job")

    __table_args__ = (Index("ix_jobs_active_published", "is_active", "published_at"),)

    @property
    def contract_list(self) -> list[str]:
        return [c for c in (self.contracts or "").split("|") if c]

    @property
    def sort_date(self) -> datetime:
        return self.published_at or self.first_seen_at


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(ForeignKey("sources.key"), index=True)
    external_id: Mapped[str] = mapped_column(String(300))
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    apply_url: Mapped[str | None] = mapped_column(String(1000))
    content_hash: Mapped[str] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    close_reason: Mapped[str | None] = mapped_column(String(40))
    raw: Mapped[dict | None] = mapped_column(JSON)

    source: Mapped[Source] = relationship(back_populates="listings")
    job: Mapped[Job] = relationship(back_populates="listings")

    __table_args__ = (UniqueConstraint("source_key", "external_id", name="uq_listing_source_ext"),)


class CollectionRun(Base):
    __tablename__ = "collection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(ForeignKey("sources.key"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|success|partial|failed|blocked|skipped
    seen: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    merged: Mapped[int] = mapped_column(Integer, default=0)      # new listing attached to an existing job (duplicate)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    closed: Mapped[int] = mapped_column(Integer, default=0)
    item_errors: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    error_samples: Mapped[list | None] = mapped_column(JSON)
