"""Generic collector for pages carrying schema.org ``JobPosting`` markup.

Google for Jobs requires this markup, so most job boards and many company
career sites publish it.  One collector type therefore covers any such site;
a new source is only configuration:

    type: jsonld
    options:
      feed_url: https://example.tn/feed/          # or sitemap_url / listing_urls
      url_pattern: "/jobs/"                       # which discovered links are offers
      content_xpath: '//div[@class="job-body"]'   # optional: full text if JSON-LD is truncated
      country: TN                                 # default country for Tunisian sites

Pages without a JobPosting block (news, public-sector "concours"...) are
skipped.  Feeds/sitemaps expose recent offers only, so this collector is not a
complete snapshot: offers close via ``validThrough``, liveness checks, or
staleness.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator
from urllib.parse import urljoin, urlsplit

import lxml.html
from lxml import etree

from ...normalize import split_recruits_title
from ...normalize.text import html_to_text, parse_date, utcnow
from ..base import CollectContext, Collector, RawJob, SeenListing, register
from ..http import FetchError, NotFound, PoliteFetcher

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def find_job_postings(html_text: str) -> list[dict]:
    """All JobPosting objects in a page's JSON-LD (handles @graph and arrays)."""
    doc = lxml.html.fromstring(html_text)
    found: list[dict] = []
    for script in doc.xpath('//script[contains(@type,"ld+json")]'):
        raw = (script.text or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw, strict=False)
        except ValueError:
            # Some sites emit raw newlines/control chars inside strings.
            try:
                data = json.loads(re.sub(r"[\x00-\x1f]+", " ", raw), strict=False)
            except ValueError:
                continue
        stack = _as_list(data)
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            types = [t.lower() for t in _as_list(node.get("@type")) if isinstance(t, str)]
            if "jobposting" in types:
                found.append(node)
            stack.extend(_as_list(node.get("@graph")))
    return found


def _text(v: Any) -> str | None:
    if isinstance(v, dict):
        v = v.get("name") or v.get("value") or v.get("@value")
    if isinstance(v, list):
        v = ", ".join(str(_text(x)) for x in v if _text(x))
    return str(v).strip() if v not in (None, "") else None


def posting_to_raw(p: dict, page_url: str, *, default_country: str | None, full_html: str | None,
                   site_host: str, categories: list[str] | None = None) -> RawJob:
    org = p.get("hiringOrganization") or {}
    if isinstance(org, str):
        org = {"name": org}
    locs = _as_list(p.get("jobLocation"))
    addr: dict = {}
    for loc in locs:
        a = (loc or {}).get("address") if isinstance(loc, dict) else None
        if isinstance(a, dict):
            addr = a
            break
    country = _text(addr.get("addressCountry")) or default_country
    remote = str(p.get("jobLocationType", "")).upper() == "TELECOMMUTE"
    if remote and not addr:
        reqs = [_text(x) for x in _as_list(p.get("applicantLocationRequirements"))]
        country = next((r for r in reqs if r), country)

    desc_html = p.get("description") or ""
    if full_html and len(html_to_text(full_html)) > len(html_to_text(desc_html)) * 1.1:
        desc_html = full_html

    website = _text(org.get("sameAs")) or _text(org.get("url"))
    if website and urlsplit(website).netloc.lower().removeprefix("www.") == site_host:
        website = None  # the board itself, not the employer
    logo = org.get("logo")
    logo = _text(logo.get("url") if isinstance(logo, dict) else logo)

    title = _text(p.get("title")) or ""
    company = _text(org.get("name"))
    if not company:
        company, title = split_recruits_title(title)
    exp = p.get("experienceRequirements")
    if isinstance(exp, dict):
        months = exp.get("monthsOfExperience")
        exp = f"{float(months) / 12:g} ans" if months else _text(exp.get("description"))
    edu = p.get("educationRequirements")
    url = _text(p.get("url")) or page_url
    return RawJob(
        external_id=page_url,
        url=page_url,
        apply_url=url if url != page_url else None,
        title=title,
        company_name=company,
        company_website=website,
        company_logo=logo,
        description_html=desc_html,
        location_raw=", ".join(x for x in (_text(addr.get("addressLocality")), _text(addr.get("addressRegion"))) if x) or None,
        city=_text(addr.get("addressLocality")),
        region=_text(addr.get("addressRegion")),
        country=country,
        workplace_raw="remote" if remote else None,
        contract_raw=[str(x) for x in _as_list(p.get("employmentType")) if x],
        experience_raw=_text(exp) if not isinstance(exp, str) else exp,
        education_raw=_text(edu.get("credentialCategory") if isinstance(edu, dict) else edu),
        categories_raw=[c for c in [_text(p.get("occupationalCategory")), _text(p.get("industry"))] if c] + (categories or []),
        skills_raw=[s.strip() for s in re.split(r"[,;\n]", _text(p.get("skills")) or "") if s.strip()][:30],
        published_at=p.get("datePosted"),
        expires_at=p.get("validThrough"),
    )


