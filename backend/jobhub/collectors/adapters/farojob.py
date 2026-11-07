"""Farojob.net - Tunisian job board (WordPress / JobMonster theme).

Pages carry no JobPosting markup, so offers are read from the public listing
pages (``/jobs/``, ``/jobs/page/N/``) and detail pages.  The RSS feed exists
but ``*/feed`` is disallowed by robots.txt, so it is not used.
"""
from __future__ import annotations

import re
from typing import Iterator

import lxml.html

from ...normalize import split_recruits_title
from ..base import CollectContext, Collector, RawJob, SeenListing, register
from ..http import FetchError, NotFound, PoliteFetcher

_JOB_LINK = re.compile(r"^https://www\.farojob\.net/jobs/(?!page/|feed/)[^/?#]+/$")


def _first_text(root, xpath: str) -> str | None:
    for el in root.xpath(xpath):
        t = " ".join(el.text_content().split())
        if t:
            return t
    return None


def parse_listing(html_text: str) -> list[str]:
    doc = lxml.html.fromstring(html_text)
    links = []
    for href in doc.xpath("//a/@href"):
        if _JOB_LINK.match(href) and href not in links:
            links.append(href)
    return links


def parse_detail(html_text: str, url: str) -> RawJob | None:
    doc = lxml.html.fromstring(html_text)
    # The offer's own metadata is in the page heading; the main column also
    # lists *related* offers with the same markup, so never search it for metadata.
    heads = doc.xpath('//header[contains(@class,"noo-page-heading")]')
    if not heads:
        return None
    main = heads[0]
    heading = _first_text(main, './/h1') or _first_text(doc, '//h1[contains(@class,"page-title")]')
    if not heading:
        return None
    desc_nodes = doc.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " noo-main ")]'
                           '//div[contains(@class,"job-desc")]')
    if not desc_nodes:
        return None
    company = _first_text(main, './/span[contains(@class,"job-company")]')
    split_company, title = split_recruits_title(heading)
    company = company or split_company
    location = _first_text(main, './/span[contains(@class,"job-location")]')
    posted = _first_text(main, './/span[contains(@class,"job-date__posted")]') or _first_text(main, ".//time")
    categories = [" ".join(e.text_content().split()) for e in main.xpath('.//span[contains(@class,"job-category")]//a')] \
        or ([c] if (c := _first_text(main, './/span[contains(@class,"job-category")]')) else [])
    job_types = [" ".join(e.text_content().split()) for e in main.xpath('.//span[contains(@class,"job-type")]')]
    logo = next(iter(main.xpath('.//span[contains(@class,"company-logo")]//img/@src')), None)
    return RawJob(
        external_id=url.rstrip("/").rsplit("/", 1)[-1],
        url=url,
        apply_url=url,
        title=title,
        company_name=company,
        company_logo=logo,
        description_html=lxml.html.tostring(desc_nodes[0], encoding="unicode"),
        location_raw=location,
        country="TN",
        contract_raw=[t for t in job_types if t and "secteur" not in t.lower()],
        categories_raw=categories,
        published_at=posted,
        language="fr",
    )


@register("farojob")
class FarojobCollector(Collector):
    complete_snapshot = False

    def collect(self, ctx: CollectContext) -> Iterator[RawJob | SeenListing]:
        base = self.options.get("base_url", "https://www.farojob.net/jobs/")
        max_pages = int(self.options.get("max_pages", 3))
        links: list[str] = []
        for page in range(1, max_pages + 1):
            url = base if page == 1 else f"{base}page/{page}/"
            try:
                page_links = parse_listing(ctx.fetcher.get_text(url))
            except NotFound:
                break
            if page == 1 and not page_links:
                raise FetchError("Farojob listing has no job links: layout may have changed")
            links.extend(l for l in page_links if l not in links)
        for url in links:
            ext = url.rstrip("/").rsplit("/", 1)[-1]
            if ext in ctx.known_ids:
                yield SeenListing(ext)
                continue
            if not ctx.can_fetch_detail():
                continue
            try:
                ctx.note_detail()
                raw = parse_detail(ctx.fetcher.get_text(url), url)
            except NotFound:
                continue
            except FetchError as exc:
                ctx.item_error(f"farojob {url}: {exc}")
                continue
            if raw is None:
                ctx.item_error(f"farojob {url}: could not parse offer page")
                continue
            yield raw

    def check_alive(self, fetcher: PoliteFetcher, url: str) -> bool | None:
        try:
            page = fetcher.get_text(url)
        except NotFound:
            return False
        except FetchError:
            return None
        return parse_detail(page, url) is not None
