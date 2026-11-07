# Khedma: Tunisian job aggregator

Khedma collects public job offers published in Tunisia from many sources, normalizes them, removes duplicates and makes them searchable on one site. It links every offer to its original posting.

It is **only an information aggregator**. It does not parse CVs, match candidates, store profiles or manage applications. Users apply on the original site.

```
backend/
  sources.yaml            source registry: what is collected, and why some sources are not
  jobhub/
    collectors/
      http.py             polite fetcher: robots.txt, rate limits, retries, anti-bot detection
      robots.py           robots.txt parser (RFC 9309, supports * and $ wildcards)
      base.py             collector contract (RawJob, SeenListing) and type registry
      adapters/           one module per kind of source
    normalize/            location gazetteer, taxonomy, skills, text/HTML/date helpers
    pipeline.py           collect → normalize → dedupe → store → lifecycle; failure handling
    search.py             full-text search (SQLite FTS5) and faceted filters
    api.py                JSON API (/api, OpenAPI docs at /api/docs)
    web.py, templates/    server-rendered French UI
    scheduler.py          collection every 2 hours
    cli.py                command line
  tests/                  unit and end-to-end tests, fixtures captured from the real sources
```

## Free deployment (GitHub Actions + GitHub Pages)

The public site is fully static, so it costs nothing to host:

* Every 2 hours, [.github/workflows/publish.yml](.github/workflows/publish.yml) does the following:
  1. restores the database from the `data` branch
  2. collects new offers
  3. saves the database back to the `data` branch
  4. rebuilds the site as static HTML plus `data/jobs.json`
  5. publishes it on GitHub Pages
* Job, company and source pages are pre-rendered HTML, which is good for SEO. On the catalogue page, search and filters run in the browser on `data/jobs.json`.

### One-time setup (about 5 minutes)

1. Create a **public** repository on GitHub, for example `khedma`. Public repositories get unlimited free Actions minutes. A private one gets 2,000 minutes a month, which is just about enough.
2. Push this folder to it:
   ```bash
   git init -b main
   git add .
   git commit -m "Tunisian job aggregator"
   git remote add origin https://github.com/<your-user>/khedma.git
   git push -u origin main
   ```
3. On GitHub, go to **Settings → Pages → Build and deployment → Source** and choose **GitHub Actions**.
4. Go to **Actions → "Collect jobs and publish site" → Run workflow**. The first run takes about 15 minutes, because it collects everything once. Later runs take 2 to 5 minutes.
5. Your site is live at `https://<your-user>.github.io/khedma/` and refreshes every 2 hours.

Notes:
* GitHub may start scheduled runs a few minutes late during busy periods.
* GitHub pauses scheduled workflows in a repository with no activity for 60 days. If that happens, click **Enable workflow** in the Actions tab.
* To use your own domain, add it under **Settings → Pages → Custom domain**. Links adapt automatically.
* If the `data` branch is ever deleted, the next run simply does a full collection again.

## Run locally (development)

Requires Python 3.11+.

```bash
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt     # Windows; on Linux/macOS use .venv/bin/
cd backend
../.venv/Scripts/python -m jobhub.cli collect               # first collection (about 15 min, polite rate limits)
../.venv/Scripts/python -m uvicorn jobhub.main:app --port 8000
```

Open http://localhost:8000. Locally the site runs as a dynamic server, with the same pages plus the JSON API at `/api/docs`.

To preview exactly what GitHub Pages will serve:

```bash
../.venv/Scripts/python -m jobhub.cli build-static --out ../_site
../.venv/Scripts/python -m http.server -d ../_site 8001
```

### Self-hosting on a server (optional)

The [Dockerfile](Dockerfile) runs the dynamic server and the 2-hour collection in one container. Mount a persistent volume at `/data`. Use this if you have a VPS. The free GitHub Pages setup above does not need it.

Outside Docker, you can refresh automatically every two hours in either of these ways:

* a dedicated worker: `python -m jobhub.cli schedule`
* the web process itself: set `JOBHUB_SCHEDULER_ENABLED=1`. Use this with a single web worker only.

