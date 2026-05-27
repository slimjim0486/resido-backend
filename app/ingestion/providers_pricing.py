"""Extract real advertised pricing from a provider's website via Claude (Haiku).

Google Maps never populates `price_level` for home-services businesses (cleaning,
AC repair, handyman, movers, …), so the Services rows carry no usable price. This
module fills that gap on the write path: for a provider that has a website, fetch
the page (Exa-first, same fetch helper the KB uses) and ask Haiku for the lowest
*advertised* AED price plus its unit.

Cheap, high-volume work → Haiku with the static system prompt prompt-cached, the
same shape as `events_extract.py`. Fully gated and best-effort: when AI/keys are
missing it no-ops and rows keep null pricing (graceful degradation, per CLAUDE.md).
Null pricing is the honest "no advertised price" state — the UI just shows nothing.
"""

import asyncio
from datetime import datetime, timezone

from app.config import settings
from app.core.logging import get_logger
from app.ingestion import pipeline

logger = get_logger(__name__)

_MAX_INPUT_CHARS = 8000  # matches exa_client text cap
_MIN_INPUT_CHARS = 200   # too short to plausibly hold pricing
# Provider sites are small; modest fan-out keeps a cell/backfill quick without
# hammering the fetch providers or the Anthropic rate limit.
_DEFAULT_CONCURRENCY = 5

PRICING_SYSTEM = """You extract advertised service prices from the text of a Dubai local-services \
provider's website (cleaning, AC repair, handyman, movers, laundry, pet vets, pet hotels, grooming, etc). Call emit_pricing.

Rules:
- Return ONLY prices the page actually states. NEVER invent, estimate, or convert a number.
- price_from: the lowest concrete advertised price as "AED <n>" (e.g. "AED 35"). Omit if the page \
states no price at all.
- unit: what that price is per, in the page's own terms (e.g. "per hour", "per visit", \
"per cleaner/hour", "per AC unit", "starting package", "per garment"). Omit if unclear.
- price_to: an upper bound, as "AED <n>", only if a clear range is stated. Omit otherwise.
- pricing_notes: one short factual phrase of context if present (e.g. "min 3 hours", \
"deep clean from", "first booking"). Omit if none.
- has_pricing: true only if you found at least one concrete AED amount for a service the business \
performs. A phone number, address, or VAT registration number is NOT a price.
- If the page advertises no concrete service price, set has_pricing=false and omit the price fields."""

EMIT_PRICING_TOOL = {
    "name": "emit_pricing",
    "description": "Return advertised service pricing found on the provider's website.",
    "input_schema": {
        "type": "object",
        "properties": {
            "has_pricing": {"type": "boolean"},
            "price_from": {"type": "string", "description": "lowest advertised price, 'AED <n>'"},
            "price_to": {"type": "string", "description": "upper bound 'AED <n>' if a range is stated"},
            "unit": {"type": "string", "description": "per hour / per visit / per AC unit / per garment …"},
            "pricing_notes": {"type": "string", "description": "short context phrase, e.g. 'min 3 hours'"},
        },
        "required": ["has_pricing"],
    },
}


def _enabled() -> bool:
    """Pricing extraction is on only when explicitly enabled *and* a Claude key
    exists — otherwise we no-op and leave pricing null (graceful degradation)."""
    return bool(settings.SERVICES_EXTRACT_PRICING and settings.ANTHROPIC_API_KEY)


def _normalize(data: dict) -> dict | None:
    """Map Haiku's tool input onto our price_* columns. Returns None when no
    concrete price was found (we never store a unit/notes without a number)."""
    if not data.get("has_pricing") or not data.get("price_from"):
        return None

    def _clip(key: str, n: int) -> str | None:
        v = data.get(key)
        return str(v)[:n] if v else None

    return {
        "price_from": _clip("price_from", 32),
        "price_to": _clip("price_to", 32),
        "price_unit": _clip("unit", 64),
        "price_notes": _clip("pricing_notes", 300),
    }


async def extract_pricing(page_text: str, *, url: str, client=None) -> dict | None:
    """Ask Haiku for advertised pricing in ``page_text``. Returns the price_*
    dict, or None when there's no usable price (or AI is unavailable)."""
    if not _enabled():
        return None
    text = (page_text or "").strip()
    if len(text) < _MIN_INPUT_CHARS:
        return None

    if client is None:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    try:
        resp = await client.messages.create(
            model=settings.CLAUDE_EXTRACT_MODEL,
            max_tokens=512,
            system=[{"type": "text", "text": PRICING_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=[EMIT_PRICING_TOOL],
            tool_choice={"type": "tool", "name": "emit_pricing"},
            messages=[{"role": "user", "content": f"SOURCE: {url}\n\n{text[:_MAX_INPUT_CHARS]}"}],
        )
    except Exception as exc:  # extraction is best-effort; caller continues
        logger.warning("pricing_extract_failed", url=url, error=str(exc))
        return None

    for block in resp.content:
        if block.type == "tool_use" and isinstance(block.input, dict):
            return _normalize(block.input)
    return None


async def price_from_website(url: str, *, client=None) -> dict | None:
    """Fetch a provider site (Exa→Firecrawl) and extract advertised pricing.
    Returns the price_* dict or None on any fetch/extract failure or miss."""
    if not _enabled() or not url:
        return None
    try:
        scraped = await pipeline._fetch_content(url)
    except Exception as exc:  # fetch is best-effort
        logger.warning("pricing_fetch_failed", url=url, error=str(exc))
        return None
    return await extract_pricing(scraped.get("markdown") or "", url=url, client=client)


async def enrich_pricing(rows: list[dict], *, concurrency: int = _DEFAULT_CONCURRENCY) -> int:
    """Mutate scrape rows in place with extracted pricing (best-effort, bounded
    fan-out). Every row with a website gets `price_fetched_at` stamped (so a later
    pass can tell "tried, none found" from "never tried"); rows with a concrete
    price also get the price_* fields. Returns the count priced. No-op → 0 when
    disabled. Mirrors r2_storage.mirror_field's in-place enrichment pattern."""
    if not _enabled():
        return 0

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    now = datetime.now(timezone.utc)
    sem = asyncio.Semaphore(concurrency)
    targets = [r for r in rows if r.get("website")]

    async def _one(row: dict) -> int:
        async with sem:
            data = await price_from_website(row["website"], client=client)
        row["price_fetched_at"] = now
        if data:
            row.update(data)
            return 1
        return 0

    results = await asyncio.gather(*(_one(r) for r in targets))
    priced = sum(results)
    logger.info("pricing_enriched", attempted=len(targets), priced=priced)
    return priced
