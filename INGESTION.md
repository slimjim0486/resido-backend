# Resido — Data Ingestion Architecture

> **Decided:** 2026-05-25. Resolves SPEC §9 "KB ingestion engine".
> **Principle:** Resido has **two content domains with opposite economics.** Treating them
> the same way — re-scraping everything on a schedule and re-embedding it all — is the expensive
> mistake. Source each by its *volatility*, and separate the three cost events: **discover · fetch · embed.**

---

## 1. The two domains

| | **Essentials KB** (visa, EID, Ejari, DEWA, RTA, insurance, banking, schooling) | **Lifestyle feed** (what's on, dining, events, getaways) |
|---|---|---|
| Changes | Slowly — rules/fees shift occasionally | Constantly — events expire, venues open |
| "Latest" means | "Correct **as of** date X" + citation | "**This week / this weekend**" |
| Volume | Bounded (~100–300 canonical pages) | High churn, time-bound |
| Storage | `kb_chunks` + Voyage embeddings (RAG) | structured `events` table — **not** embedded |
| Failure mode if stale | Wrong legal/fee answer → liability | A dead event link → mild |
| Pattern | **Discover once · fetch on TTL · embed on change** | **Scheduled pull · cache to expiry** |

Embedding lifestyle content into pgvector is wasted spend: "Coldplay at Etihad Arena, Jan 12" is
time-bound — it's a feed row, not a permanent Q&A chunk.

---

## 2. Tool routing — right tool, right cost

| Tool | Cost shape | Use it for | Don't use it for |
|---|---|---|---|
| **Exa** | per query; text included in the same call | **Default discovery + fetch.** Returns page text from its own index → bypasses u.ae proxy/JS block; folds discover+fetch into one billed call | bulk re-fetch of unchanged pages |
| **Firecrawl** | per page scrape | **Fallback** clean-markdown for pages Exa indexes poorly; supports `maxAge` cache | the u.ae path (it can't reach it) |
| **Apify** | per compute (heaviest) | **Structured/recurring** scrapes only: event listings (Tier B), RTA/Salik fines, price feeds; genuinely JS-hard/anti-bot pages | anything Exa already indexes |

Implemented: `app/ingestion/exa_client.py` (`search(with_text=True)`, `get_contents()`),
`firecrawl_client.py`, `apify_client.py`. `pipeline._fetch_content()` does **Exa-first → Firecrawl-fallback**.

---

## 3. Tier A — Essentials KB (cited, slow-changing)

The RAG corpus. Schema already supports it: `sources.refresh_ttl_days`, `kb_chunks.fetched_at`.

```
Exa search (includeDomains, with_text=True)   ── discover ONCE → sources registry
        │
        ▼
_fetch_content(url): Exa contents → Firecrawl fallback   ── fetch on TTL
        │
        ▼
content-hash diff ── unchanged? skip ──▶ (no embed, no write)   ◀── the cost lever
        │ changed
        ▼
chunk → Voyage embed → upsert kb_chunks (url + fetched_at + category)
```

- **Discover once.** Run Exa with `includeDomains` over the authoritative allowlist to populate
  `sources`. Re-discovery is rare (new topics), not per-refresh.
- **Fetch on a staggered TTL.** Refresh worker re-fetches sources whose `last_fetched_at` exceeds
  `refresh_ttl_days`. Stagger so we never re-fetch everything at once (smooths cost, dodges rate limits).
  Suggested TTLs: visa/residency rules `60–90`, fees/prices `30`, stable explainer pages `90`.
- **Embed only on change.** Hash the fetched text; if unchanged, skip the delete+embed. Embeddings
  (`voyage-4-large`) are the one *recurring* cost — gating them behind a hash drops steady-state cost
  to ≈ the Exa fetch calls alone. *(Task #4 — adds `content_hash` to `sources`.)*

**Authoritative allowlist (seed `sources`):** `u.ae`, `gdrfa.gov.ae`, `dha.gov.ae`, `mohap.gov.ae`,
`rta.ae`, `dewa.gov.ae`, `centralbank.ae`, `mohre.gov.ae`, `khda.gov.ae`, `dubai.gov.ae`.

---

## 4. Tier B — Lifestyle feed (fresh, churny)

Powers the **"What's on in Dubai" Home hero**. **No embeddings, no RAG.**

```
Apify actor (Platinumlist / Time Out / Visit Dubai)   ── 1 scheduled run/day
        │
        ▼
normalize → upsert events(title, starts_at, ends_at, venue, url, image_url, category, expires_at)
        │
        ▼
served directly to Home hero · cached to each event's end date (self-expiring)
```

- Structured rows, not chunks → cheap to store, trivial to render, expire themselves.
- One scheduled Apify run/day covers it; on-demand reads hit the cached table, never the scraper.
- Optional cheaper variant: Exa date-filtered search (`startPublishedDate`) for "what's on this week,"
  cached in Redis — use if a given source has no good Apify actor. *(Task #6.)*
- **Query facets (migration `0011`):** each row carries `price_min` (lowest AED, parsed from the
  `price_from` display string via `app/core/pricing.parse_aed`; `0`=free, `NULL`=no price listed) and
  `family_friendly` (`bool|None`, derived at ingest via `events.infer_family_friendly` — category +
  keyword scan, or the Claude extractor's explicit flag). These let `events_service.search_events`
  answer budget / kid-friendly / day-of-week asks at the DB. Day/date filters read **Dubai-local**
  time (`timezone('Asia/Dubai', starts_at)`), since rows are stored UTC.
- **Search stays keyword, never semantic.** Free-text `query` is `ilike` over title/description/venue —
  this is the one place we *deliberately don't* embed: the feed is small and time-bound, so vectors
  would be recurring spend that goes stale. `search_events` backs both `GET /api/v1/events` and the
  agent's `find_events` tool, so the Home feed and the co-pilot never diverge.

---

## 4b. Services — local provider directory (Tier A-style, monetization surface)

Powers the **Services vertical** — Resido's lead-gen surface (`SPEC.md` §1 monetization).
High-intent local providers (cleaning, AC repair, handyman, movers, …), discoverable, ranked,
with a callback/quote CTA. **No embeddings, no RAG** — structured rows, like Tier B — but modeled
on **Tier A economics**: a *bounded grid, slow TTL, durable rows*, the opposite of the churny feed.

```
for (category × area) in a bounded grid      ── ~10 cats × ~10 areas = ~100 cells
        │
        ▼
Apify compass/crawler-google-places   ── 1 run/cell, MONTHLY (only TTL-elapsed cells)
        │
        ▼
normalize defensively → Bayesian score → upsert service_providers (key: place_id)
        │
        ▼
served via GET /api/v1/services?category=&area=  ·  ranked score desc (google_rank tiebreaker)
```

- **Why Apify (not Exa) here:** Google Maps is the canonical, structured source for local businesses
  (rating, review count, phone, hours, geo) and is JS-hard/anti-bot — exactly Tier 2's "structured/recurring
  scrape" lane. The off-the-shelf `compass/crawler-google-places` actor is used via the existing
  `apify_client.run_actor` (reuses `APIFY_TOKEN`; actor configurable via `APIFY_MAPS_ACTOR`).
- **Bounded grid = the cost lever.** The (category × area) registry (`app/ingestion/services_registry.py`)
  is finite. `SERVICES_PER_CELL` (~15) caps places/cell. ~100 cells × ~15 ≈ 1.5k places **once a month** at
  ~$4/1k — a few dollars/month, cached as durable rows. Not daily; that's the whole point.
- **TTL refresh, Tier-A style.** `scripts/refresh_services.py` re-scrapes only cells whose freshest row has
  aged past `SERVICES_TTL_DAYS` (30) — cell freshness is read straight off `max(fetched_at)` per
  `(category, area)` (`providers.cell_freshness`), so there's no separate bookkeeping table. Wired to a
  **monthly** Railway cron (`railway.refresh-services.json`, `0 3 1 * *`). Seed/refresh both reuse `APIFY_TOKEN`.
  The detail-page scrape (opening hours) is the costliest toggle and is gated by `SERVICES_SCRAPE_DETAILS`
  (default on) so re-seeds can run fast/cheap without it.
- **Retiring stale listings (mark-and-sweep).** `fetched_at` *is* "last seen in a scrape" (the upsert only
  bumps it when a provider appears). After a cell is scraped, `providers.retire_stale` **soft-hides**
  (`is_active=False`, never deletes) any provider in *that cell* whose `fetched_at` is older than
  `SERVICES_STALE_GRACE_DAYS` (60 ≈ 2 missed monthly scrapes). This is fair both ways: one missed scrape is
  free (protects a business that briefly dropped below the rank cap or had a bad Google day), but a sustained
  ~2-month absence retires it; users stop seeing dead listings within ~2 months. The sweep is **scoped to
  cells actually scraped**, so a paused cron can never blank the directory. Reappearing in a later scrape
  flips `is_active` back to `True` (revival keeps the row id + lead attribution). `GET /services` filters on
  `is_active`; the row's `fetched_at` is exposed so the app can show an "Updated <month>" freshness cue
  (signal freshness, don't chase it — cost-lever #4). *(Migration `0005_service_provider_is_active`.)*
- **Ranking — Bayesian trust score.** `score = (v/(v+m))·R + (m/(v+m))·C` (m=20, C=4.2), computed on upsert,
  so a 5.0-from-6-reviews can't outrank a 4.6-from-800. Default sort is `score` desc; `google_rank` (scrape
  order) is the tiebreaker. `rating` + `reviews_count` are surfaced as the trust signal.
- **Defensive normalize.** Maps actor field names vary; `providers_maps.normalize` maps many candidate keys
  (`totalScore`/`rating`, `reviewsCount`/`reviews`, `location{lat,lng}`/`lat`+`lng`, …) and derives a
  wa.me-ready WhatsApp number from UAE *mobiles* only. Per-cell `try/except` so one bad query can't sink the run.
- **Demoable offline.** `scripts/seed_services.py` falls back to a curated sample set when `APIFY_TOKEN` is
  unset (like `seed_events.py`); `--dry-run --category --area` scrapes one cell and prints normalized+scored
  rows with **no DB write** (the read-only validation pattern).
- **Agent + monetization.** `find_services(category, area)` returns the top ranked providers to the co-pilot;
  `create_lead` accepts a service category + provider context so the agent can offer a callback/quote → `leads`.

**Shipped 2026-05-25:** `service_providers` table + migration `0004_service_providers`; `app/services/providers.py`
(upsert/list + Bayesian score + `cell_freshness`); `app/ingestion/providers_maps.py` + `services_registry.py`;
public `GET /api/v1/services`; `find_services` agent tool; `scripts/seed_services.py` + `scripts/refresh_services.py`
+ monthly cron. Verified read-only: an `ac_repair × Dubai Marina` dry run returned 15 providers with real
place_ids, hours, derived WhatsApp numbers, and correct Bayesian ranking. (`apify_client.run_actor` also fixed to
swap `username/actor` → `username~actor` for the REST path.)

---

## 4c. Image mirroring (Cloudflare R2)

Scraped image URLs are short-lived — Google Maps photo links are token-signed and expire,
ticket-site images are hotlink-protected/rate-limited — so storing them verbatim leaves cards
with broken images within days. At ingest we **re-host every scraped image in the R2 `resido`
bucket** and persist *our* stable public URL instead.

```
normalize → mirror_field (download → R2 PutObject) → upsert      ── write path only
```

- **Where it hooks:** `events.ingest_apify_events` (`image_url`) and `providers_maps.ingest_cell`
  (`photo_url`), right before upsert. `dry_run_cell` and the Exa events path are untouched
  (Exa yields no images; dry runs never write).
- **Keys are content-addressed on the row's identity** — `events/<sha1(url)>`,
  `providers/<sha1(place_id)>` — so a re-scrape overwrites the same object (bounded object count;
  provider photos refresh monthly in place).
- **No SDK.** `app/ingestion/r2_storage.py` SigV4-signs a single S3 `PutObject` over httpx —
  same raw-REST approach as Apify/Exa, and sidesteps boto3's default-checksum quirks with
  S3-compatible stores. Downloads use a browser UA (to clear hotlink protection), a 10 MB cap,
  and content-type sniffing for CDNs that mislabel.
- **Graceful + gated.** Set all five `R2_*` vars (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
  `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_PUBLIC_URL`) and mirroring runs; leave any blank and
  it no-ops, keeping source URLs (the local-dev default). Any per-image download/upload failure
  logs a warning and falls back to the source URL — a bad image can't sink an ingest run.
  `R2_PUBLIC_URL` is the bucket's Public Development URL / custom domain, **not** the
  account-level `*.r2.cloudflarestorage.com` S3 endpoint (that's derived from the account id).

---

## 5. Tier C — Live on-demand (the long tail + "latest" safety valve)

When the agent's `kb_search` misses, don't fail or hallucinate:

```
kb_search miss → just-in-time Exa search (with_text) → answer + cite
        │
        ▼
Redis cache result by query (hours)   ·   if source is authoritative + reusable → promote into Tier A
```

- Pay **only when actually asked.** Cache by query so repeats are free.
- **Demand-driven KB growth:** real questions promote URLs into `sources`, so the KB grows from
  what users need instead of speculative pre-scraping. *(Task #7.)*

---

## 6. Cost levers (summary)

1. **Discover once · fetch on TTL · embed only on change** — the big one (Tier A).
2. **Right tool per job** — Exa default fetch · Firecrawl fallback · Apify only structured/recurring.
3. **Cache everywhere** — Redis (Exa-by-query, event feed) · Firecrawl `maxAge` · events self-expire.
4. **Freshness *signaling*, not chasing** — the "last verified" badge (already in the UI) lets a
   60-day TTL still read as current. Don't re-pull daily to feel fresh.
5. **Demand-driven growth** — Tier C promotes real questions into Tier A; no speculative scraping.

---

## 7. Sequenced work

| # | Tier | Task | Status |
|---|---|---|---|
| 1 | A | Exa-contents fetch path (unblocks u.ae) | ✅ done |
| 4 | A | `content_hash` gating — skip re-embed on unchanged | ✅ done |
| 5 | A | TTL staggered refresh worker + source allowlist | ✅ done |
| 6 | B | `events` table + Apify lifestyle feed → Home hero | ✅ done |
| 7 | C | On-demand Exa search + KB promotion in agent | ✅ done |
| 8 | Services | Apify Google Maps grid → `service_providers` directory + lead-gen | ✅ done |

**Tier C shipped (2026-05-25):** `app/services/live_search.py` — when `kb_search` returns nothing,
the tool transparently falls back to a cached (Redis, 6h) just-in-time Exa search; results are flagged
`verified: false` with a `source_note`, citations omit a verified date, and the system prompt tells the
agent to hedge. Authoritative-domain hits (`*.gov.ae`, `u.ae`, `rta.ae`, `centralbank.ae`, …) are
promoted into `sources` (last_fetched_at NULL) so the next `refresh_kb` run ingests them into the
verified Tier A KB — demand-driven growth. No new agent tool surface; the fallback lives inside `kb_search`.

**Tier A cost gate + refresh shipped (2026-05-25):** `sources.content_hash` (migration
`0003_source_content_hash`); `pipeline.ingest_url` now skips chunk-replace + re-embed when a
refetched page is byte-identical, touching only `last_fetched_at`. `app/ingestion/registry.py`
holds the curated allowlist (14 sources, per-category TTLs); `app/ingestion/refresh.py` refreshes
only TTL-elapsed sources, oldest-first, capped per run; `scripts/refresh_kb.py` registers + refreshes
(`--limit`, `--register-only`). **Wired as a Railway cron** (`railway.refresh-kb.json`, every 6h);
the Tier B feed runs alongside it (`railway.seed-events.json`, daily). Setup steps + env vars:
[`backend/DEPLOY_CRON.md`](DEPLOY_CRON.md).

**Tier B shipped (2026-05-25):** `events` table + migration `0002_events`; public `GET /api/v1/events`;
`scripts/seed_events.py`; Flutter Home strip + per-category Explore listings + Event detail page, all
wired to the feed with a graceful sample fallback.

**Events source precedence** (`seed_events`): **Apify** (`app/ingestion/events.py`, if `APIFY_EVENTS_ACTOR`
set) → **Exa** (`app/ingestion/events_exa.py`, default) → curated samples. One Exa query per lifestyle key,
tagged with that key so the 6 tiles bucket correctly.

**Exa→Claude structured extraction (built 2026-05-25, on by default).** When `EVENTS_EXTRACT` +
`ANTHROPIC_API_KEY` are set, each discovered page is read by Claude (`app/ingestion/events_extract.py`,
Haiku + forced tool-use + prompt caching) into **structured individual events** (real date · venue · price ·
booking link). `_finalize` synthesizes unique URLs, dedups on (title, date), and drops past / >60-day
events. Verified: a Shanghai Me brunch article → `{date 2026-06-06, venue Shanghai Me, AED 348}`.
Falls back to page-as-item links when AI is off (date pill hidden, source host as subtitle). Full design:
[`backend/EVENTS_EXTRACTION.md`](EVENTS_EXTRACTION.md). Later high-fidelity supplement: a dedicated
Platinumlist actor/affiliate feed for ticketed events.

Tiers A and B are independent pipelines and can proceed in parallel.
