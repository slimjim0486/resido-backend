"""Tool definitions + dispatcher for the agent.

Capabilities map to the requirement: READ (kb_search, get_profile, list_checklist,
list_deadlines), WRITE (add_checklist_item, set_reminder, create_lead), EDIT
(update_profile, update_checklist_item). All mutations go through the shared
`services.workspace` layer so behavior matches the REST API exactly.
"""

from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import workspace
from app.services.kb import search_kb

# ─── Anthropic tool schemas ───────────────────────────────────────────────
TOOLS: list[dict] = [
    {
        "name": "kb_search",
        "description": "Search the curated, verified Dubai knowledge base. ALWAYS use this before answering factual questions about official processes, fees, documents, or deadlines.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language search query"},
                "category": {
                    "type": "string",
                    "description": "Optional category filter, e.g. visa, insurance, housing, transport, banking, schooling",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_profile",
        "description": "Read the user's saved profile/household context.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "update_profile",
        "description": "Save durable facts about the user (nationality, emirate, visa_type, arrival_date YYYY-MM-DD, employer, household dict, notes).",
        "input_schema": {
            "type": "object",
            "properties": {
                "nationality": {"type": "string"},
                "emirate": {"type": "string"},
                "visa_type": {"type": "string"},
                "arrival_date": {"type": "string", "description": "YYYY-MM-DD"},
                "employer": {"type": "string"},
                "household": {"type": "object"},
                "notes": {"type": "string"},
            },
        },
    },
    {
        "name": "list_checklist",
        "description": "List the user's current 'Dubai life' checklist items.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_checklist_item",
        "description": "Add a task to the user's checklist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "category": {"type": "string"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "notes": {"type": "string"},
                "source_url": {"type": "string"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_checklist_item",
        "description": "Update a checklist item (e.g. mark status done). Requires item_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "title": {"type": "string"},
                "status": {"type": "string", "enum": ["todo", "doing", "done"]},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "notes": {"type": "string"},
            },
            "required": ["item_id"],
        },
    },
    {
        "name": "list_deadlines",
        "description": "List the user's reminders/deadlines.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_reminder",
        "description": "Create a dated reminder/deadline (visa renewal, fine due date, school term, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "category": {"type": "string"},
                "recurrence": {"type": "string", "enum": ["none", "yearly", "monthly"]},
                "source_url": {"type": "string"},
            },
            "required": ["title", "due_date"],
        },
    },
    {
        "name": "create_lead",
        "description": "With the user's explicit consent, create a service lead to connect them with a vetted partner.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vertical": {
                    "type": "string",
                    "description": "insurance | banking | schooling | real_estate | telecom | relocation",
                },
                "payload": {"type": "object", "description": "Any details to pass along"},
            },
            "required": ["vertical"],
        },
    },
]


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


async def execute_tool(
    name: str, tool_input: dict, *, session: AsyncSession, user_id: UUID
) -> tuple[dict, list[dict], dict | None]:
    """Returns (result_for_model, citations, action)."""

    if name == "kb_search":
        chunks = await search_kb(
            session, tool_input["query"], category=tool_input.get("category"), limit=5
        )
        citations, results = [], []
        for c in chunks:
            verified = c.fetched_at.date().isoformat() if c.fetched_at else None
            citations.append(
                {"title": c.title, "url": c.url, "category": c.category, "fetched_at": verified}
            )
            results.append(
                {
                    "title": c.title,
                    "url": c.url,
                    "category": c.category,
                    "verified": verified,
                    "content": c.content[:1500],
                }
            )
        return {"results": results, "count": len(results)}, citations, None

    if name == "get_profile":
        p = await workspace.get_or_create_profile(session, user_id)
        return (
            {
                "nationality": p.nationality,
                "emirate": p.emirate,
                "visa_type": p.visa_type,
                "arrival_date": p.arrival_date.isoformat() if p.arrival_date else None,
                "employer": p.employer,
                "household": p.household,
                "notes": p.notes,
            },
            [],
            None,
        )

    if name == "update_profile":
        fields = dict(tool_input)
        if "arrival_date" in fields:
            fields["arrival_date"] = _parse_date(fields["arrival_date"])
        await workspace.update_profile(session, user_id, fields)
        return {"ok": True}, [], {"type": "profile_updated", "summary": "Updated your profile"}

    if name == "list_checklist":
        items = await workspace.list_checklist(session, user_id)
        return (
            {
                "items": [
                    {
                        "id": str(i.id),
                        "title": i.title,
                        "status": i.status,
                        "category": i.category,
                        "due_date": i.due_date.isoformat() if i.due_date else None,
                    }
                    for i in items
                ]
            },
            [],
            None,
        )

    if name == "add_checklist_item":
        item = await workspace.add_checklist_item(
            session,
            user_id,
            title=tool_input["title"],
            category=tool_input.get("category"),
            due_date=_parse_date(tool_input.get("due_date")),
            notes=tool_input.get("notes"),
            source_url=tool_input.get("source_url"),
        )
        return (
            {"ok": True, "id": str(item.id)},
            [],
            {"type": "checklist_added", "summary": f"Added “{item.title}” to your checklist"},
        )

    if name == "update_checklist_item":
        try:
            item_id = UUID(tool_input["item_id"])
        except (ValueError, KeyError):
            return {"ok": False, "error": "invalid item_id"}, [], None
        fields = {k: v for k, v in tool_input.items() if k != "item_id"}
        if "due_date" in fields:
            fields["due_date"] = _parse_date(fields["due_date"])
        item = await workspace.update_checklist_item(session, user_id, item_id, fields)
        if item is None:
            return {"ok": False, "error": "not found"}, [], None
        return {"ok": True}, [], {"type": "checklist_updated", "summary": f"Updated “{item.title}”"}

    if name == "list_deadlines":
        items = await workspace.list_deadlines(session, user_id)
        return (
            {
                "items": [
                    {"title": d.title, "due_date": d.due_date.isoformat(), "category": d.category}
                    for d in items
                ]
            },
            [],
            None,
        )

    if name == "set_reminder":
        due = _parse_date(tool_input.get("due_date"))
        if due is None:
            return {"ok": False, "error": "due_date must be YYYY-MM-DD"}, [], None
        d = await workspace.add_deadline(
            session,
            user_id,
            title=tool_input["title"],
            due_date=due,
            category=tool_input.get("category"),
            recurrence=tool_input.get("recurrence"),
            source_url=tool_input.get("source_url"),
        )
        return (
            {"ok": True, "id": str(d.id)},
            [],
            {"type": "reminder_set", "summary": f"Reminder set: {d.title} on {d.due_date.isoformat()}"},
        )

    if name == "create_lead":
        lead = await workspace.create_lead(
            session, user_id, vertical=tool_input["vertical"], payload=tool_input.get("payload") or {}
        )
        return (
            {"ok": True, "id": str(lead.id)},
            [],
            {"type": "lead_created", "summary": f"Connecting you with a {lead.vertical} partner"},
        )

    return {"ok": False, "error": f"unknown tool {name}"}, [], None