A file lock prevents two collections from overlapping.

### CLI

| Command | Purpose |
|---|---|
| `python -m jobhub.cli collect` | collect from all enabled sources |
| `python -m jobhub.cli collect -s keejob -s workable-tn` | collect from specific sources only (ignores failure backoff) |
| `python -m jobhub.cli sources` | list sources with their health and active offer counts |
| `python -m jobhub.cli stats` | catalogue statistics |
| `python -m jobhub.cli schedule` | run the 2-hour collection loop |
| `python -m jobhub.cli build-static --out DIR [--base-path /repo] [--site-url URL]` | export the static site |
| `python -m jobhub.cli export-db PATH` | write a compact single-file copy of the database |

### Configuration (environment variables)

| Variable | Default | |
|---|---|---|
| `JOBHUB_DATABASE_URL` | `sqlite:///backend/data/jobhub.db` | SQLAlchemy URL |
| `JOBHUB_COLLECT_INTERVAL_HOURS` | `2` | collection interval |
| `JOBHUB_SCHEDULER_ENABLED` | `0` | run the scheduler inside the web process |
| `JOBHUB_USER_AGENT` | `TunisieJobsBot/1.0 (...)` | sent with every request; put a contact URL here in production |
| `JOBHUB_ROBOTS_TOKEN` | `TunisieJobsBot` | name matched against robots.txt `User-agent` groups |
| `JOBHUB_MIN_REQUEST_INTERVAL` | `1.0` | minimum seconds between two requests to the same host; a larger robots `Crawl-delay` wins |
| `JOBHUB_STALE_AFTER_DAYS` | `45` | when to close feed offers that can't be verified |
| `JOBHUB_ADMIN_TOKEN` | unset | enables `POST /api/admin/collect` (header `X-Admin-Token`) |
| `JOBHUB_SITE_NAME` | `Khedma` | brand name shown in the UI |

## Sources

All sources are configured in [backend/sources.yaml](backend/sources.yaml). The `/sources` page shows each one's live status.

| Source | Kind | How it is read |
|---|---|---|
| Keejob | Tunisian job board | `llms-full.txt` (index of all active offers) plus a `.md` page per offer. Keejob publishes both for automated readers. |
| Tunisie Travail | Tunisian job board | RSS feed plus schema.org `JobPosting` markup on each offer page |
| Farojob | Tunisian job board | public listing and offer pages (its RSS feed is disallowed by robots.txt) |
| Workable network | ATS, many companies | `jobs.workable.com` search API filtered on Tunisia. robots.txt declares `Content-Signal: search=yes`. |
| Yassir (Lever), Ozeol (Recruitee), SESAMm and ALX (Greenhouse) | company career boards | official public job-board APIs, keeping only Tunisia-located offers |

Sources that are deliberately **not** collected (each is listed in `sources.yaml` with its reason):

* **Tanitjobs**: its terms of use only allow one personal, non-commercial copy of the content.
* **Emploi Tunisie**: serves a Cloudflare anti-bot challenge to automated clients.
* **Optioncarriere**: robots.txt disallows offer pages, and it is an aggregator itself.
* **SmartRecruiters (Devoteam, etc.)**: `api.smartrecruiters.com/robots.txt` disallows every agent except LinkedInBot. The adapter is written and can be enabled if that changes.

### Adding a source

**Same platform as an existing source.** This needs only YAML, for example another company on Lever:

```yaml
- key: lever-acme
  name: Acme
  type: lever                # greenhouse | lever | recruitee | ashby | workable_account | smartrecruiters
  homepage: https://acme.com
  options: {board: acme, company: Acme, website: https://acme.com}
```

**Any site that publishes schema.org `JobPosting` markup.** Most career sites and job boards do, because Google for Jobs requires it. Use the generic `jsonld` type and point it at an RSS feed, a sitemap or listing pages:

```yaml
- key: acme-careers
  name: Acme Careers
  type: jsonld
  options:
    sitemap_url: https://careers.acme.tn/sitemap.xml   # or feed_url / listing_urls: [...]
    url_pattern: "/jobs/"                              # which discovered links are offers
    country: TN                                        # default when the markup has no country
```

