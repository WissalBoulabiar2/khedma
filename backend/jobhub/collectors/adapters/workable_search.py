"""Workable job network (jobs.workable.com) - offers from every company using
the Workable ATS, filtered on a location.

robots.txt of jobs.workable.com only restricts /search pages and profiles,
and declares ``Content-Signal: search=yes`` (building a search index with
links back is permitted).
"""
from __future__ import annotations

from typing import Iterator

from ...normalize.locations import is_tunisia
from ..base import CollectContext, Collector, RawJob, register
from ..http import FetchError

API = "https://jobs.workable.com/api/v1/jobs"


@register("workable_search")
class WorkableSearchCollector(Collector):
    complete_snapshot = True

    def collect(self, ctx: CollectContext) -> Iterator[RawJob]:
        location = self.options.get("location", "Tunisia")
        max_pages = int(self.options.get("max_pages", 50))
        params: dict[str, str] = {"location": location}
        if self.options.get("query"):
            params["query"] = self.options["query"]
        token = None
        seen: set[str] = set()
        for _page in range(max_pages):
            data = ctx.fetcher.get_json(API, params={**params, **({"pageToken": token} if token else {})})
            if not isinstance(data, dict) or "jobs" not in data:
                raise FetchError("unexpected Workable search response format")
            for job in data["jobs"]:
                if job.get("id") in seen:
                    continue
                seen.add(job["id"])
                raw = to_raw(job)
                if raw is not None:
                    yield raw
            token = data.get("nextPageToken")
            if not token or not data["jobs"]:
                return
        self.run_complete = False  # stopped at max_pages


def to_raw(job: dict) -> RawJob | None:
    loc = job.get("location") or {}
    locs = job.get("locations") or []
    if is_tunisia(loc.get("countryName"), *locs) is not True:
        return None
    company = job.get("company") or {}
    sections = [job.get("description") or ""]
    if job.get("requirementsSection"):
        sections.append("<h3>Profil recherché</h3>" + job["requirementsSection"])
    if job.get("benefitsSection"):
        sections.append("<h3>Avantages</h3>" + job["benefitsSection"])
    city_bits = [b for b in (loc.get("city"), loc.get("subregion")) if b]
    return RawJob(
        external_id=job["id"],
        url=job["url"],
        apply_url=job["url"],
        title=job.get("title") or "",
        company_name=company.get("title"),
        company_website=company.get("website"),
        company_logo=company.get("image"),
        company_description=company.get("description"),
        description_html="".join(sections),
        location_raw=", ".join(city_bits + [loc.get("countryName") or ""]).strip(", "),
        city=loc.get("city") or None,
        region=loc.get("subregion"),
        country=loc.get("countryName") or "Tunisia",
        workplace_raw=job.get("workplace"),
        contract_raw=[job["employmentType"]] if job.get("employmentType") else [],
        categories_raw=[job["department"]] if job.get("department") else [],
        published_at=job.get("created"),
        language=job.get("language"),
    )
