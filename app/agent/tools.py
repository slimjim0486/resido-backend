"""Tool definitions + dispatcher for the agent.

Capabilities map to the requirement: READ (kb_search, get_profile, list_checklist,
list_deadlines, list_renewals), WRITE (add_checklist_item, set_reminder,
track_renewal, set_visa_anchor, create_lead), EDIT (update_profile,
update_checklist_item, update_renewal). All mutations go through the shared
`services.workspace` layer so behavior matches the REST API exactly.
"""

from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import providers as providers_service
from app.services import workspace
from app.services.kb import search_kb
from app.services.live_search import live_search

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
        "name": "list_renewals",
        "description": "List the documents/renewals the user is tracking (visa, Emirates ID, car registration, insurance, tenancy, etc.) with their expiry dates.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "track_renewal",
        "description": "Track a document's expiry date so we can remind the user before it lapses (and avoid fines). We track DATES ONLY — never ask for or store ID numbers, scans, or copies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_type": {
                    "type": "string",
                    "description": "visa | emirates_id | passport | uae_driving_license | car_registration | motor_insurance | health_insurance | ejari_tenancy | trade_license | domestic_worker_visa | domestic_worker_insurance",
                },
                "expiry_date": {"type": "string", "description": "YYYY-MM-DD"},
                "confidence": {
                    "type": "string",
                    "enum": ["confirmed", "estimated"],
                    "description": "Use 'estimated' if the user didn't state the date directly; defaults to 'confirmed'.",
                },
                "notes": {"type": "string"},
            },
            "required": ["doc_type"],
        },
    },
    {
        "name": "update_renewal",
        "description": "Update a tracked renewal's expiry date or notes. Requires document_id (from list_renewals).",
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "expiry_date": {"type": "string", "description": "YYYY-MM-DD"},
                "confidence": {"type": "string", "enum": ["confirmed", "estimated"]},
                "notes": {"type": "string"},
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "set_visa_anchor",
        "description": "Given the user's residency visa expiry date, set up their whole visa cluster in one step — visa, Emirates ID, and health insurance (the latter two as estimated, same date). Offer this the moment a user mentions when their visa expires.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expiry_date": {"type": "string", "description": "Visa expiry, YYYY-MM-DD"},
            },
            "required": ["expiry_date"],
        },
    },
    {
        "name": "find_services",
        "description": "Find ranked local service providers in Dubai (cleaning, AC repair, handyman, plumbing, electrician, movers, pest control, maid service, car service, laundry). Use this when the user needs a home/living service. Returns providers sorted by a trust score (rating weighted by review count).",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "cleaning | ac_repair | handyman | plumbing | electrician | movers | pest_control | maid_service | car_service | laundry",
                },
                "area": {
                    "type": "string",
                    "description": "Optional Dubai area filter, e.g. 'Dubai Marina', 'JLT', 'Business Bay'",
                },
            },
            "required": ["category"],
        },
    },
    {
        "name": "create_lead",
        "description": "With the user's explicit consent, create a service lead to connect them with a vetted partner — including a callback/quote request for a local home-service provider returned by find_services.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vertical": {
                    "type": "string",
                    "description": "A service category (cleaning, ac_repair, movers, …) or high-value vertical (insurance | banking | schooling | real_estate | telecom | relocation)",
                },
                "payload": {
                    "type": "object",
                    "description": "Details to pass along, e.g. {provider_name, area, contact_preference, notes}",
                },
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
        query, category = tool_input["query"], tool_input.get("category")
        chunks = await search_kb(session, query, category=category, limit=5)
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

        source_note = "Verified knowledge base."
        if not results:
            # Tier C: KB miss → just-in-time live web search. Authoritative hits
            # are queued for ingestion into the verified KB on the next refresh.
            for r in await live_search(session, query, category=category):
                # No fetched_at on the citation: these aren't verified KB sources,
                # so the chip won't claim a "verified" date.
                citations.append(
                    {"title": r["title"], "url": r["url"], "category": r["category"], "fetched_at": None}
                )
                results.append(r)
            source_note = (
                "No verified KB entry — these are LIVE web results, not yet verified. "
                "Tell the user they're fresh from the web and should be confirmed "
                "against the official source before acting on fees/legal/deadline details."
            )
        return {"results": results, "count": len(results), "source_note": source_note}, citations, None

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

    if name == "list_renewals":
        docs = await workspace.list_documents(session, user_id)
        return (
            {
                "renewals": [
                    {
                        "id": str(d.id),
                        "doc_type": d.doc_type,
                        "title": d.title,
                        "expiry_date": d.expiry_date.isoformat() if d.expiry_date else None,
                        "confidence": d.confidence,
                    }
                    for d in docs
                ]
            },
            [],
            None,
        )

    if name == "track_renewal":
        doc = await workspace.add_document(
            session,
            user_id,
            doc_type=tool_input["doc_type"],
            expiry_date=_parse_date(tool_input.get("expiry_date")),
            confidence=tool_input.get("confidence") or "confirmed",
            notes=tool_input.get("notes"),
        )
        return (
            {"ok": True, "id": str(doc.id)},
            [],
            {"type": "renewal_tracked", "summary": f"Now tracking your {doc.title}"},
        )

    if name == "update_renewal":
        try:
            document_id = UUID(tool_input["document_id"])
        except (ValueError, KeyError):
            return {"ok": False, "error": "invalid document_id"}, [], None
        fields = {k: v for k, v in tool_input.items() if k != "document_id"}
        if "expiry_date" in fields:
            fields["expiry_date"] = _parse_date(fields["expiry_date"])
        doc = await workspace.update_document(session, user_id, document_id, fields)
        if doc is None:
            return {"ok": False, "error": "not found"}, [], None
        return {"ok": True}, [], {"type": "renewal_updated", "summary": f"Updated your {doc.title}"}

    if name == "set_visa_anchor":
        expiry = _parse_date(tool_input.get("expiry_date"))
        if expiry is None:
            return {"ok": False, "error": "expiry_date must be YYYY-MM-DD"}, [], None
        cluster = await workspace.apply_visa_anchor(session, user_id, expiry_date=expiry)
        return (
            {
                "ok": True,
                "tracked": [{"title": d.title, "confidence": d.confidence} for d in cluster],
            },
            [],
            {
                "type": "visa_anchor_set",
                "summary": f"Set up {len(cluster)} visa renewals (Emirates ID & insurance estimated — confirm anytime)",
            },
        )

    if name == "find_services":
        items = await providers_service.list_providers(
            session,
            category=tool_input["category"],
            area=tool_input.get("area"),
            limit=5,
        )
        return (
            {
                "providers": [
                    {
                        "name": p.name,
                        "area": p.area,
                        "rating": p.rating,
                        "reviews_count": p.reviews_count,
                        "phone": p.phone,
                        "whatsapp": p.whatsapp,
                        "website": p.website,
                    }
                    for p in items
                ],
                "count": len(items),
                "note": "Ranked by trust score (rating weighted by review count). "
                "Offer to request a callback/quote via create_lead with the user's consent.",
            },
            [],
            None,
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
