# DOCUMENTS.md — Renewals (the "important docs" offering)

The third core offering. Read this before touching `app/services/renewals.py`,
the `documents` table, or the `/me/documents` endpoints.

## The one design decision everything follows from

**We are a renewal radar, not a document vault.** The value is knowing *when a
document expires* and *what to do next* — never holding the document. So the
entire feature runs on the smallest possible piece of data per item:

```
{ doc_type, expiry_date, confidence }
```

No ID numbers. No passport scans. No uploaded copies. The `documents.file_url`
column exists from the initial schema but is **deliberately left unpopulated** —
do not wire it up without an explicit product decision to become a vault (which
would change the privacy posture, security surface, and trust pitch entirely).

The privacy contract is a feature, say it in the UI:

> **We never ask for or store your documents.** Just tell us when things expire —
> we'll remind you and show you exactly how to renew.

## Why this lives on existing primitives

Two tables from `0001_initial` already cover this; we did not invent a new concept:

- **`documents`** (`app/models/document.py`) — the tracked renewal items. One row
  per thing-with-an-expiry. This is the source of truth.
- **`deadlines`** (`app/models/deadline.py`) — general-purpose, *ad-hoc* reminders
  not tied to a document (a fine due date, a school term, an appointment). Left
  as-is. Renewals do **not** materialize deadline rows — reminder timing is
  derived (see below), so the two systems stay independent and don't double-count.

`0006` adds two columns to `documents`:

- `confidence` `confirmed|estimated` (default `confirmed`) — see cascade.
- `last_reminded_on` `date|null` — dedupe key for the (future) reminder job.

## The capture mechanic: anchor-date cascade

Onboarding asks **one optional, skippable question** — the visa/EID expiry date —
and never forces it. Everything else is "add later." From that single anchor we
populate the **visa cluster** in one write (`workspace.apply_visa_anchor`):

| doc_type           | derivation from the visa expiry date | confidence  |
| ------------------ | ------------------------------------- | ----------- |
| `visa`             | the date the user entered             | `confirmed` |
| `emirates_id`      | same date (matches in practice)       | `estimated` |
| `health_insurance` | same date (must be valid to renew)    | `estimated` |

`VISA_ANCHOR_CASCADE` in `renewals.py` is the authoritative list. Anything we
*derive* rather than the user directly stating is marked `estimated` so a guessed
date never masquerades as fact — the UI shows a "~ tap to confirm" badge, and it
keeps us honest with the agent's "never guess on visa/legal/deadline" guardrail.

Everything outside the visa cluster runs on its own cycle and is **never
inferred** — it's an "add later" chip (`onboarding_chip` in the catalog):
passport, driving licence, car registration, motor insurance, Ejari/tenancy,
trade licence, domestic-worker visa & insurance.

> Honest scope note: the cascade is genuinely powerful for the visa cluster and
> politely useless for the rest. That's intended — the anchor delivers an instant
> populated dashboard; the rest is a calm grid that never nags.

## The catalog (`RENEWAL_TYPES`)

`app/services/renewals.py` is the single, centrally-tunable registry — same spirit
as `ingestion/registry.py`. Each `RenewalType` carries the Dubai-specific knobs:

- `lead_days` — surface/remind when the expiry is within this many days. Tuned per
  document, framed around the **fine/consequence avoided**, not a generic "7 days":
  - visa **60**, EID **30** (medical test + EID appointment need runway)
  - car registration / motor insurance **30** (insurance must be valid *before*
    the Mulkiya renewal)
  - health insurance **45** (it gates the visa renewal)
  - passport **240** — the ~8-month rule, not a true expiry (validity threshold)
- `recurrence` — `yearly` for annual items, `none` for multi-year (visa, licence,
  passport) the user re-dates manually.
- `kb_query` / `kb_category` — powers the **"How to renew →"** CTA via `kb_search`
  (offering #1, cited).
- `lead_vertical` — powers the **"Find help →"** CTA via `create_lead` (offering
  #3): typing centres → `relocation`, brokers → `insurance`, Mulkiya test →
  `car_service`, tenancy → `real_estate`. `null` where no partner applies.

## The payoff loop (why this is the strongest offering)

A renewal card is the highest-intent trigger in the app:

```
expiry approaches → "How to renew →" (cited KB)  → "Find help →" (service lead)
                         offering #1                    offering #3
```

The documents engine is what keeps surfacing demand for the other two halves. A
visa-expiry nudge that lands "here are 3 nearby typing centres" is a monetizable,
in-the-moment lead — not a generic calendar ping.

## API surface (`/api/v1/me`)

JWT-authed, like the rest of the workspace. Envelope is `APIResponse` as usual.

- `GET    /me/documents`            — list tracked renewals (soonest expiry first)
- `POST   /me/documents`            — track one `{doc_type, expiry_date, ...}`
- `PATCH  /me/documents/{id}`       — update expiry / confidence / notes
- `DELETE /me/documents/{id}`       — stop tracking (hard delete; no document data is held anyway)
- `POST   /me/visa-anchor`          — `{expiry_date}` → runs the cascade, returns the cluster

## Agent tools (`agent/tools.py`)

Intent-named, matching the existing convention (`set_reminder`, `find_services`).
All route through `services/workspace.py` so agent and REST never diverge.

- `list_renewals` — read tracked renewals
- `track_renewal` — add one (`doc_type` from the catalog, `expiry_date`, optional `confidence`)
- `update_renewal` — change an expiry/confidence (requires `id`)
- `set_visa_anchor` — the magic: one visa expiry → the visa cluster

The agent offers these opportunistically (e.g. user asks "how do I renew my
Mulkiya?" → answer, then "want me to track it so you don't get fined?"). No
deletion tool — removal is a deliberate UI action only.

## Not built yet (deliberate)

- **Reminder delivery.** Timing is derivable today (`needs_reminder(doc, today)`),
  but there's no push/FCM/email infra in the app yet, so reminders are surfaced
  **in-app** ("expires in N days"). When a channel exists, add a cron service
  (cf. `DEPLOY_CRON.md`) that scans `documents`, respects `lead_days` +
  `last_reminded_on`, and sends. `recurrence: yearly` rolls the date forward on
  acknowledgement.
- **Flutter "Renewals" surface** in My Dubai (RenewalCard mirroring EventCard,
  the chip grid, the optional onboarding anchor question).
- **Photo→OCR date extraction.** Out of scope for trust reasons; only revisit with
  on-device-only, never-uploaded framing.
