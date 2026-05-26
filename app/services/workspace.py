"""Workspace service — the single source of truth for profile / checklist /
deadline / lead mutations. Shared by the REST API *and* the AI agent's tools so
both paths behave identically.
"""

from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checklist import ChecklistItem
from app.models.deadline import Deadline
from app.models.document import Document
from app.models.event import Event
from app.models.favorite import Favorite
from app.models.lead import Lead
from app.models.profile import Profile
from app.models.service import ServiceProvider
from app.services import preferences, renewals


async def get_or_create_profile(session: AsyncSession, user_id: UUID) -> Profile:
    result = await session.execute(select(Profile).where(Profile.user_id == user_id))
    profile = result.scalar_one_or_none()
    if profile is None:
        profile = Profile(user_id=user_id, household={})
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
    return profile


async def update_profile(session: AsyncSession, user_id: UUID, fields: dict) -> Profile:
    profile = await get_or_create_profile(session, user_id)
    for key, value in fields.items():
        if value is not None and hasattr(profile, key):
            setattr(profile, key, value)
    await session.commit()
    await session.refresh(profile)
    return profile


async def list_checklist(session: AsyncSession, user_id: UUID) -> list[ChecklistItem]:
    result = await session.execute(
        select(ChecklistItem)
        .where(ChecklistItem.user_id == user_id)
        .order_by(ChecklistItem.created_at.desc())
    )
    return list(result.scalars().all())


