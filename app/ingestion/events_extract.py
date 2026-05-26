"""Claude structured-event extraction for the Tier B feed.

Reads one Exa-discovered page and returns structured, individual events
(date · venue · price · booking link) via forced tool-use. Cheap, high-volume
work → runs on Haiku with the static system prompt prompt-cached. Degrades to
an empty list (caller keeps the page-as-item feed) when AI is unavailable.
See backend/EVENTS_EXTRACTION.md.
"""

from datetime import date

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_INPUT_CHARS = 8000  # matches exa_client text cap
_MIN_INPUT_CHARS = 400   # too short to plausibly hold events

EXTRACT_SYSTEM = """You extract real, attendable events in or near Dubai, UAE from the text of a \
web page (often a guide or listicle). Call emit_events with what you find.

Rules:
- Return ONLY concrete, individual events a person could actually attend — never the page's \
navigation, ads, newsletter prompts, or the "N things to do" framing itself.
- Resolve relative dates ("this Saturday", "next weekend", "23 May") against the provided TODAY \
date and output ISO 8601 (YYYY-MM-DD, or full datetime when a time is given). Use null when no \
date is stated — NEVER guess a date.
- NEVER invent venues, prices, dates, or links. Omit any field the page does not state.
- price_from: "Free" when free; otherwise the lowest price as "AED <n>". Omit if unknown.
- area: the Dubai neighbourhood if identifiable (e.g. "Downtown", "Dubai Marina").
- booking_url: include ONLY if a specific ticket/event link for that event appears in the text.
- description: one or two factual sentences.
- family_friendly: true if it's clearly suitable for kids/families (e.g. mentions \
children, all ages, a theme park, a family day); false if it's clearly adults-only \
(18+/21+, nightclub, ladies' night, bottomless brunch). Omit if unclear — don't guess.
- If the page has no concrete events, return an empty list."""

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
                        "title": {"type": "string"},
                        "starts_at": {"type": "string", "description": "ISO 8601; null if undated"},
                        "ends_at": {"type": "string", "description": "ISO 8601; optional"},
                        "venue": {"type": "string"},
                        "area": {"type": "string", "description": "Dubai neighbourhood"},
                        "price_from": {"type": "string", "description": "'Free' or 'AED 95'"},
                        "description": {"type": "string", "description": "1-2 sentences"},
                        "family_friendly": {
                            "type": "boolean",
                            "description": "true if clearly kid/family-suitable, false if clearly adults-only; omit if unclear",
                        },
                        "booking_url": {"type": "string", "description": "specific ticket/event link if present"},
                    },
                    "required": ["title"],
                },
            }
        },
        "required": ["events"],
    },
}


async def extract_events(
    page_text: str,
    *,
    url: str,
    category: str,
    today: date,
    client=None,
) -> list[dict]:
    """Return structured event dicts found on the page (possibly empty)."""
    if not settings.ANTHROPIC_API_KEY:
        return []
    text = (page_text or "").strip()
    if len(text) < _MIN_INPUT_CHARS:
        return []

    if client is None:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    try:
        resp = await client.messages.create(
            model=settings.CLAUDE_EXTRACT_MODEL,
            max_tokens=2048,
            system=[{"type": "text", "text": EXTRACT_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=[EMIT_EVENTS_TOOL],
            tool_choice={"type": "tool", "name": "emit_events"},
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"TODAY: {today.isoformat()}\nSOURCE: {url}\nCATEGORY: {category}\n\n"
                        f"{text[:_MAX_INPUT_CHARS]}"
                    ),
                }
            ],
        )
    except Exception as exc:  # extraction is best-effort; caller continues
        logger.warning("extract_failed", url=url, error=str(exc))
        return []

    for block in resp.content:
        if block.type == "tool_use" and isinstance(block.input, dict):
            events = block.input.get("events")
            if isinstance(events, list):
                return [e for e in events if isinstance(e, dict) and (e.get("title") or "").strip()]
    return []
