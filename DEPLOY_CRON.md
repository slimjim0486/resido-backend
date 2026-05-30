# Railway cron jobs — paused

Scheduled jobs normally keep the ingestion moat fresh and clean (see `backend/INGESTION.md`),
but they are intentionally paused while the product direction is being reconsidered.
Each cron config sets `deploy.cronSchedule` to `null` and uses a no-op `startCommand`,
so deploying those services cannot accidentally run scrapes, refreshes, deduplication,
or preference distillation.

Railway supports `cronSchedule: null` in config-as-code, and code config overrides
dashboard service settings for the deployment. The web-service fallback schedulers are
also disabled by default via `BACKGROUND_JOBS_PAUSED=true`, with
`EVENTS_FEED_AUTORUN=false` and `DEDUPLICATOR_AUTORUN=false` as secondary per-job gates.

Original scheduled jobs, kept here for when they are intentionally resumed:

| Job | Script | Config file | Paused config | Previous schedule (UTC) | What it does |
|---|---|---|---|---|---|
| **KB refresh** (Tier A) | `scripts/refresh_kb.py` | `railway.refresh-kb.json` | `cronSchedule: null` | `0 */6 * * *` (every 6h) | Registers the source allowlist, then re-fetches only TTL-elapsed sources. Hash gate skips re-embedding unchanged pages, so most runs are near-free. |
| **Events feed** (Tier B) | `scripts/seed_events.py` | `railway.seed-events.json` | `cronSchedule: null` | `0 2 * * *` (06:00 Dubai) | Pulls the lifestyle "What's on" feed. Source precedence: **Apify actor** if `APIFY_EVENTS_ACTOR` set → else **Exa** per-category queries (current default) → else rolling sample set. Existing event images are reused, so recurring runs do not redo Exa image/R2 work for known URLs. |
| **Services refresh** | `scripts/refresh_services.py` | `railway.refresh-services.json` | `cronSchedule: null` | `0 3 * * 1` (weekly Monday @ 07:00 Dubai) | Checks for stale `(category/subcategory × area)` cells, then refreshes only the oldest due batch (default cap: 30 cells/run). Category TTLs live in `services_registry.py` so volatile home/medical-access cells refresh sooner than slower pets/medical specialty cells. No-op without `APIFY_TOKEN`. |
| **Event deduplicator** | `scripts/deduplicate.py --scope events` | `railway.deduplicate.json` | `cronSchedule: null` | `0 4 * * *` (08:00 Dubai) | Scores live events for cross-source duplicates after the daily feed refresh. Removes only pairs at `DEDUPLICATOR_CONFIDENCE_THRESHOLD` or higher (default `0.90`). |
| **Provider deduplicator** | `scripts/deduplicate.py --scope providers` | `railway.deduplicate-providers.json` | `cronSchedule: null` | `0 7 * * 1` (weekly Monday @ 11:00 Dubai) | Scores active providers for cross-source duplicates after the weekly services refresh. Providers are soft-hidden (`is_active=false`) to preserve durable attribution. |
| **Preference distillation** | `scripts/distill_preferences.py` | `railway.distill-preferences.json` | `cronSchedule: null` | `0 2 * * *` (06:00 Dubai) | Uses Haiku to enrich preference summaries for users with enough taste signals. No-op without `ANTHROPIC_API_KEY`. |

> **Schedules are UTC.** Dubai is UTC+4, so `0 2 * * *` fires at 06:00 Dubai.
> Restore each job's real `startCommand` and `cronSchedule`, then set
> `BACKGROUND_JOBS_PAUSED=false` before re-enabling the fallback schedulers.

