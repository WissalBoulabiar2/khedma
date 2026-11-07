"""Public job-board APIs of applicant tracking systems.

Each source is one company board (``board`` option).  Boards list offers
worldwide, so only offers located in Tunisia are kept.  These APIs are the
documented, unauthenticated endpoints ATS vendors provide for career sites.
"""
from __future__ import annotations

import html
from typing import Iterator

from ...normalize.locations import is_tunisia
from ..base import CollectContext, Collector, RawJob, SeenListing, register
from ..http import FetchError


def _tn(*texts: str | None, country: str | None = None) -> bool:
    if country and is_tunisia(country) is True:
        return True
    return any(is_tunisia(None, t) is True for t in texts if t)


class _BoardCollector(Collector):
    complete_snapshot = True

    def __init__(self, key, name, options=None):
        super().__init__(key, name, options)
        if not self.options.get("board"):
            raise ValueError(f"source {key}: option 'board' is required")
        self.board = str(self.options["board"])
        self.company = self.options.get("company") or name

    def collect(self, ctx: CollectContext) -> Iterator[RawJob]:  # pragma: no cover - abstract
        raise NotImplementedError


@register("greenhouse")
class GreenhouseCollector(_BoardCollector):
    def collect(self, ctx: CollectContext) -> Iterator[RawJob]:
        data = ctx.fetcher.get_json(f"https://boards-api.greenhouse.io/v1/boards/{self.board}/jobs", params={"content": "true"})
        if "jobs" not in data:
            raise FetchError("unexpected Greenhouse response format")
        for j in data["jobs"]:
            loc = (j.get("location") or {}).get("name") or ""
            offices = [o.get("location") or o.get("name") or "" for o in j.get("offices") or []]
            if not _tn(loc, *offices):
                continue
            meta = {m.get("name", "").lower(): m.get("value") for m in j.get("metadata") or [] if isinstance(m, dict)}
            yield RawJob(
                external_id=str(j["id"]),
                url=j["absolute_url"],
                apply_url=j["absolute_url"],
                title=j["title"],
                company_name=self.company,
                company_website=self.options.get("website"),
                description_html=html.unescape(j.get("content") or ""),
                location_raw=loc or ", ".join(offices),
                country=None,
                workplace_raw=str(meta.get("workplace type") or meta.get("location type") or "") or None,
                contract_raw=[str(v) for k, v in meta.items() if v and ("employment" in k or "contract" in k)],
                categories_raw=[d.get("name", "") for d in j.get("departments") or []],
                published_at=j.get("first_published") or j.get("updated_at"),
            )


@register("lever")
class LeverCollector(_BoardCollector):
    def collect(self, ctx: CollectContext) -> Iterator[RawJob]:
        data = ctx.fetcher.get_json(f"https://api.lever.co/v0/postings/{self.board}", params={"mode": "json"})
        if not isinstance(data, list):
            raise FetchError("unexpected Lever response format")
        for j in data:
            cats = j.get("categories") or {}
            locs = [cats.get("location") or ""] + list(cats.get("allLocations") or [])
            if not _tn(*locs, country=j.get("country")):
                continue
            # Multi-location offers: keep the Tunisian location, not the primary one.
            tn_loc = next((l for l in locs if l and is_tunisia(None, l) is True), None)
            primary_tn = is_tunisia(j.get("country")) is True
            parts = [j.get("description") or ""]
            for lst in j.get("lists") or []:
                parts.append(f"<h3>{html.escape(lst.get('text', ''))}</h3><ul>{lst.get('content', '')}</ul>")
            if j.get("additional"):
                parts.append(j["additional"])
            yield RawJob(
                external_id=j["id"],
                url=j["hostedUrl"],
                apply_url=j.get("applyUrl") or j["hostedUrl"],
                title=j["text"],
                company_name=self.company,
                company_website=self.options.get("website"),
                description_html="".join(parts),
                location_raw=cats.get("location") if primary_tn or not tn_loc else tn_loc,
                country=j.get("country") if primary_tn or not tn_loc else "TN",
                workplace_raw=j.get("workplaceType"),
                contract_raw=[cats["commitment"]] if cats.get("commitment") else [],
                categories_raw=[c for c in (cats.get("team"), cats.get("department")) if c],
                published_at=j.get("createdAt"),
            )


