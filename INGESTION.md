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
set) → **Exa** (`app/ingestion/events_exa.py`, current default — one query per lifestyle key, tagged with
that key so the 6 tiles bucket correctly; lossy on date/venue/price) → curated samples. Exa items are
current, real, tappable links; cards/detail hide the date pill when there's no date and fall back to the
source host. **Upgrade path:** a custom Apify actor (or Exa→Claude structured extraction) for true
per-event date/venue/price.

Tiers A and B are independent pipelines and can proceed in parallel.
