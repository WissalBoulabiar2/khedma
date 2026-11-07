"""Collector contract.

A *collector type* (adapter) knows how to read one kind of source: a job
board, an ATS API, an RSS feed, schema.org markup...  A *source* is a
configured instance of a type (sources.yaml), e.g. ``lever`` + ``Yassir``.

Adding a source of an existing type = a few lines of YAML.
Adding a new type = subclass ``Collector`` and decorate with ``@register``.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, ClassVar, Iterator

from .http import PoliteFetcher

log = logging.getLogger(__name__)


@dataclass
class RawJob:
    """A job offer as read from a source, before normalization."""

    external_id: str
    url: str
    title: str
    company_name: str | None = None
    apply_url: str | None = None
    description_html: str | None = None
    description_text: str | None = None
    company_website: str | None = None
    company_logo: str | None = None
    company_description: str | None = None
    company_source_url: str | None = None
    location_raw: str | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None          # ISO code or name; None = unknown
    workplace_raw: str | None = None    # "remote", "Hybride", "on_site"...
    contract_raw: list[str] = field(default_factory=list)
    experience_raw: str | None = None
    education_raw: str | None = None
    categories_raw: list[str] = field(default_factory=list)
    skills_raw: list[str] = field(default_factory=list)
    published_at: datetime | date | None = None
    expires_at: datetime | date | None = None
    language: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SeenListing:
    """The source still lists this offer, but we already have its details."""

    external_id: str


@dataclass
class CollectContext:
    fetcher: PoliteFetcher
    known_ids: frozenset[str] = frozenset()
    # Company profile pages (on this source) we already have details for.
    known_company_profiles: frozenset[str] = frozenset()
    # Cap on detail-page fetches per run so one large source can't stall the
    # whole cycle; the remainder is picked up on the next run.
    max_details: int = 300
    item_errors: list[str] = field(default_factory=list)
    details_fetched: int = 0

    def item_error(self, message: str) -> None:
        log.warning(message)
        self.item_errors.append(message)

    def can_fetch_detail(self) -> bool:
        return self.details_fetched < self.max_details

    def note_detail(self) -> None:
        self.details_fetched += 1


class Collector(ABC):
    type: ClassVar[str]
    #: True when one collect() returns *every* active offer of the source, so
    #: offers missing from a successful run can be marked closed.
    complete_snapshot: ClassVar[bool] = False

    def __init__(self, key: str, name: str, options: dict[str, Any] | None = None):
        self.key = key
        self.name = name
        self.options = options or {}
        # A snapshot collector sets this to False when it stopped early
        # (page limit...), so missing offers are not wrongly closed.
        self.run_complete = True

    @abstractmethod
    def collect(self, ctx: CollectContext) -> Iterator[RawJob | SeenListing]:
        """Yield offers. Must raise on source-level failure (not swallow it)."""

    def check_alive(self, fetcher: PoliteFetcher, url: str) -> bool | None:
        """Optional liveness check for one listing. None = unknown."""
        return None


_REGISTRY: dict[str, type[Collector]] = {}


def register(type_name: str):
    def deco(cls: type[Collector]) -> type[Collector]:
        cls.type = type_name
        _REGISTRY[type_name] = cls
        return cls
    return deco


def collector_types() -> dict[str, type[Collector]]:
    from . import adapters  # noqa: F401  (import side effect: registration)
    return dict(_REGISTRY)


def build_collector(type_name: str, key: str, name: str, options: dict[str, Any]) -> Collector:
    types = collector_types()
    if type_name not in types:
        raise KeyError(f"unknown collector type '{type_name}'. Known: {sorted(types)}")
    return types[type_name](key, name, options)