@register("jsonld")
class JsonLdCollector(Collector):
    complete_snapshot = False

    def __init__(self, key, name, options=None):
        super().__init__(key, name, options)
        o = self.options
        if not (o.get("feed_url") or o.get("sitemap_url") or o.get("listing_urls")):
            raise ValueError(f"source {key}: one of feed_url, sitemap_url, listing_urls is required")
        self.url_pattern = re.compile(o["url_pattern"]) if o.get("url_pattern") else None
        self.title_exclude = re.compile(o["title_exclude_pattern"], re.I) if o.get("title_exclude_pattern") else None
        self.content_xpath = o.get("content_xpath")
        self.default_country = o.get("country")
        self.max_items = int(o.get("max_items", 300))

    # -- discovery -------------------------------------------------------
    def discover(self, fetcher: PoliteFetcher) -> list[tuple[str, list[str]]]:
        """Return candidate (url, categories) pairs, newest first when known."""
        o = self.options
        out: list[tuple[str, list[str]]] = []
        if o.get("feed_url"):
            root = etree.fromstring(fetcher.get(o["feed_url"]).content)
            for item in root.iter("item", "{http://www.w3.org/2005/Atom}entry"):
                link = item.findtext("link") or ""
                if not link:
                    el = item.find("{http://www.w3.org/2005/Atom}link")
                    link = el.get("href") if el is not None else ""
                title = item.findtext("title") or item.findtext("{http://www.w3.org/2005/Atom}title") or ""
                if self.title_exclude and self.title_exclude.search(title):
                    continue
                cats = [c.text.strip() for c in item.findall("category") if c.text]
                out.append((link.strip(), cats))
        if o.get("sitemap_url"):
            for url in self._sitemap_urls(fetcher, o["sitemap_url"], depth=0):
                out.append((url, []))
        for listing in _as_list(o.get("listing_urls")):
            doc = lxml.html.fromstring(fetcher.get_text(listing))
            for href in doc.xpath("//a/@href"):
                out.append((urljoin(listing, href).split("#")[0], []))
        seen: set[str] = set()
        result = []
        for url, cats in out:
            if not url or url in seen or (self.url_pattern and not self.url_pattern.search(url)):
                continue
            seen.add(url)
            result.append((url, cats))
        return result[: self.max_items]

    def _sitemap_urls(self, fetcher: PoliteFetcher, url: str, depth: int) -> list[str]:
        root = etree.fromstring(fetcher.get(url).content)
        if root.tag.endswith("sitemapindex"):
            if depth > 1:
                return []
            subs = [(e.findtext("sm:loc", namespaces=_SITEMAP_NS) or "", e.findtext("sm:lastmod", namespaces=_SITEMAP_NS) or "")
                    for e in root.findall("sm:sitemap", _SITEMAP_NS)]
            pat = self.options.get("sitemap_pattern")
            subs = [s for s in subs if s[0] and (not pat or re.search(pat, s[0]))]
            subs.sort(key=lambda s: s[1], reverse=True)
            urls: list[str] = []
            for loc, _ in subs[: int(self.options.get("max_sitemaps", 3))]:
                urls.extend(self._sitemap_urls(fetcher, loc, depth + 1))
            return urls
        entries = [(e.findtext("sm:loc", namespaces=_SITEMAP_NS) or "", e.findtext("sm:lastmod", namespaces=_SITEMAP_NS) or "")
                   for e in root.findall("sm:url", _SITEMAP_NS)]
        entries.sort(key=lambda s: s[1], reverse=True)
        return [loc.strip() for loc, _ in entries if loc]

    # -- collection --------------------------------------------------------
    def collect(self, ctx: CollectContext) -> Iterator[RawJob | SeenListing]:
        site_host = ""
        for url, cats in self.discover(ctx.fetcher):
            if url in ctx.known_ids:
                yield SeenListing(url)
                continue
            if not ctx.can_fetch_detail():
                continue
            site_host = urlsplit(url).netloc.lower().removeprefix("www.")
            try:
                ctx.note_detail()
                page = ctx.fetcher.get_text(url)
            except NotFound:
                continue
            except FetchError as exc:
                ctx.item_error(f"{self.key}: {url}: {exc}")
                continue
            postings = find_job_postings(page)
            if not postings:
                continue  # not a job offer page
            full_html = None
            if self.content_xpath:
                nodes = lxml.html.fromstring(page).xpath(self.content_xpath)
                if nodes:
                    full_html = lxml.html.tostring(nodes[0], encoding="unicode")
            raw = posting_to_raw(postings[0], url, default_country=self.default_country, full_html=full_html,
                                 site_host=site_host, categories=cats)
            if raw.title:
                yield raw

    def check_alive(self, fetcher: PoliteFetcher, url: str) -> bool | None:
        try:
            page = fetcher.get_text(url)
        except NotFound:
            return False
        except FetchError:
            return None
        postings = find_job_postings(page)
        if not postings:
            return False if self.options.get("missing_markup_means_closed") else None
        valid = parse_date(postings[0].get("validThrough"))
        if valid and valid < utcnow():
            return False
        return True