_RECRUITEE_EMPLOYMENT = {
    "fulltime_permanent": "CDI", "parttime_permanent": "CDI temps partiel", "fulltime_fixed_term": "CDD",
    "parttime_fixed_term": "CDD temps partiel", "internship": "Stage", "freelance": "Freelance",
    "traineeship": "Stage", "apprenticeship": "Alternance", "temporary": "CDD",
}
_RECRUITEE_EXPERIENCE = {"student_college": "Débutant", "entry_level": "Débutant", "mid_level": "Confirmé",
                         "experienced": "Confirmé", "manager": "Senior", "senior_level": "Senior", "executive": "Expert"}


@register("recruitee")
class RecruiteeCollector(_BoardCollector):
    def collect(self, ctx: CollectContext) -> Iterator[RawJob]:
        data = ctx.fetcher.get_json(f"https://{self.board}.recruitee.com/api/offers/")
        if "offers" not in data:
            raise FetchError("unexpected Recruitee response format")
        for j in data["offers"]:
            locs = j.get("locations") or []
            tn_locs = [l for l in locs if is_tunisia(l.get("country_code") or l.get("country")) is True]
            if not tn_locs and not _tn(j.get("location"), country=j.get("country_code") or j.get("country")):
                continue
            first = tn_locs[0] if tn_locs else {}
            workplace = "remote" if j.get("remote") else "hybrid" if j.get("hybrid") else "on_site" if j.get("on_site") else None
            desc = (j.get("description") or "") + (("<h3>Profil recherché</h3>" + j["requirements"]) if j.get("requirements") else "")
            yield RawJob(
                external_id=str(j["id"]),
                url=j.get("careers_url") or f"https://{self.board}.recruitee.com/o/{j.get('slug')}",
                apply_url=j.get("careers_apply_url") or j.get("careers_url"),
                title=j["title"],
                company_name=j.get("company_name") or self.company,
                company_website=self.options.get("website"),
                description_html=desc,
                location_raw=first.get("city") and f"{first.get('city')}, {first.get('state') or ''}" or j.get("location"),
                city=first.get("city") or j.get("city"),
                region=first.get("state") or j.get("state_name"),
                country=first.get("country_code") or j.get("country_code") or "TN",
                workplace_raw=workplace,
                contract_raw=[_RECRUITEE_EMPLOYMENT.get(j.get("employment_type_code") or "", "")] if j.get("employment_type_code") else [],
                experience_raw=_RECRUITEE_EXPERIENCE.get(j.get("experience_code") or ""),
                education_raw=j.get("education_code"),
                categories_raw=[j["department"]] if j.get("department") else [],
                published_at=j.get("published_at") or j.get("created_at"),
                expires_at=j.get("close_at"),
            )


@register("ashby")
class AshbyCollector(_BoardCollector):
    def collect(self, ctx: CollectContext) -> Iterator[RawJob]:
        data = ctx.fetcher.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{self.board}")
        if "jobs" not in data:
            raise FetchError("unexpected Ashby response format")
        for j in data["jobs"]:
            addr = ((j.get("address") or {}).get("postalAddress") or {})
            secondary = [s.get("location", "") for s in j.get("secondaryLocations") or []]
            if not _tn(j.get("location"), *secondary, country=addr.get("addressCountry")):
                continue
            yield RawJob(
                external_id=j["id"],
                url=j["jobUrl"],
                apply_url=j.get("applyUrl") or j["jobUrl"],
                title=j["title"],
                company_name=self.company,
                company_website=self.options.get("website"),
                description_html=j.get("descriptionHtml"),
                description_text=j.get("descriptionPlain"),
                location_raw=j.get("location"),
                city=addr.get("addressLocality"),
                region=addr.get("addressRegion"),
                country=addr.get("addressCountry") or None,
                workplace_raw=j.get("workplaceType") or ("remote" if j.get("isRemote") else None),
                contract_raw=[j["employmentType"]] if j.get("employmentType") else [],
                categories_raw=[c for c in (j.get("department"), j.get("team")) if c],
                published_at=j.get("publishedAt"),
            )


