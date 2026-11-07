"""RawJob -> NormalizedJob."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..collectors.base import RawJob
from .locations import GOVERNORATES, foreign_country_in, is_tunisia, resolve_location
from .skills import extract_skills
from .taxonomy import classify, normalize_contracts, normalize_workplace, parse_experience
from .text import clean_line, fold, html_to_text, parse_date, sanitize_html, text_to_html, utcnow

_LEGAL_FORMS = re.compile(
    r"\b(s\.?a\.?r\.?l|s\.?u\.?a\.?r\.?l|s\.?a\.?s|s\.?a|sarl|suarl|sas|sa|ltd|llc|inc|gmbh|plc|corp|co|company|"
    r"groupe|group|holding|tunisie|tunisia|tunis|international|intl)\b\.?")
_GENDER = re.compile(r"\(?\b(h/f|f/h|m/f|f/m|m/w|h/f/x|m/f/d|f/h/x)\b\)?|\(e\)|\(s\)|\(ne\)", re.I)


class Rejected(Exception):
    """The offer is not suitable for the catalogue (reason in message)."""


@dataclass
class NormalizedJob:
    title: str
    company_name: str | None
    company_key: str | None
    description_html: str | None
    description_text: str
    skills: list[str]
    domain: str
    specialization: str
    governorate: str | None
    city: str | None
    location_label: str
    workplace: str | None
    contracts: list[str]
    experience_level: str | None
    experience_years: float | None
    education: str | None
    published_at: datetime | None
    expires_at: datetime | None
    url: str
    apply_url: str
    fingerprint: str
    content_hash: str
    company_website: str | None = None
    company_logo: str | None = None
    company_description: str | None = None
    company_source_url: str | None = None
    extra: dict = field(default_factory=dict)


def company_key(name: str | None) -> str | None:
    if not name:
        return None
    f = fold(name).replace("&", " and ")
    f = _LEGAL_FORMS.sub(" ", f)
    f = re.sub(r"[^a-z0-9]+", " ", f).strip()
    return f or fold(name)


def title_key(title: str) -> str:
    t = _GENDER.sub(" ", title)
    t = fold(t)
    t = re.sub(r"[^a-z0-9+#]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def clean_title(title: str) -> str:
    t = clean_line(title) or ""
    t = re.sub(r"\s*[-–|]\s*(ref|réf)\.?\s*[:#]?\s*[\w-]+\s*$", "", t, flags=re.I)
    if t.isupper() and len(t) > 6:
        t = t.capitalize()
    return t.strip(" -–|,")


_RECRUTE = re.compile(r"^(?P<company>.{2,90}?)\s+(?:recrute(?:nt)?|is hiring|is looking for|cherche|recherche)\s*:?\s+(?P<title>.+)$", re.I)


def split_recruits_title(text: str) -> tuple[str | None, str]:
    """'Zitouna Tamkeen recrute Chef Service Reporting' -> ('Zitouna Tamkeen', 'Chef Service Reporting')."""
    t = clean_line(text) or ""
    m = _RECRUTE.match(t)
    if not m:
        return None, t
    company = m.group("company").strip(" -–:")
    title = m.group("title").strip(" -–:")
    title = re.sub(r"^(?:un|une|des|plusieurs|deux|trois|\d+)\s+", "", title, flags=re.I)
    return company, title[:1].upper() + title[1:]


def fingerprint(company: str | None, title: str, governorate: str | None) -> str:
    base = "|".join([company_key(company) or "", title_key(title), governorate or ""])
    return hashlib.sha1(base.encode()).hexdigest()


def normalize(raw: RawJob, *, now: datetime | None = None) -> NormalizedJob:
    now = now or utcnow()
    title = clean_title(raw.title)
    if len(title) < 3:
        raise Rejected("missing title")
    if not raw.url:
        raise Rejected("missing source URL")

    in_tn = is_tunisia(raw.country, raw.location_raw, raw.city, raw.region)
    if in_tn is not True:
        raise Rejected(f"not located in Tunisia (country={raw.country!r}, location={raw.location_raw!r})")
    # Tunisian boards also carry offers abroad ("Nouadhibou, Mauritanie").
    if foreign_country_in(raw.location_raw) and not is_tunisia(None, raw.location_raw):
        raise Rejected(f"located abroad: {raw.location_raw!r}")

    desc_html = sanitize_html(raw.description_html) if raw.description_html else text_to_html(raw.description_text)
    desc_text = html_to_text(desc_html) if desc_html else (raw.description_text or "").strip()

    loc = resolve_location(raw.city, raw.location_raw, raw.region)
    gov, city = loc.governorate, loc.city
    label_parts = [p for p in [city, GOVERNORATES.get(gov) if gov else None] if p]
    if city and gov and fold(city) == fold(GOVERNORATES[gov]):
        label_parts = [city]
    location_label = ", ".join(label_parts) or (clean_line(raw.location_raw) or "Tunisie")

    cls = classify(title, raw.categories_raw, desc_text)
    workplace = normalize_workplace(raw.workplace_raw, raw.title, raw.location_raw)
    contracts = normalize_contracts(raw.contract_raw)
    if not contracts:
        # Title: "Stage PFE ...", "CDD 6 mois".
        contracts = [c for c in normalize_contracts(raw.title) if c in ("stage", "sivp", "cdd", "cdi", "freelance", "alternance")]
    if not contracts and desc_text:
        # Description: only the unambiguous Tunisian contract acronyms.
        contracts = [c for c in normalize_contracts(desc_text[:4000]) if c in ("cdi", "cdd", "sivp")]
    level, years = parse_experience(raw.experience_raw, title, desc_text)
    if "stage" in contracts and level is None:
        level = "debutant"

    published = parse_date(raw.published_at)
    if published and published > now + timedelta(days=1):
        published = now
    expires = parse_date(raw.expires_at)
    if expires and (expires.hour, expires.minute, expires.second) == (0, 0, 0):
        expires = expires.replace(hour=23, minute=59, second=59)  # a date-only deadline lasts all day

    company = clean_line(raw.company_name)
    if company and fold(company) in {"confidentiel", "confidential", "entreprise confidentielle", "societe confidentielle", "anonyme"}:
        company = None

    skills = extract_skills(title, desc_text, raw.skills_raw)
    apply_url = raw.apply_url or raw.url
    content_hash = hashlib.sha1("\x1f".join([
        title, company or "", desc_text, location_label, ",".join(contracts), str(expires or ""),
    ]).encode()).hexdigest()

    return NormalizedJob(
        title=title,
        company_name=company,
        company_key=company_key(company),
        description_html=desc_html,
        description_text=desc_text,
        skills=skills,
        domain=cls.domain,
        specialization=cls.specialization,
        governorate=gov,
        city=city,
        location_label=location_label,
        workplace=workplace,
        contracts=contracts,
        experience_level=level,
        experience_years=years,
        education=clean_line(raw.education_raw),
        published_at=published,
        expires_at=expires,
        url=raw.url,
        apply_url=apply_url,
        fingerprint=fingerprint(company, title, gov),
        content_hash=content_hash,
        company_website=raw.company_website,
        company_logo=raw.company_logo,
        company_description=raw.company_description,
        company_source_url=raw.company_source_url,
        extra=raw.extra,
    )
