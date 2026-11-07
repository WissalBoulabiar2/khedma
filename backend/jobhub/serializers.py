"""Model -> JSON-ready dicts (shared by the API and templates)."""
from __future__ import annotations

from typing import Any

from .models import Company, Job, Listing
from .normalize.locations import GOVERNORATES
from .normalize.taxonomy import CONTRACT_TYPES, DOMAIN_LABELS, EXPERIENCE_LEVELS, SPECIALIZATION_LABELS, WORKPLACES
from .normalize.text import snippet


def _iso(dt) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def company_brief(c: Company | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {"id": c.id, "name": c.name, "slug": c.slug, "website": c.website, "logo_url": c.logo_url,
            "active_jobs": c.active_jobs_count}


def job_summary(j: Job) -> dict[str, Any]:
    return {
        "id": j.id,
        "slug": j.slug,
        "title": j.title,
        "company": company_brief(j.company),
        "location": j.location_label,
        "governorate": {"value": j.governorate, "label": GOVERNORATES.get(j.governorate)} if j.governorate else None,
        "city": j.city,
        "domain": {"value": j.domain, "label": DOMAIN_LABELS.get(j.domain, j.domain)},
        "specialization": {"value": j.specialization, "label": SPECIALIZATION_LABELS.get(j.specialization, j.specialization)},
        "contracts": [{"value": c, "label": CONTRACT_TYPES.get(c, c)} for c in j.contract_list],
        "experience": {"value": j.experience_level, "label": EXPERIENCE_LEVELS[j.experience_level]} if j.experience_level else None,
        "workplace": {"value": j.workplace, "label": WORKPLACES[j.workplace]} if j.workplace else None,
        "skills": j.skills or [],
        "published_at": _iso(j.published_at),
        "first_seen_at": _iso(j.first_seen_at),
        "is_active": j.is_active,
        "excerpt": snippet(j.description_text, 240),
        "url": f"/emplois/{j.id}-{j.slug}",
    }


def listing_info(l: Listing) -> dict[str, Any]:
    return {"source": {"key": l.source_key, "name": l.source.name if l.source else l.source_key},
            "url": l.url, "apply_url": l.apply_url, "is_active": l.is_active,
            "first_seen_at": _iso(l.first_seen_at), "last_seen_at": _iso(l.last_seen_at)}


def job_detail(j: Job) -> dict[str, Any]:
    d = job_summary(j)
    d.update({
        "description_html": j.description_html,
        "description_text": j.description_text,
        "experience_years": j.experience_years,
        "education": j.education,
        "expires_at": _iso(j.expires_at),
        "apply_url": j.apply_url,
        "source_url": j.source_url,
        "closed_at": _iso(j.closed_at),
        "sources": [listing_info(l) for l in sorted(j.listings, key=lambda l: l.first_seen_at)],
    })
    return d