**A new kind of site.** Subclass `Collector` in `jobhub/collectors/adapters/`, decorate it with `@register("my_type")`, import it in `adapters/__init__.py`, and add a fixture-based test. A collector only yields `RawJob` objects (or `SeenListing` for offers already stored). Throttling, robots.txt, retries, normalization, deduplication and lifecycle are handled for it.

Check the site's robots.txt and terms of use before enabling a new source.

## How collection works

1. **Fetching.** Every request goes through `PoliteFetcher`, which does the following:
   * checks robots.txt (cached 24 h, wildcard-aware). If robots.txt is unreachable or returns 401/403, the host is treated as off-limits.
   * spaces requests to the same host.
   * retries timeouts, 429 and 5xx responses with backoff, honoring `Retry-After`.
   * stops with `AccessBlocked` on 401/403 or an anti-bot challenge. It never retries or works around these.
2. **Normalization.** Each `RawJob` is mapped to controlled vocabularies:
   * location: 24 governorates plus cities and business districts, e.g. "Les Berges du Lac" → Tunis, "Technopole El Ghazala" → Ariana
   * contract: CDI, CDD, SIVP, internship, freelance…
   * experience level and work mode (on-site, hybrid, remote)
   * domain and specialization, using a weighted keyword classifier
   * skills, from a curated dictionary

   Descriptions are sanitized HTML. Offers that are not located in Tunisia are rejected.
3. **Deduplication.** An offer is stored once as a `Job`, with one `Listing` per source. It counts as a duplicate in either of these cases:
   * it has the same fingerprint: normalized company + title + governorate
   * it is from the same company, has a very similar title, a compatible governorate and a close publication date

   Titles with different numbers never match, and two active ads on the same source are never merged by fuzzy matching.
4. **Lifecycle.**
   * **Snapshot sources** (Keejob, the ATS APIs, Workable): an offer missing from a *successful, complete* run is closed. If a run returns less than half of the previously active offers, nothing is closed and the run is marked partial.
   * **Feed sources:** when an offer leaves the feed, its page is re-checked and the offer is closed if it is gone or past `validThrough`.
   * Offers past their expiry date, or older than 90 days with no expiry date, are closed.
   * A job stays active while any of its listings is active.
5. **Failures.**
   * A failing item is counted and skipped.
   * A failing source gets a `failed` or `blocked` run, and nothing is closed for it. It then backs off exponentially, or for 24 h if it was blocked.
   * Each run is recorded in `collection_runs` with counts, request totals and error samples.

## API

The OpenAPI docs are at `/api/docs`. Main endpoints:

* `GET /api/jobs` takes these query parameters:
  * `q`, `domain`, `specialization`, `governorate`, `city`
  * `contract` (repeatable), `experience` (repeatable), `workplace` (repeatable)
  * `posted_within` (1/3/7/30)
  * `company`, `sort` (recent|relevance), `page`, `per_page`

  It returns results and facet counts.
* `GET /api/jobs/{id}`: full detail, including every source and the original application link
* `GET /api/companies`, `GET /api/companies/{slug}`
* `GET /api/stats`, `GET /api/taxonomy`, `GET /api/sources`, `GET /api/health`

## Tests

```bash
cd backend && ../.venv/Scripts/python -m pytest -q
```

The tests cover:

* robots.txt semantics and the polite fetcher: blocking, retries, throttling, challenge detection
* normalization rules
* every adapter, against pages captured from the real sources
* the pipeline end to end: dedupe, closing, the suspicious-snapshot guard, backoff, reactivation, liveness checks, run locking
* search and facets
* the HTTP layer

## Production notes

* SQLite with FTS5 handles tens of thousands of offers comfortably. Models are plain SQLAlchemy, so PostgreSQL works too. Search then falls back to `ILIKE`.
* The static catalogue loads one JSON index, about 150 KB compressed for 650 offers. That stays fast up to a few thousand offers.
* `JOBHUB_USER_AGENT` should include a contact URL so site owners can reach you. The workflow sets it to your `/sources` page.
