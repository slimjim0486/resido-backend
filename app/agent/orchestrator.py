"""Claude tool-use loop. Degrades gracefully when ANTHROPIC_API_KEY is unset."""

import json
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import build_system_prompt
from app.agent.tools import TOOLS, execute_tool
from app.config import settings
from app.core.logging import get_logger
from app.models.profile import Profile
from app.models.user import User
from app.services import preferences, renewals
from app.services.workspace import (
    get_or_create_profile,
    list_checklist,
    list_deadlines,
    list_documents,
)

logger = get_logger(__name__)

MAX_TURNS = 6


@dataclass
class AgentResult:
    reply: str
    citations: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)


def _profile_summary(profile: Profile) -> str:
    parts = []
    if profile.nationality:
        parts.append(f"Nationality: {profile.nationality}")
    if profile.emirate:
        parts.append(f"Emirate: {profile.emirate}")
    if profile.visa_type:
        parts.append(f"Visa: {profile.visa_type}")
    if profile.arrival_date:
        parts.append(f"Arrived: {profile.arrival_date.isoformat()}")
    if profile.household:
        parts.append(f"Household: {json.dumps(profile.household)}")
    if profile.notes:
        parts.append(f"Notes: {profile.notes}")
    return "; ".join(parts) or "No profile details saved yet."


async def _workspace_snapshot(session: AsyncSession, profile: Profile, user_id, *, today: date) -> str:
    """A compact, date-aware view of the user's situation, injected so the agent
    can be proactive (lead with what's expiring) instead of re-asking for facts."""
    documents = await list_documents(session, user_id)
    checklist = await list_checklist(session, user_id)
    deadlines = await list_deadlines(session, user_id)

    lines = [
        f"LIVE SNAPSHOT (as of {today.isoformat()}) — use it proactively; don't re-ask for what's here.",
        f"Profile: {_profile_summary(profile)}",
    ]

    if documents:
        lines.append("Tracked renewals:")
        for d in documents[:12]:  # already ordered soonest-expiry first
            if d.expiry_date:
                n = (d.expiry_date - today).days
                when = (
                    f"expired {-n}d ago"
                    if n < 0
                    else "expires today"
                    if n == 0
                    else f"expires in {n}d ({d.expiry_date.isoformat()})"
                )
                due = (
                    " — DUE TO RENEW"
                    if renewals.needs_reminder(d.doc_type, d.expiry_date, today=today)
                    else ""
                )
            else:
                when, due = "no date set", ""
            est = " [estimated — confirm with user]" if d.confidence == renewals.ESTIMATED else ""
            lines.append(f"  - {d.title}: {when}{due}{est}")
    else:
        lines.append(
            "Tracked renewals: none yet. If the user mentions any document expiry, offer to "
            "track it — use set_visa_anchor when they give a visa/Emirates ID date."
        )

    open_items = [i for i in checklist if i.status != "done"]
    if open_items:
        lines.append("Open checklist tasks: " + "; ".join(i.title for i in open_items[:10]))

    upcoming = [d for d in deadlines if d.due_date >= today][:8]
    if upcoming:
        lines.append(
            "Upcoming reminders: "
            + "; ".join(f"{d.title} ({d.due_date.isoformat()})" for d in upcoming)
        )

    # Taste graph (events/services personalisation): the feed already re-ranks for
    # this user, so this line is just so the agent can *name* the preference when
    # it explains a pick ("a brunch in Marina, since you tend to like those").
    pref = await preferences.get_preference_profile(session, user_id)
    taste = preferences.memory_for_prompt(pref)
    if taste:
        lines.append(f"Lifestyle tastes (for find_events/find_services): {taste}")

    return "\n".join(lines)


async def run_agent(
    session: AsyncSession, user: User, message: str, history: list[dict] | None = None
) -> AgentResult:
    history = history or []

    if not settings.ai_enabled:
        return AgentResult(
            reply=(
                "The AI assistant isn't switched on yet — add ANTHROPIC_API_KEY to the "
                "backend .env and restart. Once enabled I can answer your Dubai questions "
                "with cited sources and manage your checklist and reminders."
            )
        )

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    profile = await get_or_create_profile(session, user.id)
    snapshot = await _workspace_snapshot(session, profile, user.id, today=date.today())

    system = [
        # Static instructions — cached across users/turns.
        {
            "type": "text",
            "text": build_system_prompt(),
            "cache_control": {"type": "ephemeral"},
        },
        # Per-user live snapshot — uncached (changes as the user's situation does).
        {"type": "text", "text": snapshot},
    ]

    messages: list[dict] = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": message})

    citations: list[dict] = []
    actions: list[dict] = []

    for _ in range(MAX_TURNS):
        resp = await client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=1024,
            system=system,
            tools=TOOLS,
            messages=messages,
        )

        if resp.stop_reason == "tool_use":
            assistant_content: list[dict] = []
            tool_results: list[dict] = []
            for block in resp.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append(
                        {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
                    )
                    try:
                        result, cites, action = await execute_tool(
                            block.name, block.input, session=session, user_id=user.id
                        )
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.warning("tool_error", tool=block.name, error=str(exc))
                        result, cites, action = {"ok": False, "error": str(exc)}, [], None
                    citations.extend(cites)
                    if action:
                        actions.append(action)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        }
                    )
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})
            continue

        reply = "".join(b.text for b in resp.content if b.type == "text").strip()
        seen, unique_citations = set(), []
        for c in citations:
            if c["url"] not in seen:
                seen.add(c["url"])
                unique_citations.append(c)
        return AgentResult(reply=reply, citations=unique_citations, actions=actions)

    return AgentResult(
        reply="I couldn't finish that in time — please try rephrasing.",
        citations=citations,
        actions=actions,
    )
