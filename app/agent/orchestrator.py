"""Claude tool-use loop. Degrades gracefully when ANTHROPIC_API_KEY is unset."""

import json
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import build_system_prompt
from app.agent.tools import TOOLS, execute_tool
from app.config import settings
from app.core.logging import get_logger
from app.models.profile import Profile
from app.models.user import User
from app.services.workspace import get_or_create_profile

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

    system = [
        {
            "type": "text",
            "text": build_system_prompt(_profile_summary(profile)),
            "cache_control": {"type": "ephemeral"},
        }
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
