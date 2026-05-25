# Spec — Exa → Claude structured event extraction (Tier B+)

> **Status:** ✅ Built 2026-05-25 (`events_extract.py` + `events_exa.py` wiring; on by default).
> Verified read-only: a Shanghai Me brunch article → `{date 2026-06-06, venue Shanghai Me, AED 348}`.
> **Goal:** Turn Exa-discovered *guide/listicle pages* into **structured, individual events**
> (real date · venue · price · booking link) using a Claude extraction pass — the "AI
> data-extraction" moat in `SPEC.md` — without a brittle single-source scraper.

---

## 1. Why

Today `app/ingestion/events_exa.py` stores **one feed item per page** (`_to_event`): the card is a
link with `starts_at/venue/price = null`. That's a *links* feed, not an *events* feed, and it can't
power "book this / lead-gen".

**After:** each discovered page is read by Claude, which emits a **list of concrete events** with the
structured fields the UI and monetization need. Multi-source (Exa breadth) + structured (Claude) +
resilient (no single site to break). No frontend change — `EventCard`/detail already render
date/venue/price; they just light up.

```
            BEFORE                                   AFTER
Exa page ──► 1 feed item (link only)     Exa page ──► Claude extract ──► N structured events
```

---

## 2. Pipeline

```
for key in CATEGORY_QUERIES:                       # 6 lifestyle keys (unchanged)
    pages = exa_client.search(query, with_text=True, num_results=N)
    for page in pages:                             # page.text ≤ 8000 chars (exa _MAX_CHARS)
        events = extract_events(page.text, url=page.url, category=key, today=…)   # Claude
        rows   = [_finalize(e, page, key) for e in events]                        # url, expiry, tags
    upsert_events(session, dedup(rows))            # existing service, keyed by url
```

Discovery, the `events` table, `upsert_events`, the `/events` endpoint, `seed_events`, and the cron all
stay as-is. The only change is **inside the per-page loop**: extract many structured events instead of
emitting the page itself.

---

## 3. New module — `app/ingestion/events_extract.py`

```python
async def extract_events(
    page_text: str, *, url: str, category: str, today: date,
) -> list[dict]:
    """Claude reads one page and returns structured event dicts (possibly empty)."""
```

**Model:** `settings.CLAUDE_EXTRACT_MODEL` → default **`claude-haiku-4-5`** (cheap, fast, high-volume;
the agent keeps Sonnet). 

**Forced tool-use for reliable JSON** (no free-text parsing). One tool, forced:

```python
EMIT_EVENTS_TOOL = {
  "name": "emit_events",
  "description": "Return the concrete, individual events found on the page.",
  "input_schema": {
    "type": "object",
    "properties": {
      "events": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "title":       {"type": "string"},
            "starts_at":   {"type": "string", "description": "ISO 8601; resolve relative dates against TODAY; null if none"},
            "ends_at":     {"type": "string"},
            "venue":       {"type": "string"},
            "area":        {"type": "string", "description": "Dubai neighbourhood"},
            "price_from":  {"type": "string", "description": "'Free' or 'AED 95'"},
            "description": {"type": "string", "description": "1–2 sentences"},
            "booking_url": {"type": "string", "description": "specific ticket/event link if present on the page"}
          },
          "required": ["title"]
        }
      }
    },
    "required": ["events"]
  }
}

resp = await client.messages.create(
    model=settings.CLAUDE_EXTRACT_MODEL,
    max_tokens=2048,
    system=[{"type": "text", "text": EXTRACT_SYSTEM, "cache_control": {"type": "ephemeral"}}],
    tools=[EMIT_EVENTS_TOOL],
    tool_choice={"type": "tool", "name": "emit_events"},
    messages=[{"role": "user", "content": f"TODAY: {today}\nSOURCE: {url}\nCATEGORY: {category}\n\n{page_text}"}],
)
events = next((b.input["events"] for b in resp.content if b.type == "tool_use"), [])
```

**System prompt (`EXTRACT_SYSTEM`) rules** — static, so it's **prompt-cached**:
- Extract only **real, individual, attendable** events in/near Dubai. **Not** the page's own nav,
  ads, or "10 things" framing — the actual events.
- Resolve relative dates ("this Saturday", "23 May") against **TODAY**; output ISO. `null` if undated.
- Be conservative: if the page has no concrete events, return `{"events": []}`. **Never invent**
  dates, venues, or prices.
- `price_from`: "Free" when free; otherwise lowest "AED N". Omit if unknown.
- `booking_url`: only if a specific link is present in the text; else omit (caller falls back to page URL).

