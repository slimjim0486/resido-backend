"""Tool definitions + dispatcher for the agent.

Capabilities map to the requirement: READ (kb_search, advanced_search, get_profile,
list_checklist, list_deadlines, list_renewals, find_events, find_services), WRITE
(add_checklist_item, set_reminder, track_renewal, set_visa_anchor, create_lead,
remember_preference), EDIT (update_profile, update_checklist_item, update_renewal).
Mutations go through a shared service layer — `services.workspace` for the workspace
domains, `services.preferences` for taste memory — and the read tools call the same
service functions the REST API uses, so the agent and API never diverge.
"""

from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import events as events_service
from app.services import preferences as preferences_service
from app.services import providers as providers_service
from app.services import quotes as quotes_service
from app.services import workspace
from app.services.advanced_search import advanced_search
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
        "name": "advanced_search",
        "description": (
            "Advanced multi-source search for broad, current, comparative, or underspecified Dubai questions. "
            "Use this when one KB query is unlikely to be enough, when the user asks for latest/current information, "
            "or when you need several official-source angles. It combines verified KB results with optional live web results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Primary natural-language search query"},
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional alternate query angles or synonyms, max 4 total including query",
                },
                "category": {
                    "type": "string",
                    "description": "Optional category filter, e.g. visa, insurance, housing, transport, banking, schooling",
                },
                "include_live": {
                    "type": "boolean",
                    "description": "Whether to include live web results if KB coverage is thin. Default true.",
                },
                "official_only": {
                    "type": "boolean",
                    "description": "Restrict live web results to trusted official UAE/Dubai domains. Use for legal, fee, visa, deadline, or government-process questions.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum deduped results to return, 3-12. Default 8.",
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
        "name": "find_events",
        "description": (
            "Search Resido's curated, live Dubai lifestyle feed — things to do, dining, nightlife, "
            "shopping, family days out, and outdoor experiences. Use this for ANY 'what's on / what "
            "can we do' request and prefer it over web search for events. Supports budget, day, area, "
            "and kid-friendly filters; returns tappable listings with booking links. Translate the "
            "user's ask into filters (e.g. 'family event under AED 500 on Friday' → family_friendly=true, "
            "max_price_aed=500, weekday='friday'). Present results with date, venue and price, link each "
            "title to its url, and compare options using the returned timing, area, price, family suitability, "
            "tags/reason, and description. Then offer to set a reminder or add it to the checklist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free-text keywords, e.g. 'jazz', 'brunch', 'desert safari'. Optional.",
                },
                "category": {
                    "type": "string",
                    "description": "dining | events | nightlife | shopping | family | outdoors",
                },
                "area": {
                    "type": "string",
                    "description": "Dubai neighbourhood, e.g. 'Downtown', 'Dubai Marina'",
                },
                "family_friendly": {
                    "type": "boolean",
                    "description": "true to return only kid/family-suitable events",
                },
                "max_price_aed": {
                    "type": "number",
                    "description": "Budget ceiling in AED — keeps events at or under it (and free ones)",
                },
                "free_only": {"type": "boolean", "description": "true to return only free events"},
                "weekday": {
                    "type": "string",
                    "description": "Match a day of week, e.g. 'friday'. For 'this Friday' pass the weekday; for an exact date use date_from/date_to.",
                },
                "date_from": {"type": "string", "description": "Earliest event date, YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "Latest event date, YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "Max results, 1-20. Default 6."},
            },
        },
    },
    {
        "name": "find_services",
        "description": "Find ranked local service providers in Dubai (cleaning, AC repair, handyman, plumbing, electrician, movers, pest control, maid service, car service, laundry). Use this when the user needs a home/living service. Returns providers sorted by a trust score (rating weighted by review count); filter by area, minimum rating, or budget.",
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
                "min_rating": {
                    "type": "number",
                    "description": "Optional floor on the Google rating, e.g. 4.5",
                },
                "max_price_aed": {
                    "type": "number",
                    "description": "Optional AED budget — keeps providers at or under this advertised price (and those with no listed price)",
                },
                "limit": {"type": "integer", "description": "Max providers, 1-10. Default 5."},
            },
            "required": ["category"],
        },
    },
    {
        "name": "create_lead",
        "description": (
            "Capture a high-intent lead with the user's explicit consent. Two modes. "
            "MODE 1 — specific provider: pass the `provider_id` from a find_services result plus a short "
            "`need` (e.g. 'shower drain cleaning'). This drafts a WhatsApp/SMS message the USER sends "
            "themselves and returns the provider's verified contact — it does NOT contact the provider for "
            "them, so never say it was 'sent' or that they'll be called. "
            "MODE 2 — high-value vertical with no specific provider yet (insurance | banking | schooling | "
            "real_estate | telecom | relocation): pass `vertical` (and optional `payload` notes). "
            "Never invent provider contact details; they only come from this tool or find_services."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "provider_id": {
                    "type": "string",
                    "description": "MODE 1: the `id` of a provider returned by find_services to request a quote from.",
                },
                "need": {
                    "type": "string",
                    "description": "MODE 1: short description of the job, e.g. 'fix shower drainage'. Required with provider_id.",
                },
                "when_pref": {
                    "type": "string",
                    "description": "MODE 1: optional preferred timing, e.g. 'this weekend', 'asap'.",
                },
                "budget_aed": {
                    "type": "integer",
                    "description": "MODE 1: optional budget in AED.",
                },
                "vertical": {
                    "type": "string",
                    "description": "MODE 2: a high-value vertical (insurance | banking | schooling | real_estate | telecom | relocation).",
                },
                "payload": {
                    "type": "object",
                    "description": "MODE 2: details to pass along, e.g. {area, contact_preference, notes}.",
                },
            },
        },
    },
    {
        "name": "remember_preference",
        "description": (
            "Save a durable lifestyle taste, preference, or constraint the user states that the "
            "events/services filters can't capture on their own — e.g. 'I'm vegetarian', 'we hate "
            "loud or crowded places', 'we love the beach', 'prefer outdoor seating', 'no alcohol'. "
            "This sharpens future find_events/find_services suggestions. Use ONLY for LASTING "
            "preferences about leisure/dining/services that the user clearly stated; NOT for one-off "
            "requests ('tonight we want sushi'), and NOT for visa/legal/document facts (use "
            "update_profile or track_renewal for those)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The preference as a short third-person phrase, e.g. 'Vegetarian', 'Dislikes loud venues', 'Loves beach days', 'Prefers budget-friendly options'.",
                },
            },
            "required": ["text"],
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

    if name == "advanced_search":
        result = await advanced_search(
            session,
            tool_input["query"],
            queries=tool_input.get("queries"),
            category=tool_input.get("category"),
            limit=tool_input.get("limit"),
            include_live=tool_input.get("include_live", True),
            official_only=tool_input.get("official_only", False),
        )
        return (
            {
                "results": result["results"],
                "count": result["count"],
                "verified_count": result["verified_count"],
                "live_count": result["live_count"],
                "queries": result["queries"],
                "source_note": result["source_note"],
            },
            result["citations"],
            None,
        )

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

    if name == "find_events":
        limit = max(1, min(int(tool_input.get("limit") or 6), 20))
        personalize = await preferences_service.get_preference_profile(session, user_id)
        events = await events_service.search_events(
            session,
            query=tool_input.get("query"),
            category=tool_input.get("category"),
            area=tool_input.get("area"),
            family_friendly=tool_input.get("family_friendly"),
            max_price=tool_input.get("max_price_aed"),
            free_only=bool(tool_input.get("free_only")),
            weekday=tool_input.get("weekday"),
            date_from=_parse_date(tool_input.get("date_from")),
            date_to=_parse_date(tool_input.get("date_to")),
            limit=limit,
            personalize=personalize,
        )
        explain_event = getattr(preferences_service, "explain_event", None)
        return (
            {
                "events": [
                    {
                        "title": e.title,
                        "description": e.description[:500] if e.description else None,
                        "category": e.category,
                        "venue": e.venue,
                        "area": e.area,
                        "starts_at": e.starts_at.isoformat() if e.starts_at else None,
                        "ends_at": e.ends_at.isoformat() if e.ends_at else None,
                        "price_from": e.price_from,
                        "price_min_aed": float(e.price_min) if e.price_min is not None else None,
                        "is_free": (float(e.price_min) == 0) if e.price_min is not None else None,
                        "price_certainty": "listed" if e.price_from else "not_listed",
                        "family_friendly": e.family_friendly,
                        "tags": getattr(e, "tags", None) or [],
                        "why_ranked": explain_event(personalize, e) if explain_event else None,
                        "source": e.source,
                        "expires_at": e.expires_at.isoformat() if e.expires_at else None,
                        "url": e.url,
                    }
                    for e in events
                ],
                "count": len(events),
                "note": (
                    "Live listings from Resido's curated feed (recommendations, not legal facts — no "
                    "verified-source citation needed). For analysis or comparisons, weigh timing, area, "
                    "price certainty, family suitability, category/tags, venue, description, and why_ranked. "
                    "Lead with the best fit and the tradeoff, not a raw list. Link each title to its url. "
                    "Say when a price isn't listed (price_from null). For a dated event, offer to "
                    "set_reminder or add_checklist_item. If nothing fits, loosen one filter rather than "
                    "inventing events."
                ),
            },
            [],
            None,
        )

    if name == "find_services":
        limit = max(1, min(int(tool_input.get("limit") or 5), 10))
        personalize = await preferences_service.get_preference_profile(session, user_id)
        items = await providers_service.list_providers(
            session,
            category=tool_input["category"],
            area=tool_input.get("area"),
            area_soft=True,
            min_rating=tool_input.get("min_rating"),
            max_price=tool_input.get("max_price_aed"),
            limit=limit,
            personalize=personalize,
        )
        return (
            {
                "providers": [
                    {
                        "id": str(p.id),
                        "name": p.name,
                        "area": p.area,
                        "rating": p.rating,
                        "reviews_count": p.reviews_count,
                        "trust_score": round(p.score, 2),
                        "price_from": p.price_from,
                        "price_to": p.price_to,
                        "price_unit": p.price_unit,
                        "price_notes": p.price_notes,
                        "highlights": p.highlights,
                        "phone": p.phone,
                        "whatsapp": p.whatsapp,
                        "website": p.website,
                        "maps_url": p.maps_url,
                    }
                    for p in items
                ],
                "count": len(items),
                "note": "Ranked by trust score (rating weighted by review count). Don't dump the list — "
                "lead with the single best pick (1-2 max), giving its rating + review count as the trust "
                "signal and the advertised price when present (say so when it isn't). Area is a soft boost, "
                "not a filter: results may sit outside the user's exact area (home services travel) — if so, "
                "say the provider covers/comes out to their area rather than implying it's local. Then drive "
                "to the action: offer to draft a quote via create_lead (pass the chosen provider's `id` and a "
                "short `need`) with the user's consent — this drafts a message they send themselves; it does "
                "NOT contact the provider for them. Only quote a provider's phone/website using the values in "
                "this result; never invent contact details.",
            },
            [],
            None,
        )

    if name == "create_lead":
        provider_id_raw = tool_input.get("provider_id")
        if provider_id_raw:
            # MODE 1 — quote draft for a specific provider. Contact is loaded
            # server-side so the agent can never invent it.
            try:
                provider_id = UUID(provider_id_raw)
            except (ValueError, TypeError):
                return {"ok": False, "error": "invalid provider_id"}, [], None
            provider = await providers_service.get_provider(session, provider_id)
            if provider is None:
                return (
                    {"ok": False, "error": "provider not found — call find_services to get a current provider_id"},
                    [],
                    None,
                )
            need = (tool_input.get("need") or "").strip()
            if not need:
                return (
                    {"ok": False, "error": "need is required with provider_id — a short description of the job"},
                    [],
                    None,
                )
            budget = tool_input.get("budget_aed")
            result = await quotes_service.draft_quote(
                session,
                user_id,
                provider=provider,
                need=need,
                when_pref=tool_input.get("when_pref"),
                budget_aed=int(budget) if budget is not None else None,
            )
            return (
                {
                    "ok": True,
                    "lead_id": str(result["lead_id"]),
                    "draft_message": result["message"],
                    "channel": result["channel"],  # whatsapp | sms | none
                    "to_number": result["to_number"],
                    "provider": {
                        "name": provider.name,
                        "phone": provider.phone,
                        "whatsapp": provider.whatsapp,
                        "website": provider.website,
                    },
                    "note": (
                        "Lead logged (demand signal). NOTHING was sent to the provider and no callback is "
                        "scheduled. The app shows a 'Send on WhatsApp'/'Send via SMS' button under your "
                        "reply that opens `draft_message` pre-filled — so just tell the user you've drafted "
                        "a message to {provider} and they can tap to send it themselves. Do NOT say 'sent', "
                        "'submitted', or 'they'll reach out/call you'. Share ONLY the contact in this result "
                        "(to_number, provider.phone/whatsapp/website) — never invent or guess a number, "
                        "email, or website. If channel is 'none', say there's no direct number on file and "
                        "offer the website only if present."
                    ),
                },
                [],
                {
                    "type": "lead_created",
                    "summary": f"Quote drafted for {provider.name}",
                    "channel": result["channel"],
                    "to_number": result["to_number"],
                    "message": result["message"],
                    "provider_name": provider.name,
                },
            )

        # MODE 2 — high-value vertical with no specific provider yet.
        vertical = (tool_input.get("vertical") or "").strip()
        if not vertical:
            return (
                {"ok": False, "error": "pass provider_id (a find_services provider) or vertical (a high-value lead)"},
                [],
                None,
            )
        lead = await workspace.create_lead(
            session, user_id, vertical=vertical, payload=tool_input.get("payload") or {}
        )
        return (
            {
                "ok": True,
                "id": str(lead.id),
                "note": (
                    "Lead recorded. Don't claim a message was sent to any business or promise a specific "
                    "callback time, and don't state any phone/email/website you didn't get from a tool result."
                ),
            },
            [],
            {"type": "lead_created", "summary": f"Logged your {lead.vertical} request"},
        )

    if name == "remember_preference":
        text = (tool_input.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "text is required"}, [], None
        await preferences_service.add_preference_note(
            session, user_id, text=text, source="chat"
        )
        return (
            {"ok": True, "note": "Saved as a lasting preference; it will shape future event/service suggestions."},
            [],
            {"type": "preference_remembered", "summary": f"Got it — I'll remember: {text}"},
        )

    return {"ok": False, "error": f"unknown tool {name}"}, [], None