async def add_checklist_item(
    session: AsyncSession,
    user_id: UUID,
    *,
    title: str,
    category: str | None = None,
    due_date: date | None = None,
    notes: str | None = None,
    source_url: str | None = None,
) -> ChecklistItem:
    item = ChecklistItem(
        user_id=user_id,
        title=title,
        category=category,
        due_date=due_date,
        notes=notes,
        source_url=source_url,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def update_checklist_item(
    session: AsyncSession, user_id: UUID, item_id: UUID, fields: dict
) -> ChecklistItem | None:
    item = await session.get(ChecklistItem, item_id)
    if item is None or item.user_id != user_id:
        return None
    for key, value in fields.items():
        if value is not None and hasattr(item, key):
            setattr(item, key, value)
    await session.commit()
    await session.refresh(item)
    return item


async def list_deadlines(session: AsyncSession, user_id: UUID) -> list[Deadline]:
    result = await session.execute(
        select(Deadline).where(Deadline.user_id == user_id).order_by(Deadline.due_date.asc())
    )
    return list(result.scalars().all())


async def add_deadline(
    session: AsyncSession,
    user_id: UUID,
    *,
    title: str,
    due_date: date,
    category: str | None = None,
    recurrence: str | None = None,
    source_url: str | None = None,
) -> Deadline:
    deadline = Deadline(
        user_id=user_id,
        title=title,
        due_date=due_date,
        category=category,
        recurrence=recurrence,
        source_url=source_url,
    )
    session.add(deadline)
    await session.commit()
    await session.refresh(deadline)
    return deadline


# ─── Renewals / tracked documents (see DOCUMENTS.md) ─────────────────────────
# A renewal is a document row holding only {doc_type, expiry_date, confidence} —
# the radar tracks dates, never the document itself.


async def list_documents(session: AsyncSession, user_id: UUID) -> list[Document]:
    result = await session.execute(
        select(Document)
        .where(Document.user_id == user_id)
        .order_by(Document.expiry_date.asc().nulls_last())
    )
    return list(result.scalars().all())


async def add_document(
    session: AsyncSession,
    user_id: UUID,
    *,
    doc_type: str,
    expiry_date: date | None = None,
    confidence: str = renewals.CONFIRMED,
    title: str | None = None,
    notes: str | None = None,
) -> Document:
    rt = renewals.get_type(doc_type)
    document = Document(
        user_id=user_id,
        doc_type=doc_type,
        title=title or (rt.label if rt else doc_type),
        expiry_date=expiry_date,
        confidence=confidence,
        notes=notes,
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document


async def update_document(
    session: AsyncSession, user_id: UUID, document_id: UUID, fields: dict
) -> Document | None:
    document = await session.get(Document, document_id)
    if document is None or document.user_id != user_id:
        return None
    # A user-supplied edit promotes an estimated date to confirmed unless told otherwise.
    if "expiry_date" in fields and "confidence" not in fields:
        fields["confidence"] = renewals.CONFIRMED
    for key, value in fields.items():
        if value is not None and hasattr(document, key):
            setattr(document, key, value)
    await session.commit()
    await session.refresh(document)
    return document


async def delete_document(session: AsyncSession, user_id: UUID, document_id: UUID) -> bool:
    document = await session.get(Document, document_id)
    if document is None or document.user_id != user_id:
        return False
    await session.delete(document)
    await session.commit()
    return True


async def apply_visa_anchor(
    session: AsyncSession, user_id: UUID, *, expiry_date: date
) -> list[Document]:
    """The anchor cascade: one visa expiry date populates the whole visa cluster.

    Idempotent per doc_type — re-running updates the existing row rather than
    duplicating it (so re-entering the date just corrects the cluster).
    """
    existing = {d.doc_type: d for d in await list_documents(session, user_id)}
    cluster: list[Document] = []
    for doc_type, confidence in renewals.VISA_ANCHOR_CASCADE:
        current = existing.get(doc_type)
        if current is None:
            cluster.append(
                await add_document(
                    session,
                    user_id,
                    doc_type=doc_type,
                    expiry_date=expiry_date,
                    confidence=confidence,
                )
            )
        else:
            updated = await update_document(
                session,
                user_id,
                current.id,
                {"expiry_date": expiry_date, "confidence": confidence},
            )
            if updated is not None:
                cluster.append(updated)
    return cluster


async def create_lead(
    session: AsyncSession, user_id: UUID, *, vertical: str, payload: dict
) -> Lead:
    lead = Lead(user_id=user_id, vertical=vertical, payload=payload or {})
    session.add(lead)
    await session.commit()
    await session.refresh(lead)
    # A lead is the strongest taste signal we get — feed it into the preference
    # memory (best-effort; never lets capture break lead creation).
    await preferences.capture_lead(session, user_id, vertical=vertical, payload=payload or {})
    return lead


# ─── Favorites / saved items ─────────────────────────────────────────────────
# A favorite is a polymorphic reference {item_type, item_id} — never a snapshot.
# Reads resolve to the *live* event/provider row and silently drop anything that
# no longer exists (an expired event you can't attend shouldn't linger).

EVENT = "event"
SERVICE = "service"


async def list_favorite_ids(session: AsyncSession, user_id: UUID) -> dict[str, list[UUID]]:
    """The bare ids the user has saved, bucketed by type — cheap heart hydration."""
    result = await session.execute(
        select(Favorite.item_type, Favorite.item_id)
        .where(Favorite.user_id == user_id)
        .order_by(Favorite.created_at.desc())
    )
    buckets: dict[str, list[UUID]] = {EVENT: [], SERVICE: []}
    for item_type, item_id in result.all():
        buckets.setdefault(item_type, []).append(item_id)
    return buckets


async def list_favorites(
    session: AsyncSession, user_id: UUID
) -> tuple[list[Event], list[ServiceProvider]]:
    """Saved items resolved to live rows, newest-saved first.

    Events are filtered by the same liveness rule as the public feed (published &
    not expired); services by `is_active`. Unresolvable saves are omitted.
    """
    ids = await list_favorite_ids(session, user_id)
    event_ids, service_ids = ids[EVENT], ids[SERVICE]

    events: list[Event] = []
    if event_ids:
        now = datetime.now(timezone.utc)
        rows = await session.execute(
            select(Event)
            .where(Event.id.in_(event_ids))
            .where(Event.is_published.is_(True))
            .where(or_(Event.expires_at.is_(None), Event.expires_at >= now))
        )
        by_id = {e.id: e for e in rows.scalars().all()}
        events = [by_id[i] for i in event_ids if i in by_id]  # preserve saved order

    services: list[ServiceProvider] = []
    if service_ids:
        rows = await session.execute(
            select(ServiceProvider)
            .where(ServiceProvider.id.in_(service_ids))
            .where(ServiceProvider.is_active.is_(True))
        )
        by_id = {s.id: s for s in rows.scalars().all()}
        services = [by_id[i] for i in service_ids if i in by_id]

    return events, services


async def add_favorite(
    session: AsyncSession, user_id: UUID, *, item_type: str, item_id: UUID
) -> bool:
    """Idempotent save. Returns True (favorited) regardless of prior state."""
    stmt = (
        pg_insert(Favorite)
        .values(user_id=user_id, item_type=item_type, item_id=item_id)
        .on_conflict_do_nothing(constraint="uq_favorites_user_item")
    )
    await session.execute(stmt)
    await session.commit()
    # Saving something is a strong taste signal — record it for the co-pilot's memory.
    await preferences.capture_favorite(session, user_id, item_type=item_type, item_id=item_id)
    return True


async def remove_favorite(
    session: AsyncSession, user_id: UUID, *, item_type: str, item_id: UUID
) -> bool:
    """Idempotent un-save. Returns False (not favorited)."""
    result = await session.execute(
        select(Favorite).where(
            Favorite.user_id == user_id,
            Favorite.item_type == item_type,
            Favorite.item_id == item_id,
        )
    )
    favorite = result.scalar_one_or_none()
    if favorite is not None:
        await session.delete(favorite)
        await session.commit()
        # Only a real un-save is a (mild negative) taste signal; removing something
        # that was never saved isn't.
        await preferences.capture_favorite(
            session, user_id, item_type=item_type, item_id=item_id, removed=True
        )
    return False