---

## 4. Post-processing — `_finalize(raw, page, key)`

| Field | Rule |
|---|---|
| `category` | the query `key` (so the 6 tiles bucket correctly, by construction) |
| `url` | `raw.booking_url` if valid http(s) **and unique**; else `page.url + "#" + slug(title)` (keeps rows unique for the URL-keyed upsert, still opens the source) |
| `source` | host of the final url |
| `starts_at`/`ends_at` | parse ISO (reuse `events._parse_dt`) |
| `expires_at` | `(ends_at or starts_at) + 1 day` if dated, else `now + 14d` (reuse `events.py` rule) |
| `image_url` | `null` (Exa/Claude don't yield images → gradient fallback; future: OG-image fetch) |
| `is_published` | `True` |

**Filtering:** drop events with `starts_at` in the past or > 60 days out (keep undated). 
**Dedup within a run:** key on `(title.lower().strip(), starts_at.date())`; keep first — the same event
recurs across guide pages.

---

## 5. Config additions (`app/config.py`)

```python
CLAUDE_EXTRACT_MODEL: str = "claude-haiku-4-5"
EVENTS_EXTRACT: bool = True          # master switch
EVENTS_EXTRACT_MAX_PAGES: int = 3    # pages/category sent to Claude
```

No new secret — reuses `ANTHROPIC_API_KEY` (already set).

---

## 6. Wiring — `events_exa.py`

`ingest_exa_events` chooses per page:

```python
if settings.EVENTS_EXTRACT and settings.ANTHROPIC_API_KEY:
    rows = await _extract_and_finalize(page, key)     # NEW: structured
else:
    row = _to_event(page, key); rows = [row] if row else []   # current page-as-item fallback
```

**Graceful degradation is the point:** if extraction is off or `ANTHROPIC_API_KEY` is unset, the feed
keeps working exactly as it does today. `seed_events` and the cron are unchanged.

---

## 7. Cost (≈ daily run)

- ~6 categories × 3 pages = **~18 Haiku calls/day**, ~8k input + small output each.
- **Prompt caching** on the static system prompt removes most repeated input cost.
- Haiku pricing + caching ⇒ effectively **cents/day**. (Optional later: a `content_hash` gate on
  pages — skip re-extracting unchanged pages — mirroring Tier A. Not needed at this volume.)

---

## 8. No migration needed

`Event` already has every target field (`starts_at`, `ends_at`, `venue`, `area`, `price_from`,
`description`, `url`, `source`, `expires_at`). Purely additive ingestion change. *(Optional later:
`events.content_hash` for the skip-unchanged optimization; `external_id` for stronger cross-source dedup.)*

---

## 9. Verification

1. **Dry run** (no DB write): `extract_events` over 2–3 live Exa pages, print the structured events —
   same read-only pattern already used to validate the Exa source.
2. **Unit tests:** `_finalize` URL synthesis + dedup; date filtering; empty-list handling.
3. **End-to-end:** run `seed_events`, then `GET /events?category=dining` shows rows with real
   `starts_at`/`venue`/`price_from`; the app's date pills + prices populate.

---

## 10. Sequenced tasks

1. `events_extract.py` — `EMIT_EVENTS_TOOL`, `EXTRACT_SYSTEM`, `extract_events()` (forced tool-use + caching).
2. `_finalize` + dedup/filter helpers in `events_exa.py`; branch `ingest_exa_events` on the extract switch.
3. Config: `CLAUDE_EXTRACT_MODEL`, `EVENTS_EXTRACT`, `EVENTS_EXTRACT_MAX_PAGES`.
4. Dry-run script + unit tests; run end-to-end; confirm structured rows.
5. Docs: flip the Tier B note in `INGESTION.md` from "lossy" to "structured via extraction".

**Estimate:** ~½ day. No schema change, no frontend change, fully backward-compatible.

---

## 11. Limitations / risks

- **Extraction quality tracks source quality** — listicles without firm dates yield undated events
  (kept, sorted last). Curating Exa queries/domains toward event-listing pages improves yield.
- **No images** from this path (gradient fallback). Add OG-image scraping later if cards need photos.
- **Hallucination guard** rests on the conservative prompt + "never invent" rule + the
  past/+60-day filter. Spot-check early runs.
- For **ticketed events with booking links + affiliate revenue**, a dedicated Platinumlist actor/API
  remains the higher-fidelity supplement — this extraction path is the resilient, multi-source base.