@register("smartrecruiters")
class SmartRecruitersCollector(_BoardCollector):
    """Note: api.smartrecruiters.com's robots.txt currently disallows all
    agents but LinkedInBot, so the polite fetcher will refuse these requests
    and the run is reported as blocked.  Kept for when that changes."""

    def collect(self, ctx: CollectContext) -> Iterator[RawJob | SeenListing]:
        base = f"https://api.smartrecruiters.com/v1/companies/{self.board}/postings"
        offset = 0
        while True:
            data = ctx.fetcher.get_json(base, params={"country": "tn", "limit": 100, "offset": offset})
            content = data.get("content") or []
            for p in content:
                if p["id"] in ctx.known_ids:
                    yield SeenListing(p["id"])
                    continue
                if not ctx.can_fetch_detail():
                    continue
                ctx.note_detail()
                d = ctx.fetcher.get_json(f"{base}/{p['id']}")
                secs = ((d.get("jobAd") or {}).get("sections") or {})
                desc = "".join(
                    (f"<h3>{html.escape(s.get('title') or '')}</h3>" if s.get("title") else "") + (s.get("text") or "")
                    for k in ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
                    if (s := secs.get(k)))
                loc = p.get("location") or {}
                yield RawJob(
                    external_id=p["id"],
                    url=d.get("postingUrl") or f"https://jobs.smartrecruiters.com/{self.board}/{p['id']}",
                    apply_url=d.get("applyUrl"),
                    title=p["name"],
                    company_name=(p.get("company") or {}).get("name") or self.company,
                    description_html=desc,
                    location_raw=", ".join(x for x in (loc.get("city"), loc.get("region")) if x),
                    city=loc.get("city"), region=loc.get("region"), country=loc.get("country"),
                    workplace_raw="remote" if loc.get("remote") else ("hybrid" if loc.get("hybrid") else None),
                    contract_raw=[(p.get("typeOfEmployment") or {}).get("label") or ""],
                    experience_raw=(p.get("experienceLevel") or {}).get("label"),
                    categories_raw=[(p.get("function") or {}).get("label") or ""],
                    published_at=p.get("releasedDate"),
                )
            offset += len(content)
            if not content or offset >= int(data.get("totalFound") or 0):
                return


@register("workable_account")
class WorkableAccountCollector(_BoardCollector):
    def collect(self, ctx: CollectContext) -> Iterator[RawJob]:
        data = ctx.fetcher.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{self.board}", params={"details": "true"})
        if "jobs" not in data:
            raise FetchError("unexpected Workable widget response format")
        for j in data["jobs"]:
            if not _tn(j.get("city"), j.get("state"), country=j.get("country")):
                continue
            yield RawJob(
                external_id=j.get("shortcode") or j["url"],
                url=j["url"],
                apply_url=j.get("application_url") or j["url"],
                title=j["title"],
                company_name=data.get("name") or self.company,
                company_website=self.options.get("website"),
                description_html=j.get("description"),
                location_raw=", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x),
                city=j.get("city"), region=j.get("state"), country=j.get("country"),
                workplace_raw="remote" if j.get("telecommuting") else None,
                contract_raw=[j["employment_type"]] if j.get("employment_type") else [],
                experience_raw=j.get("experience"),
                categories_raw=[j["department"]] if j.get("department") else [],
                published_at=j.get("published_on") or j.get("created_at"),
            )
