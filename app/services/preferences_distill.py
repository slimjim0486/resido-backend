"""Haiku distillation — the AI enrichment layer of the taste graph.

The rules engine (`preferences.recompute_profile`) gives us *structured* taste:
which categories/areas/budgets the signals add up to. This module reads the same
behaviour as free text — the actual titles of events a user saved, the things they
asked providers for — and asks Haiku for the *nuance* the structured maps can't
hold: cuisine, vibe, dietary needs, indoor/outdoor, budget-consciousness. It writes
a warmer `summary` plus conservative `inferred` notes.

Same gating/degradation shape as `ingestion/events_extract.py` and `services/quotes.py`:
no Claude key → no-op (the rules-templated summary stands). Best-effort per user —
an LLM failure never raises into the cron. Runs from `scripts/distill_preferences.py`.
See backend/PERSONALIZATION.md.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.models.event import Event
from app.models.favorite import Favorite
from app.models.lead import Lead
from app.models.preference import PreferenceProfile
from app.models.service import ServiceProvider
from app.services import preferences

logger = get_logger(__name__)

_MAX_NOTES = 8

DISTILL_SYSTEM = """You maintain a concise taste profile for a Dubai lifestyle app, used to \
personalise event and service suggestions. From a user's saved/requested history you call \
emit_profile with (1) a short, warm summary and (2) durable preference notes.

Rules:
- summary: 1-2 plain sentences, third person, e.g. "Enjoys relaxed weekend brunches in Dubai \
Marina and family days out; budget-conscious." Describe taste, not a data dump.
- notes: ONLY durable preferences the structured data can't already express AND that a clear, \
repeated pattern supports — cuisine (vegetarian, seafood), vibe (quiet, lively, outdoor seating), \
dietary/lifestyle (no alcohol, halal), setting (beach, desert, indoor). Each note is a short \
third-person phrase ("Vegetarian", "Prefers quiet venues", "Loves the beach").
- BE CONSERVATIVE. One data point is not a pattern — omit a note rather than guess. Never invent \
a preference the history doesn't clearly support. A confident profile of 2 good notes beats 6 shaky ones.
- Do NOT restate the obvious structured facts (their top category/area/budget) as notes — those are \
already captured; notes are for the nuance beyond them.
- confidence: 0.0-1.0, how strongly the history supports the note. Use <0.6 sparingly.
- If the history is too thin for any real nuance, return an empty notes list (a summary is still fine)."""

EMIT_PROFILE_TOOL = {
    "name": "emit_profile",
    "description": "Return the distilled taste summary and durable preference notes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "1-2 sentence, third-person taste summary"},
            "notes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "short third-person preference phrase"},
                        "confidence": {"type": "number", "description": "0.0-1.0 support strength"},
                    },
                    "required": ["text"],
                },
            },
        },
        "required": ["summary", "notes"],
    },
}


async def _gather_context(
    session: AsyncSession, user_id: UUID, profile: PreferenceProfile
) -> str | None:
    """Build the free-text history block for the LLM: the rules-derived grounding
    plus the actual saved/requested items. Returns None when there's nothing to go on."""
    fav_rows = (
        await session.execute(
            select(Favorite.item_type, Favorite.item_id)
            .where(Favorite.user_id == user_id)
            .order_by(Favorite.created_at.desc())
            .limit(40)
        )
    ).all()
    event_ids = [iid for it, iid in fav_rows if it == "event"]
    service_ids = [iid for it, iid in fav_rows if it == "service"]

    lines: list[str] = []
    if profile.summary:
        lines.append(f"Structured summary (already known): {profile.summary}")
    if profile.typical_budget_aed is not None:
        lines.append(f"Typical spend: ~AED {round(profile.typical_budget_aed)}")
    if profile.family_bias is True:
        lines.append("Tends toward family-friendly outings")

    if event_ids:
        rows = (
            await session.execute(
                select(Event.title, Event.category, Event.venue, Event.area, Event.price_from)
                .where(Event.id.in_(event_ids))
            )
        ).all()
        if rows:
            lines.append("\nSaved events:")
            for title, cat, venue, area, price in rows[:20]:
                bits = [b for b in (cat, venue, area, price) if b]
                lines.append(f"  - {title}" + (f" ({', '.join(bits)})" if bits else ""))

    if service_ids:
        rows = (
            await session.execute(
                select(ServiceProvider.name, ServiceProvider.category, ServiceProvider.area)
                .where(ServiceProvider.id.in_(service_ids))
            )
        ).all()
        if rows:
            lines.append("\nSaved service providers:")
            for nm, cat, area in rows[:15]:
                bits = [b for b in (cat, area) if b]
                lines.append(f"  - {nm}" + (f" ({', '.join(bits)})" if bits else ""))

    lead_rows = (
        await session.execute(
            select(Lead.vertical, Lead.payload)
            .where(Lead.user_id == user_id)
            .order_by(Lead.created_at.desc())
            .limit(10)
        )
    ).all()
    needs = []
    for vertical, payload in lead_rows:
        need = (payload or {}).get("need")
        needs.append(f"  - {vertical}" + (f": {need}" if need else ""))
    if needs:
        lines.append("\nRequested services / quotes:")
        lines.extend(needs)

    # Need *some* concrete history beyond the bare grounding lines to say anything new.
    has_history = bool(event_ids or service_ids or lead_rows)
    if not has_history and not profile.summary:
        return None
    return "\n".join(lines)


async def distill_user(session: AsyncSession, user_id: UUID, *, client=None) -> bool:
    """Distill one user's taste with Haiku and persist it. Returns True if a profile
    was written, False when skipped (no key / no history / LLM failure)."""
    if not settings.ai_enabled:
        return False
    profile = await preferences.get_preference_profile(session, user_id)
    if profile is None or profile.personalization_paused:
        return False
    context = await _gather_context(session, user_id, profile)
    if not context:
        return False

    if client is None:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    try:
        resp = await client.messages.create(
            model=settings.CLAUDE_EXTRACT_MODEL,
            max_tokens=600,
            system=[{"type": "text", "text": DISTILL_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=[EMIT_PROFILE_TOOL],
            tool_choice={"type": "tool", "name": "emit_profile"},
            messages=[{"role": "user", "content": f"User history:\n{context}"}],
        )
    except Exception as exc:  # best-effort; the rules-templated summary stands
        logger.warning("distill_failed", user=str(user_id), error=str(exc))
        return False

    summary, notes = None, []
    for block in resp.content:
        if block.type == "tool_use" and isinstance(block.input, dict):
            summary = (block.input.get("summary") or "").strip() or None
            raw = block.input.get("notes")
            if isinstance(raw, list):
                notes = [n for n in raw if isinstance(n, dict) and (n.get("text") or "").strip()][:_MAX_NOTES]
            break

    await preferences.apply_distillation(
        session, user_id, summary=summary, inferred_notes=notes
    )
    return True
