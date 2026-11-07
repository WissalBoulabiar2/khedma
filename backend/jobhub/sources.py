"""Source registry: sources.yaml -> validated configs + DB rows."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from .collectors.base import Collector, build_collector, collector_types
from .config import settings
from .models import Source


@dataclass
class SourceConfig:
    key: str
    name: str
    type: str
    homepage: str | None = None
    enabled: bool = True
    disabled_reason: str | None = None
    notes: str | None = None
    max_details: int = 300
    options: dict[str, Any] = field(default_factory=dict)

    def build(self) -> Collector:
        return build_collector(self.type, self.key, self.name, self.options)


def load_sources(path: Path | None = None) -> list[SourceConfig]:
    path = path or settings.sources_file
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    known_types = collector_types()
    out: list[SourceConfig] = []
    keys: set[str] = set()
    for i, entry in enumerate(data.get("sources") or []):
        for req in ("key", "name", "type"):
            if not entry.get(req):
                raise ValueError(f"sources[{i}]: missing '{req}'")
        if entry["key"] in keys:
            raise ValueError(f"duplicate source key '{entry['key']}'")
        if entry["type"] not in known_types:
            raise ValueError(f"source '{entry['key']}': unknown type '{entry['type']}'")
        keys.add(entry["key"])
        cfg = SourceConfig(
            key=entry["key"], name=entry["name"], type=entry["type"], homepage=entry.get("homepage"),
            enabled=bool(entry.get("enabled", True)),
            disabled_reason=(entry.get("disabled_reason") or "").strip() or None,
            notes=(entry.get("notes") or "").strip() or None,
            max_details=int(entry.get("max_details", 300)), options=entry.get("options") or {},
        )
        cfg.build()  # validates options early
        out.append(cfg)
    return out


def sync_sources(session: Session, configs: list[SourceConfig]) -> None:
    """Mirror the registry into the sources table (history rows are kept)."""
    existing = {s.key: s for s in session.query(Source).all()}
    for cfg in configs:
        row = existing.get(cfg.key) or Source(key=cfg.key, consecutive_failures=0)
        row.name, row.type, row.homepage = cfg.name, cfg.type, cfg.homepage
        row.enabled = cfg.enabled
        row.disabled_reason = None if cfg.enabled else (cfg.disabled_reason or "Désactivée")
        session.add(row)
    for key, row in existing.items():
        if key not in {c.key for c in configs}:
            row.enabled = False
            row.disabled_reason = "Removed from the registry"
    session.flush()
