"""robots.txt parsing and matching (RFC 9309).

Python's urllib.robotparser ignores the `*` and `$` wildcards used by most job
boards (e.g. ``Disallow: /offres-emploi/*/postuler/``), which would make us
*more* permissive than the site intends.  This implementation supports them,
picks the most specific user-agent group and applies longest-match precedence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit


@dataclass
class _Rule:
    allow: bool
    pattern: str
    regex: re.Pattern[str]

    @property
    def specificity(self) -> int:
        return len(self.pattern)


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    parts = [re.escape(p) for p in pattern.split("*")]
    return re.compile("^" + ".*".join(parts) + ("$" if anchored else ""), re.S)


def _norm(path: str) -> str:
    # Compare decoded paths so that "%C3%A9" and "é" match the same rule.
    try:
        return unquote(path)
    except Exception:  # pragma: no cover - defensive
        return path


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[_Rule] = field(default_factory=list)
    crawl_delay: float | None = None


class RobotsPolicy:
    """Parsed robots.txt for one host."""

    def __init__(self, groups: list[_Group], allow_all: bool = False, deny_all: bool = False):
        self._groups = groups
        self.allow_all = allow_all
        self.deny_all = deny_all

    # -- construction -------------------------------------------------
    @classmethod
    def parse(cls, text: str) -> "RobotsPolicy":
        groups: list[_Group] = []
        current: _Group | None = None
        last_was_agent = False
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if current is None or not last_was_agent:
                    current = _Group()
                    groups.append(current)
                current.agents.append(value.lower())
                last_was_agent = True
                continue
            last_was_agent = False
            if current is None:
                continue
            if key in ("allow", "disallow"):
                if key == "disallow" and value == "":
                    continue  # empty Disallow == allow everything
                if not value.startswith("/") and not value.startswith("*"):
                    value = "/" + value
                current.rules.append(_Rule(key == "allow", value, _compile(_norm(value))))
            elif key == "crawl-delay":
                try:
                    current.crawl_delay = float(value)
                except ValueError:
                    pass
        return cls(groups)

    @classmethod
    def allowing_all(cls) -> "RobotsPolicy":
        return cls([], allow_all=True)

    @classmethod
    def denying_all(cls) -> "RobotsPolicy":
        return cls([], deny_all=True)

    # -- queries ------------------------------------------------------
    def _groups_for(self, token: str) -> list[_Group]:
        token = token.lower()
        specific = [g for g in self._groups if any(a != "*" and a in token for a in g.agents)]
        if specific:
            return specific
        return [g for g in self._groups if "*" in g.agents]

    def can_fetch(self, token: str, url: str) -> bool:
        if self.deny_all:
            return False
        if self.allow_all:
            return True
        parts = urlsplit(url)
        path = _norm((parts.path or "/") + (f"?{parts.query}" if parts.query else ""))
        if path == "/robots.txt":
            return True
        best: _Rule | None = None
        for group in self._groups_for(token):
            for rule in group.rules:
                if rule.regex.match(path):
                    if (best is None or rule.specificity > best.specificity
                            or (rule.specificity == best.specificity and rule.allow)):
                        best = rule
        return True if best is None else best.allow

    def crawl_delay(self, token: str) -> float | None:
        for group in self._groups_for(token):
            if group.crawl_delay is not None:
                return group.crawl_delay
        return None
