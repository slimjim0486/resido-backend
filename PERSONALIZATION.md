# Personalization — the co-pilot's preference memory ("taste graph")

This is the authoritative design doc for how Resido remembers what a user likes and
uses it to personalize **events** (Tier B lifestyle feed) and **services** (provider
directory). It complements `INGESTION.md` (content tiers) and `DOCUMENTS.md` (renewals).

> **Why not the Essentials?** Visa/EID/DEWA/etc. are *date-driven* and already
> readable from the user's tracked documents + profile — there's nothing to "learn".
> This system is scoped to the two surfaces where taste matters and isn't otherwise
> knowable: what to do, and who to hire.

## Principles (read before changing anything here)

- **Structured, not embedded.** Like events themselves (`INGESTION.md`: "events are
  time-bound, RAG would be wasted spend"), we personalize on **structured attributes**
  (category, area, price band, family, weekday), not vectors. No new embedding spend.
- **Trust still wins for services.** The Bayesian trust score is the moat. Taste is a
  *bounded tiebreaker* (`_PROVIDER_BOOST`), never an override — a clear trust gap
  (e.g. 4.3 vs 4.6) survives even a perfect taste match.
- **Graceful degradation.** Everything in Phase 1 is deterministic and works with **no
  AI key**. The Haiku layer (Phase 2) only enriches the human-readable summary; the
  ranker never depends on it. No profile / paused / zero signals → the feed is returned
  exactly as the anonymous feed would be.
- **One ranker, two callers.** Personalization lives in the **service layer**
  (`search_events`, `list_providers`), so the agent's `find_events`/`find_services` and
  the public REST feeds get identical behavior — the same lockstep rule as everywhere
  else in this codebase.
- **Separate from identity.** Taste lives in its own tables, never on `profiles`, so a
  user can reset/pause their memory without touching who they are.

## Architecture — three layers

### Layer 1 — Signals (`preference_signals`)

An append-only behavioral log. One row per meaningful action.

| column | meaning |
|---|---|
| `kind` | `favorite` · `unfavorite` · `lead` · `view` · `dismiss` · `contact` · `search` |
| `domain` | `event` · `service` |
| `item_id` | event/provider UUID (nullable; **no content FK** — content churns) |
| `attrs` | **denormalized facets at capture time**: `{category, area, price_min, family_friendly, weekday, tags}` |
| `weight` | base strength for the `kind` (decay applied later) |

The load-bearing decision is **denormalizing `attrs`**. Favorites (`models/favorite.py`)
store only a ref because they re-resolve to a live card; a *signal is about the taste*,
not the item, and events self-expire / providers soft-delete — so the meaning must
outlive the row. We never join a signal back to content.

Base weights (`SIGNAL_WEIGHTS`): `lead 5 · favorite 3 · contact 2 · view 1 · search 0.5
· unfavorite -1.5 · dismiss -2`.

**Capture points (Phase 1):** `workspace.add_favorite` / `remove_favorite` /
`create_lead` call `preferences.capture_*`, which resolves the item, writes the signal,
and recomputes. All best-effort (a capture failure can never break the core mutation).
Future points (Phase 3): a `view` signal from detail-screen opens, `search` from feed
queries.

### Layer 2 — Preference profile (`preference_profile`, 1:1 with user)

The distilled view the ranker actually reads.

*Machine-usable (rules-derived, normalized so the dominant entry ≈ 1.0, sign kept):*
`area_weights`, `event_category_weights`, `service_category_affinity`, `tag_weights`,
`weekday_weights`, `typical_budget_aed`, `family_bias`.

*Human-readable:* `summary` (templated in Phase 1; Haiku in Phase 2) and `notes` (a
JSONB array of `{id, text, source, confidence, pinned}` — the editable bullets for the
"What Resido knows" UI).

*Bookkeeping:* `signal_count` (confidence proxy), `personalization_paused` (kill switch),
`distilled_at`.

**Recompute** (`preferences.recompute_profile`, rules-only, deterministic & idempotent):
fold every signal with **time decay** `weight · 0.5^(age_days / HALF_LIFE_DAYS)`
(`HALF_LIFE_DAYS = 60`) into the weight maps; `typical_budget_aed` is the decayed
weighted mean of priced positive signals; `family_bias` is set by a clear behavioral
lean, else seeded from `profiles.household` (children → True). Runs after each capture.

**Summary ownership.** `summary` is rules-templated by recompute *until* the Haiku cron
runs; once `distilled_at` is set, the distiller owns `summary` and recompute stops
overwriting it (a `reset` clears `distilled_at`, handing it back to the template).

### Layer 2½ — Haiku distillation — **built**

`preferences_distill.distill_user` reads the same behavior as *free text* — the actual
titles of saved events, saved provider names, the `need` text on leads — and asks Haiku
(`CLAUDE_EXTRACT_MODEL`, forced tool-use) for the nuance the structured maps can't hold
(cuisine, vibe, dietary, indoor/outdoor, budget-consciousness). It writes a warmer
`summary` + conservative `inferred` notes via `preferences.apply_distillation`, which
**preserves `explicit`/`chat`/pinned notes** and replaces only the `inferred` ones.

Runs from `scripts/distill_preferences.py` (`railway.distill-preferences.json`, daily
02:00): self-limiting via `select_distillation_candidates` (enough signal, not paused,
new-or-stale, oldest-first, capped). Gated on the Claude key — no key → no-op and the
rules-templated summary stands. The prompt is deliberately conservative (a pattern, not
one data point; never invent; omit rather than guess).

### Layer 3 — Explicit memory (notes) — **built**

Durable tastes the structured signals can't capture (vegetarian, dislikes loud venues).
They live in `preference_profile.notes` (a JSONB list of `{id, text, source, confidence,
pinned, created_at}`), are **independent of recompute** (which only rewrites the derived
weight maps + templated summary), and are surfaced to the agent via `memory_for_prompt`.

- **Agent**: the `remember_preference` tool (source `chat`) — the prompt tells it to save
  only lasting, clearly-stated leisure/dining/service preferences, never one-offs or
  visa/legal facts. Deduped on case-insensitive text.
- **User**: the `/me/preferences` control API (source `explicit`) — read, add, edit, delete
  notes, plus pause and reset. Both paths call the same `services.preferences` functions, so
  agent and API never diverge.

## Ranking (`preferences.rank_events` / `rank_providers`)

**Confidence shrinkage** mirrors the Bayesian trust prior: `alpha = n / (n + ALPHA_K)`
(`ALPHA_K = 5` → 5 signals ≈ 0.5, 15 ≈ 0.75). A brand-new user is barely nudged; cold
start falls back to base order seeded from profile facts (e.g. household → family lean).

- **Events** — base order is soonest-first. Blend = `time_score · (1 + alpha · match)`,
  where `time_score` ≈ 1 today, 0.5 a week out, small/flat for undated rows. This lets
  taste reorder *near-term* events but won't drag a far-future perfect match to the top.
  `match ∈ [-1,1]` weights: category 0.40 · area 0.25 · family 0.15 · weekday 0.10 ·
  budget 0.10.
- **Services** — base order is Bayesian trust. Blend = `score · (1 + alpha · match ·
  _PROVIDER_BOOST)` with `_PROVIDER_BOOST = 0.12` (taste moves the effective score by at
  most ±12% at full confidence — a tiebreaker, not an override). `match` weights: area
  0.50 · category-used 0.30 · budget 0.20.

The service layer **over-fetches** (events ×3, providers ×4) when personalizing so the
re-rank has a candidate pool; the SQL filters/order are otherwise untouched.

## Surfaces / wiring

- **Public feeds** `GET /events`, `GET /services` now take **optional auth**
  (`dependencies.get_optional_user` → `optional_user_from_bearer`). Signed-in (the
  Flutter client always attaches its guest token) → personalized; anonymous → identical
  to before.
- **Agent** — `find_events`/`find_services` load the profile and pass `personalize`;
  `orchestrator._workspace_snapshot` adds a `Lifestyle tastes:` line (`memory_for_prompt` =
  summary + explicit notes) so the agent can *name* a preference when explaining a pick;
  `remember_preference` lets it save durable tastes. `prompts.py` rule 5c keeps it natural,
  forbids over-claiming, and scopes what's worth remembering.
- **Control API** (`/me/preferences`, JWT): `GET` (summary + derived chips + notes + pause
  state), `POST/PATCH/DELETE /notes[/{id}]`, `PATCH` (pause toggle), `POST /reset` (forget
  everything). This is the backend for the "What Resido knows about you" screen.

## File map

```
models/preference.py            PreferenceSignal, PreferenceProfile
alembic/versions/0012_*.py      both tables
services/preferences.py         capture · recompute · scoring · ranking · notes/controls · distill write+select
services/preferences_distill.py Haiku distillation (gather history → emit summary+notes)
scripts/distill_preferences.py  daily cron entrypoint (self-limiting)
railway.distill-preferences.json  daily 02:00 cron service
services/workspace.py           capture hooks in favorite/lead mutations
services/events.py              search_events(personalize=…)
services/providers.py           list_providers(personalize=…)
agent/tools.py                  find_events/find_services pass personalize; remember_preference
agent/orchestrator.py           tastes line in the snapshot (memory_for_prompt)
agent/prompts.py                rule 5c (personalize from memory + remember_preference)
dependencies.py                 get_optional_user
api/v1/events.py, services.py   optional auth + pass personalize
api/v1/me.py                    /me/preferences control API (GET/notes/pause/reset)
schemas/preferences.py          PreferenceOut, note CRUD + settings bodies
```

## Tuning knobs (all in `services/preferences.py`)

`SIGNAL_WEIGHTS` · `HALF_LIFE_DAYS` (60) · `ALPHA_K` (5) · `_EV_W` / `_SV_W` component
weights · `_PROVIDER_BOOST` (0.12) · `_MIN_WEIGHT` (0.05, map noise floor).

## Roadmap

- **Phase 1 (done):** signals + capture on favorite/lead, rules recompute, `personalize`
  in both service fns with shrinkage, optional auth on feeds, agent snapshot line. Zero
  recurring cost.
- **Phase 2 — explicit memory + control API (done):** `remember_preference` agent tool and
  the `/me/preferences` endpoints (read / add / edit / delete notes, pause, reset). Deterministic,
  no AI cost.
- **Phase 2 — Haiku distillation (done):** `preferences_distill.py` +
  `scripts/distill_preferences.py` + `railway.distill-preferences.json` (daily) write a
  richer `summary` + nuanced `inferred` notes; degrades to the templated summary with no key.
- **Phase 2 — remaining:** the Flutter "What Resido knows about you" screen on top of the
  `/me/preferences` API (summary + chips + editable notes + pause/reset).
- **Phase 3 (done):** legibility — `explain_event`/`explain_provider` return a "Because you…"
  reason, surfaced as a `reason` field on `EventOut`/`ServiceProviderOut` and rendered as a
  coral chip on the cards + a "For you this week" Home header; `events.tags` (migration 0013,
  Haiku extraction in `events_extract`/`events_exa`) feeding `tag_weights` and a re-weighted
  `score_event`; `view`/`dismiss` signals via `capture_interaction` + `POST /me/signals`, fired
  fire-and-forget from the event/provider detail screens.
- **Phase 3:** `events.tags` populated by the existing Haiku events extraction (feeds
  `tag_weights` for "jazz/brunch/vegetarian"); `view`/`dismiss` signals from detail
  screens; "Because you…" reason strings on cards.
```