## How Railway cron works (the constraints these files satisfy)
- A cron job is a **separate service** that runs its `startCommand` on the schedule, then **must exit** — both scripts do (`asyncio.run(...)` returns → exit 0).
- **No healthcheck** (it's not a web server) and `restartPolicyType: "NEVER"` so a clean exit isn't treated as a crash + restarted.
- Railway **won't overlap** runs — if the previous run is still going, the next tick is skipped.
- Minimum interval is 5 minutes.

These cron jobs depend on the schema already existing — migrations run from the **web** service (`railway.json`), not here. (If a cron fires before the first web deploy migrates, that one run no-ops/errors in the log; the next succeeds.)

---

## Setup (dashboard, one-time)

Do this once per cron service in the same Railway project as `resido-backend`:

1. **New → Empty Service** (or **GitHub Repo** → pick `resido-backend`). Name it `kb-refresh` / `events-feed` / `services-refresh` / `event-deduplicator` / `provider-deduplicator`.
2. **Settings → Source:** point at the same repo/branch as the web service.
3. **Settings → Config-as-code → Railway Config File:** set the path:
   - `kb-refresh` → `railway.refresh-kb.json`
   - `events-feed` → `railway.seed-events.json`
   - `services-refresh` → `railway.refresh-services.json`
   - `event-deduplicator` → `railway.deduplicate.json`
   - `provider-deduplicator` → `railway.deduplicate-providers.json`
4. **Variables:** give the service the env it needs (see below). Easiest is **project Shared Variables** referenced from all three services.
5. **Deploy.** Confirm **Settings → Cron Schedule** now shows the value from the config file, and the build uses the right start command.

### Env vars per job
Reference the Postgres plugin so both jobs hit the same DB:

```
DATABASE_URL = ${{Postgres.DATABASE_URL}}
```

| Variable | `kb-refresh` | `events-feed` | Notes |
|---|---|---|---|
| `DATABASE_URL` | ✓ | ✓ | same Postgres as web |
| `EXA_API_KEY` | ✓ | ✓ | KB fetch (refresh) **and** the events feed (per-category queries) |
| `VOYAGE_API_KEY` | ✓ | — | embeddings; without it, chunks store without vectors (keyword fallback) |
| `EMBEDDINGS_PROVIDER` / `VOYAGE_MODEL` | ✓ | — | match the web service |
| `FIRECRAWL_API_KEY` | optional | — | fallback fetch only |
| `APIFY_TOKEN` / `APIFY_EVENTS_ACTOR` | — | optional | if set, a real scraper takes precedence over Exa for events |

> Events-feed precedence: `APIFY_EVENTS_ACTOR` (if set) → **Exa** (default, needs `EXA_API_KEY`) → curated samples. Tune the Exa path with `EVENTS_EXA_PER_CATEGORY` and `EVENTS_EXTRACT_MAX_PAGES`.

**Services refresh** (`services-refresh`) needs `DATABASE_URL` + `APIFY_TOKEN` (reuses the same token; actor defaults to `compass/crawler-google-places` via `APIFY_MAPS_ACTOR`). It runs weekly as a check, not as a full scrape: `scripts.refresh_services` selects only TTL-elapsed cells from the `service_provider_cells` refresh ledger, sorts oldest-first, and caps the run at 30 cells by default. Category TTL/per-cell caps live in `app/ingestion/services_registry.py`; this keeps the expanded home + pets + medical grid realistic instead of refreshing all 211 cells at once. Without `APIFY_TOKEN` it no-ops (seed once with `scripts/seed_services.py` for curated samples).

**Deduplicators** (`event-deduplicator`, `provider-deduplicator`) need only `DATABASE_URL`. Optionally set `DEDUPLICATOR_CONFIDENCE_THRESHOLD` if you want a stricter threshold than `0.90`. Run `python -m scripts.deduplicate --dry-run --scope events` or `--scope providers` manually first to inspect confidence reasons before enabling the cron.

`ANTHROPIC_API_KEY`, `REDIS_URL`, `SECRET_KEY`, `CORS_ORIGINS` are **not** needed by the basic refresh/dedupe crons — they fall back to safe defaults.

> Tip: put `EXA_API_KEY`, `VOYAGE_API_KEY`, `APIFY_TOKEN`, etc. in **project → Settings → Shared Variables** once, then in each service reference them (`${{shared.EXA_API_KEY}}`) so web + both crons stay in sync.

---

## Setup (CLI alternative)

```bash
railway link                       # select the resido project
# KB refresh
railway add --service kb-refresh
railway variables --service kb-refresh \
  --set "DATABASE_URL=${{Postgres.DATABASE_URL}}" \
  --set "EXA_API_KEY=…" --set "VOYAGE_API_KEY=…" --set "EMBEDDINGS_PROVIDER=voyage"
# then set Config-as-code path to railway.refresh-kb.json in the dashboard
railway up --service kb-refresh

# Events feed
railway add --service events-feed
railway variables --service events-feed \
  --set "DATABASE_URL=${{Postgres.DATABASE_URL}}" \
  --set "APIFY_TOKEN=…" --set "APIFY_EVENTS_ACTOR=<user>/dubai-events-scraper"
railway up --service events-feed
```

(The Config-as-code **file path** per service is set in the dashboard — the CLI doesn't expose it yet.)

---

## Verify
- **Settings → Cron Schedule** is empty/disabled for paused services.
- Deployment logs print `Resido cron paused; ... did not run.` if a paused cron service is deployed manually.

When jobs are intentionally resumed:
- **Settings → Cron Schedule** shows the expected expression.
- Trigger a manual run (dashboard **⋯ → Run** / `railway run --service kb-refresh "python -m scripts.refresh_kb --limit 5"`).
- **Logs** should show:
  - `kb-refresh`: `Registry: N new source(s)` then `Refresh: due=… re-embedded=… unchanged/empty=… failed=…`
  - `events-feed`: `Apify (...): inserted N` or `inserted N curated sample events`.
  - `event-deduplicator` / `provider-deduplicator`: `Deduplicator (APPLIED): checked …` and one line per removed duplicate with its confidence and score components.

## Tuning
- **Cost too high?** Lower `refresh_kb` frequency (e.g. `0 */12 * * *`) — TTLs are in days, so 6–12h is plenty. `--limit` caps sources per run as the registry grows from Tier C promotion, and failed sources back off instead of retrying every tick.
- **Stale events?** Raise `seed_events` frequency or shift the hour. The feed self-expires per event, so over-fetching only costs Apify/Exa compute, never correctness. Lower `EVENTS_EXA_PER_CATEGORY` or `EVENTS_EXTRACT_MAX_PAGES` to reduce Exa/Claude usage.
- **Services too expensive?** Lower the weekly batch cap in `scripts.refresh_services --limit N`, raise a category's `refresh_ttl_days`, lower `refresh_per_cell`, or set `SERVICES_MAX_REVIEWS=0` for a cheaper details-free refresh. Do not make this a daily full-grid scrape.
