"""Draft the WhatsApp quote/appointment-request the user sends themselves (Services CTA).

The Services "Get quotes" CTA captures a high-intent lead, but historically the
provider was never actually contacted — the lead just sat in a table (see
``api/v1/leads.py``). This closes that loop without any outbound-messaging cost
or cold-contact/consent risk: Claude (Haiku) drafts a short, polite quote request
from the user's stated need, and the app opens it **pre-filled in the user's own
WhatsApp** so they send it with one tap. The lead is still recorded for the
demand signal (the moat), now enriched with the need + drafted message.

Same gating/degradation shape as ``ingestion/providers_pricing.py``: no Claude
key → no AI, fall back to a clean templated message so the CTA still works.
"""

import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.models.service import ServiceProvider
from app.services import workspace

logger = get_logger(__name__)

DRAFT_SYSTEM = """You write a short, friendly WhatsApp message that a person in Dubai is about to \
send to a local provider to request a quote, availability, or appointment. Call emit_message with the finished message.

Rules:
- Write in the first person — it's a real message the user sends from their own phone.
- Keep it to 2-4 short sentences: a brief greeting (use the provider's name if given), what they \
need, and a request for a quote/availability or an appointment, whichever fits the provider category.
- For medical providers, ask about appointment availability only; do not describe symptoms beyond what the user provided, diagnose, suggest treatment, or ask for a price.
- Use ONLY the details provided (service, area, timing, budget). NEVER invent specifics — no \
made-up dates, prices, addresses, or names.
- Include timing or budget only if they were given; otherwise don't mention them.
- Plain text only: no markdown, no placeholders like [name] or [date], no emoji spam."""

EMIT_MESSAGE_TOOL = {
    "name": "emit_message",
    "description": "Return the WhatsApp message the user will send to request a quote or appointment.",
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "plain-text WhatsApp message, 2-4 short sentences",
            },
        },
        "required": ["message"],
    },
}


def _clean_number(raw: str | None) -> str | None:
    """Digits-only international form (the backend already stores it that way)."""
    digits = re.sub(r"[^0-9]", "", raw or "")
    return digits or None


def _channel(provider: ServiceProvider) -> tuple[str, str | None]:
    """Pick how the user reaches this provider: WhatsApp first, then SMS via the
    phone number, else no direct channel (the UI offers copy-to-clipboard)."""
    if wa := _clean_number(provider.whatsapp):
        return "whatsapp", wa
    if phone := _clean_number(provider.phone):
        return "sms", phone
    return "none", None


def _template_message(
    provider: ServiceProvider,
    *,
    need: str,
    when_pref: str | None,
    budget_aed: int | None,
    customer_name: str | None,
) -> str:
    """Deterministic fallback used when AI is unavailable — same content shape as
    the AI draft so the CTA behaves identically with or without a Claude key."""
    greeting = f"Hi {provider.name}," if provider.name else "Hi,"
    where = f" in {provider.area}" if provider.area else ""
    if provider.category == "medical":
        parts = [f"{greeting} I'm looking to book an appointment for {need.strip()}{where}."]
    else:
        parts = [f"{greeting} I'm looking for help with {need.strip()}{where}."]
    if when_pref and when_pref.strip():
        parts.append(f"Ideally {when_pref.strip()}.")
    if budget_aed and provider.category != "medical":
        parts.append(f"My budget is around AED {budget_aed}.")
    parts.append(
        "Could you share your availability?"
        if provider.category == "medical"
        else "Could you share a quote and your availability?"
    )
    parts.append(f"Thanks! — {customer_name.strip()}" if customer_name and customer_name.strip() else "Thanks!")
    return " ".join(parts)


async def _ai_message(
    provider: ServiceProvider,
    *,
    need: str,
    when_pref: str | None,
    budget_aed: int | None,
    customer_name: str | None,
) -> str | None:
    """Ask Haiku to draft the message. Best-effort: returns None when AI is
    disabled or the call fails, so the caller falls back to the template."""
    if not settings.ai_enabled:
        return None

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    context = "\n".join(
        [
            f"Provider: {provider.name}" + (f" ({provider.area})" if provider.area else ""),
            f"Service needed: {need.strip()}",
            f"Preferred timing: {when_pref.strip() if when_pref and when_pref.strip() else 'not specified'}",
            f"Budget: {f'AED {budget_aed}' if budget_aed else 'not specified'}",
            f"From: {customer_name.strip() if customer_name and customer_name.strip() else 'a customer'}",
        ]
    )
    try:
        resp = await client.messages.create(
            model=settings.CLAUDE_EXTRACT_MODEL,
            max_tokens=400,
            system=[{"type": "text", "text": DRAFT_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=[EMIT_MESSAGE_TOOL],
            tool_choice={"type": "tool", "name": "emit_message"},
            messages=[{"role": "user", "content": context}],
        )
    except Exception as exc:  # drafting is best-effort; fall back to the template
        logger.warning("quote_draft_failed", provider=str(provider.id), error=str(exc))
        return None

    for block in resp.content:
        if block.type == "tool_use" and isinstance(block.input, dict):
            return (block.input.get("message") or "").strip() or None
    return None


async def draft_quote(
    session: AsyncSession,
    user_id: UUID,
    *,
    provider: ServiceProvider,
    need: str,
    when_pref: str | None = None,
    budget_aed: int | None = None,
    customer_name: str | None = None,
) -> dict:
    """Draft a quote/appointment-request message for ``provider`` and record the lead.

    Returns ``{lead_id, message, channel, to_number}``. ``message`` is AI-written
    when a Claude key is present, else a clean template. ``channel`` is
    ``whatsapp`` | ``sms`` | ``none`` and ``to_number`` is the digits-only number
    the app builds the deep link from (null when no channel)."""
    message = await _ai_message(
        provider,
        need=need,
        when_pref=when_pref,
        budget_aed=budget_aed,
        customer_name=customer_name,
    )
    ai_drafted = message is not None
    if message is None:
        message = _template_message(
            provider,
            need=need,
            when_pref=when_pref,
            budget_aed=budget_aed,
            customer_name=customer_name,
        )

    channel, to_number = _channel(provider)
    lead = await workspace.create_lead(
        session,
        user_id,
        vertical=provider.category,
        payload={
            "provider_id": str(provider.id),
            "provider_name": provider.name,
            "area": provider.area,
            "source": "services_directory",
            "need": need.strip(),
            "when_pref": when_pref,
            "budget_aed": budget_aed,
            "channel": channel,
            "drafted_message": message,
            "ai_drafted": ai_drafted,
        },
    )
    logger.info(
        "quote_drafted",
        provider=str(provider.id),
        channel=channel,
        ai=ai_drafted,
        lead=str(lead.id),
    )
    return {
        "lead_id": lead.id,
        "message": message,
        "channel": channel,
        "to_number": to_number,
    }
