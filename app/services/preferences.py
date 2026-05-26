"""Preference memory — the co-pilot's taste graph. See backend/PERSONALIZATION.md.

Three responsibilities, all deterministic in Phase 1 (no AI key needed):

1. CAPTURE  — turn a favorite / lead into a ``PreferenceSignal`` whose ``attrs``
   carry the item's preference-relevant facets (so the taste survives the item).
2. RECOMPUTE — fold the signals into the 1:1 ``PreferenceProfile``: time-decayed
   weight maps, a typical budget, a family lean, and a templated summary.
3. RANK     — re-rank a candidate list of events/providers for a user, blending
   their taste with the base ordering (recency for events, Bayesian trust for
   services). The strength scales with how much we actually know (shrinkage), so
   a brand-new user is barely nudged and falls back to the base ranking.

Phase 2 swaps the templated summary + notes for a Haiku distillation; the schema
and the ranker don't change. The whole module no-ops cleanly when a profile is
absent or the user has paused personalisation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.pricing import parse_aed
from app.models.event import Event
from app.models.preference import PreferenceProfile, PreferenceSignal
from app.models.profile import Profile
from app.models.service import ServiceProvider

logger = get_logger(__name__)

_DUBAI_TZ = ZoneInfo("Asia/Dubai")

# Base strength per signal kind, before time decay. A requested lead is the
# strongest taste signal (real intent + money), a save is strong, a view is weak;
# undoing a save or dismissing is a (smaller) negative.
SIGNAL_WEIGHTS: dict[str, float] = {
    "lead": 5.0,
    "favorite": 3.0,
    "contact": 2.0,
    "view": 1.0,
    "search": 0.5,
    "unfavorite": -1.5,
    "dismiss": -2.0,
}

# Recent behaviour should outweigh old behaviour: a signal's weight halves every
# this-many days. ~2 months keeps a season's worth of taste live without letting
# last-year's phase dominate.
HALF_LIFE_DAYS = 60.0

# Personalisation confidence (shrinkage): alpha = n / (n + K). With K=5, ~5
# signals → 0.5 strength, ~15 → 0.75. Below that we lean on the base ranking.
ALPHA_K = 5.0

# Component weights inside an event taste-match (sum ≈ 1).
_EV_W = {"category": 0.40, "area": 0.25, "family": 0.15, "weekday": 0.10, "budget": 0.10}
# Component weights inside a service taste-match (location dominates for trades).
_SV_W = {"area": 0.50, "category": 0.30, "budget": 0.20}
# Services: trust must dominate, so taste can move a provider's effective score by
# at most ±(this · alpha) — a tiebreaker among comparably-trusted providers, not an
# override (a clear trust gap, e.g. 4.3 vs 4.6, survives even a perfect taste match).
_PROVIDER_BOOST = 0.12

# Ignore weight-map entries below this after normalisation (keeps maps tidy).
_MIN_WEIGHT = 0.05


# ─── Profile lifecycle ───────────────────────────────────────────────────────


async def get_or_create_preference_profile(
    session: AsyncSession, user_id: UUID
) -> PreferenceProfile:
    result = await session.execute(
        select(PreferenceProfile).where(PreferenceProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        profile = PreferenceProfile(user_id=user_id)
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
    return profile


async def get_preference_profile(
    session: AsyncSession, user_id: UUID
) -> PreferenceProfile | None:
    """Read-only fetch for the ranker — never creates a row (the feed shouldn't
    write on read). Returns None when the user has no taste graph yet."""
    result = await session.execute(
        select(PreferenceProfile).where(PreferenceProfile.user_id == user_id)
    )
    return result.scalar_one_or_none()


# ─── Capture ─────────────────────────────────────────────────────────────────


async def record_signal(
    session: AsyncSession,
    user_id: UUID,
    *,
    kind: str,
    domain: str,
    item_id: UUID | None = None,
    attrs: dict | None = None,
) -> PreferenceSignal:
    """Append one behavioural signal. ``weight`` is the base strength for ``kind``
    (decay is applied at recompute, not here). Commits."""
    signal = PreferenceSignal(
        user_id=user_id,
        kind=kind,
        domain=domain,
        item_id=item_id,
        attrs=_clean_attrs(attrs or {}),
        weight=SIGNAL_WEIGHTS.get(kind, 1.0),
    )
    session.add(signal)
    await session.commit()
    return signal


def _clean_attrs(attrs: dict) -> dict:
    """Keep only JSON-serialisable, preference-relevant facets."""
    out: dict = {}
    for key in ("category", "area", "family_friendly", "weekday", "query", "tags"):
        if attrs.get(key) is not None:
            out[key] = attrs[key]
    pm = attrs.get("price_min")
    if pm is not None:
        try:
            out["price_min"] = float(pm)
        except (TypeError, ValueError):
            pass
    return out


def _event_weekday(event: Event) -> int | None:
    """Dubai-local day-of-week (Sun=0 … Sat=6) for an event's start, matching the
    convention used by events.search_events; None when undated."""
    if event.starts_at is None:
        return None
    starts = event.starts_at
    if starts.tzinfo is None:
        starts = starts.replace(tzinfo=timezone.utc)
    # Python weekday(): Mon=0 … Sun=6. Postgres extract('dow'): Sun=0 … Sat=6.
    return (starts.astimezone(_DUBAI_TZ).weekday() + 1) % 7


def _event_attrs(event: Event) -> dict:
    return {
        "category": event.category,
        "area": event.area,
        "family_friendly": event.family_friendly,
        "weekday": _event_weekday(event),
        "price_min": float(event.price_min) if event.price_min is not None else None,
    }


def _provider_attrs(provider: ServiceProvider) -> dict:
    return {
        "category": provider.category,
        "area": provider.area,
        "price_min": parse_aed(provider.price_from),
    }


async def capture_favorite(
    session: AsyncSession,
    user_id: UUID,
    *,
    item_type: str,
    item_id: UUID,
    removed: bool = False,
) -> None:
    """Record a (un)favorite as a taste signal, then refresh the profile.

    Resolves the event/provider to denormalise its facets into the signal. If the
    item can't be resolved (already gone) we skip silently — a save we can't
    explain isn't worth a dangling signal. Best-effort: never raises into the
    favorite mutation it's attached to."""
    try:
        domain = "event" if item_type == "event" else "service"
        if domain == "event":
            row = await session.get(Event, item_id)
            attrs = _event_attrs(row) if row else None
        else:
            row = await session.get(ServiceProvider, item_id)
            attrs = _provider_attrs(row) if row else None
        if attrs is None:
            return
        await record_signal(
            session,
            user_id,
            kind="unfavorite" if removed else "favorite",
            domain=domain,
            item_id=item_id,
            attrs=attrs,
        )
        await recompute_profile(session, user_id)
    except Exception as exc:  # taste capture must never break the core mutation
        logger.warning("preference_capture_failed", kind="favorite", error=str(exc))


async def capture_lead(
    session: AsyncSession,
    user_id: UUID,
    *,
    vertical: str,
    payload: dict,
) -> None:
    """Record a lead as a (strong) taste signal, then refresh the profile.

    The lead's ``vertical`` is the service category (MODE 1) or a high-value
    vertical (MODE 2); either way it feeds category affinity. Area/budget come
    from the payload. Best-effort."""
    try:
        await record_signal(
            session,
            user_id,
            kind="lead",
            domain="service",
            attrs={
                "category": vertical,
                "area": payload.get("area"),
                "price_min": payload.get("budget_aed"),
            },
        )
        await recompute_profile(session, user_id)
    except Exception as exc:
        logger.warning("preference_capture_failed", kind="lead", error=str(exc))


# ─── Recompute (rules engine) ──────────────────────────────────────────────────


def _normalise(weights: dict[str, float]) -> dict[str, float]:
    """Scale a tally so the largest magnitude is 1.0 (sign preserved); drop noise."""
    if not weights:
        return {}
    peak = max(abs(v) for v in weights.values()) or 1.0
    out = {k: round(v / peak, 3) for k, v in weights.items()}
    return {k: v for k, v in out.items() if abs(v) >= _MIN_WEIGHT}


async def recompute_profile(session: AsyncSession, user_id: UUID) -> PreferenceProfile:
    """Fold all of a user's signals into their PreferenceProfile (rules-only).

    Deterministic and idempotent: re-running over the same signals yields the same
    maps. Time decay means the result shifts as signals age even without new ones.
    Commits the updated profile."""
    now = datetime.now(timezone.utc)
    signals = (
        await session.execute(
            select(PreferenceSignal).where(PreferenceSignal.user_id == user_id)
        )
    ).scalars().all()

    area: dict[str, float] = {}
    event_cat: dict[str, float] = {}
    service_aff: dict[str, float] = {}
    tags: dict[str, float] = {}
    weekday: dict[str, float] = {}
    budget_num = 0.0  # Σ decayed_weight · price
    budget_den = 0.0  # Σ decayed_weight
    family_pos = 0.0
    family_neg = 0.0

    def bump(d: dict[str, float], key, w: float) -> None:
        if key is None or key == "":
            return
        d[str(key)] = d.get(str(key), 0.0) + w

    for s in signals:
        created = s.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = max((now - created).total_seconds() / 86400.0, 0.0)
        w = s.weight * (0.5 ** (age_days / HALF_LIFE_DAYS))
        a = s.attrs or {}

        bump(area, a.get("area"), w)
        if s.domain == "event":
            bump(event_cat, a.get("category"), w)
            bump(weekday, a.get("weekday"), w)
            for t in a.get("tags") or []:
                bump(tags, t, w)
            ff = a.get("family_friendly")
            if ff is True:
                family_pos += max(w, 0.0)
            elif ff is False:
                family_neg += max(w, 0.0)
        else:  # service
            bump(service_aff, a.get("category"), w)

        pm = a.get("price_min")
        if w > 0 and pm is not None:
            budget_num += w * float(pm)
            budget_den += w

    profile = await get_or_create_preference_profile(session, user_id)
    profile.area_weights = _normalise(area)
    profile.event_category_weights = _normalise(event_cat)
    profile.service_category_affinity = _normalise(service_aff)
    profile.tag_weights = _normalise(tags)
    profile.weekday_weights = _normalise(weekday)
    profile.typical_budget_aed = round(budget_num / budget_den, 2) if budget_den > 0 else None

    # Family lean: clear behavioural signal wins; otherwise fall back to household.
    if family_pos > family_neg * 1.5 and family_pos > 0:
        profile.family_bias = True
    elif family_neg > family_pos * 1.5 and family_neg > 0:
        profile.family_bias = False
    else:
        profile.family_bias = await _household_family_lean(session, user_id)

    profile.signal_count = len(signals)
    # Once the Haiku cron has written a richer summary, it owns the field — don't
    # clobber it on every favorite. Until then (or after a reset) we keep the
    # rules-templated summary fresh. (reset_preferences clears distilled_at.)
    if profile.distilled_at is None:
        profile.summary = _templated_summary(profile)
    await session.commit()
    await session.refresh(profile)
    logger.info("preferences_recomputed", user=str(user_id), signals=len(signals))
    return profile


async def _household_family_lean(session: AsyncSession, user_id: UUID) -> bool | None:
    """Seed the family lean from the durable profile when behaviour is silent:
    a household with children → True; otherwise leave it unknown (None)."""
    result = await session.execute(select(Profile).where(Profile.user_id == user_id))
    p = result.scalar_one_or_none()
    household = (p.household if p else None) or {}
    children = household.get("children")
    if isinstance(children, list) and children:
        return True
    if isinstance(children, int) and children > 0:
        return True
    return None


def _templated_summary(profile: PreferenceProfile) -> str:
    """A plain-language summary built from the weight maps — the no-AI stand-in for
    the Phase-2 Haiku distillation. Shown in the agent snapshot and the memory UI."""
    parts: list[str] = []
    likes = [k for k, v in sorted(profile.event_category_weights.items(), key=lambda x: -x[1]) if v > 0][:3]
    if likes:
        parts.append("Enjoys " + ", ".join(likes))
    areas = [k for k, v in sorted(profile.area_weights.items(), key=lambda x: -x[1]) if v > 0][:2]
    if areas:
        parts.append("favours " + ", ".join(areas))
    if profile.typical_budget_aed is not None:
        parts.append(f"typical spend ~AED {round(profile.typical_budget_aed)}")
    if profile.family_bias is True:
        parts.append("leans family-friendly")
    used = [k for k, v in sorted(profile.service_category_affinity.items(), key=lambda x: -x[1]) if v > 0][:3]
    if used:
        parts.append("has used " + ", ".join(used))
    return "; ".join(parts) if parts else ""


# ─── Explicit memory (notes) + user controls ────────────────────────────────────
# Notes are the editable bullets in the "What Resido knows" surface — durable tastes
# the structured signals can't capture (vegetarian, dislikes loud venues). They live
# in profile.notes and are independent of recompute (which only rewrites the derived
# weight maps + templated summary, never the notes). The JSONB list is always
# *reassigned* (not mutated in place) so SQLAlchemy flags the column dirty.


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def add_preference_note(
    session: AsyncSession,
    user_id: UUID,
    *,
    text: str,
    source: str = "explicit",
    confidence: float | None = None,
) -> PreferenceProfile:
    """Append a durable preference note (deduped on case-insensitive text). ``source``
    is 'chat' (agent saved it), 'explicit' (user typed it), or 'inferred'."""
    text = (text or "").strip()
    profile = await get_or_create_preference_profile(session, user_id)
    if not text:
        return profile
    existing = profile.notes or []
    if any((n.get("text") or "").strip().lower() == text.lower() for n in existing):
        return profile  # already remembered — don't duplicate
    note = {
        "id": uuid4().hex,
        "text": text,
        "source": source,
        "confidence": confidence,
        "pinned": False,
        "created_at": _now_iso(),
    }
    profile.notes = [*existing, note]
    await session.commit()
    await session.refresh(profile)
    return profile


async def update_preference_note(
    session: AsyncSession, user_id: UUID, note_id: str, fields: dict
) -> PreferenceProfile | None:
    """Edit a note's ``text`` and/or ``pinned``. Returns None if the note is absent."""
    profile = await get_or_create_preference_profile(session, user_id)
    notes = profile.notes or []
    updated, found = [], False
    for n in notes:
        if n.get("id") == note_id:
            found = True
            n = {**n}
            if fields.get("text") is not None:
                n["text"] = str(fields["text"]).strip()
            if fields.get("pinned") is not None:
                n["pinned"] = bool(fields["pinned"])
        updated.append(n)
    if not found:
        return None
    profile.notes = updated
    await session.commit()
    await session.refresh(profile)
    return profile


async def delete_preference_note(
    session: AsyncSession, user_id: UUID, note_id: str
) -> PreferenceProfile | None:
    """Remove a note. Returns None if it wasn't there (so the API can 404)."""
    profile = await get_or_create_preference_profile(session, user_id)
    notes = profile.notes or []
    remaining = [n for n in notes if n.get("id") != note_id]
    if len(remaining) == len(notes):
        return None
    profile.notes = remaining
    await session.commit()
    await session.refresh(profile)
    return profile


async def set_personalization_paused(
    session: AsyncSession, user_id: UUID, paused: bool
) -> PreferenceProfile:
    """The user's kill switch — when paused the ranker ignores the profile entirely
    (alpha collapses to 0), without discarding the learned taste."""
    profile = await get_or_create_preference_profile(session, user_id)
    profile.personalization_paused = bool(paused)
    await session.commit()
    await session.refresh(profile)
    return profile


async def reset_preferences(session: AsyncSession, user_id: UUID) -> PreferenceProfile:
    """Forget everything: drop all signals and blank the profile back to a clean,
    active slate. The 'delete my memory' action behind the UI."""
    await session.execute(
        delete(PreferenceSignal).where(PreferenceSignal.user_id == user_id)
    )
    profile = await get_or_create_preference_profile(session, user_id)
    profile.area_weights = {}
    profile.event_category_weights = {}
    profile.service_category_affinity = {}
    profile.tag_weights = {}
    profile.weekday_weights = {}
    profile.typical_budget_aed = None
    profile.family_bias = None
    profile.summary = None
    profile.notes = []
    profile.signal_count = 0
    profile.personalization_paused = False
    profile.distilled_at = None
    await session.commit()
    await session.refresh(profile)
    logger.info("preferences_reset", user=str(user_id))
    return profile


def derived_chips(profile: PreferenceProfile) -> dict[str, list[str]]:
    """The positive, human-facing chips for the 'What Resido knows' surface — the
    top liked areas / event categories / trades used (negatives stay hidden)."""

    def top(weights: dict[str, float], n: int = 4) -> list[str]:
        return [k for k, v in sorted(weights.items(), key=lambda x: -x[1]) if v > 0][:n]

    return {
        "areas": top(profile.area_weights),
        "event_categories": top(profile.event_category_weights),
        "services_used": top(profile.service_category_affinity),
    }


def memory_for_prompt(profile: PreferenceProfile | None) -> str:
    """The taste line the agent sees: the derived summary plus any explicit notes.
    Empty when there's nothing to say or the user paused personalisation."""
    if profile is None or profile.personalization_paused:
        return ""
    parts: list[str] = []
    if profile.summary:
        parts.append(profile.summary)
    note_texts = [n.get("text") for n in (profile.notes or []) if n.get("text")]
    if note_texts:
        parts.append("also remembers: " + "; ".join(note_texts[:8]))
    return " — ".join(parts)


# ─── Haiku distillation (Phase 2 AI layer; see preferences_distill.py) ───────────
# The distiller calls this to write its output. It OWNS the summary and the
# `inferred` notes; user/agent notes (`explicit`/`chat`) and any pinned note survive
# untouched — pinning an inferred note is how a user makes it permanent.


async def apply_distillation(
    session: AsyncSession,
    user_id: UUID,
    *,
    summary: str | None,
    inferred_notes: list[dict],
) -> PreferenceProfile:
    profile = await get_or_create_preference_profile(session, user_id)
    kept = [
        n for n in (profile.notes or [])
        if n.get("source") in ("explicit", "chat") or n.get("pinned")
    ]
    seen = {(n.get("text") or "").strip().lower() for n in kept}
    fresh: list[dict] = []
    for note in inferred_notes:
        text = (note.get("text") or "").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        conf = note.get("confidence")
        fresh.append({
            "id": uuid4().hex,
            "text": text,
            "source": "inferred",
            "confidence": float(conf) if isinstance(conf, (int, float)) else None,
            "pinned": False,
            "created_at": _now_iso(),
        })
    profile.notes = [*kept, *fresh]
    if summary and summary.strip():
        profile.summary = summary.strip()
    profile.distilled_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(profile)
    logger.info("preferences_distilled", user=str(user_id), inferred_notes=len(fresh))
    return profile


async def select_distillation_candidates(
    session: AsyncSession, *, min_signals: int, stale_before: datetime, limit: int
) -> list[UUID]:
    """User ids worth (re)distilling: enough signal, not paused, and either never
    distilled or gone stale. Oldest-distilled first so a capped run fairly rotates
    through everyone."""
    stmt = (
        select(PreferenceProfile.user_id)
        .where(PreferenceProfile.signal_count >= min_signals)
        .where(PreferenceProfile.personalization_paused.is_(False))
        .where(
            or_(
                PreferenceProfile.distilled_at.is_(None),
                PreferenceProfile.distilled_at < stale_before,
            )
        )
        .order_by(PreferenceProfile.distilled_at.asc().nullsfirst())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


# ─── Ranking ───────────────────────────────────────────────────────────────────


def personalization_alpha(profile: PreferenceProfile | None) -> float:
    """How strongly to apply taste, in [0, 1]. 0 when there's no profile, the user
    paused it, or there are no signals — so the base ranking is returned untouched."""
    if profile is None or profile.personalization_paused or profile.signal_count <= 0:
        return 0.0
    n = profile.signal_count
    return n / (n + ALPHA_K)


def _area_weight(area_weights: dict[str, float], area: str | None) -> float:
    """Best matching area weight — areas are stored as neighbourhood substrings, so
    we match either direction (a saved 'Dubai Marina' should hit a 'Marina' row)."""
    if not area or not area_weights:
        return 0.0
    lo = area.lower()
    best = 0.0
    for key, val in area_weights.items():
        kl = key.lower()
        if kl in lo or lo in kl:
            best = val if abs(val) > abs(best) else best
    return best


def _budget_fit(typical: float | None, price_min: float | None) -> float:
    """+ when the item sits at/under the user's typical spend, − when it's well over;
    0 when either side is unknown (never penalise an unpriced item)."""
    if typical is None or price_min is None:
        return 0.0
    if typical <= 0:
        return 0.0
    if price_min <= typical:
        return 0.5  # comfortably affordable — a mild plus, not the main driver
    over = (price_min - typical) / typical
    return max(-1.0, -over)


def score_event(profile: PreferenceProfile, event: Event) -> float:
    """Taste match for one event in ~[-1, 1]."""
    cat = profile.event_category_weights.get(event.category, 0.0)
    area = _area_weight(profile.area_weights, event.area)
    fam = 0.0
    if profile.family_bias is True:
        fam = 1.0 if event.family_friendly is True else (-0.5 if event.family_friendly is False else 0.0)
    wd = _event_weekday(event)
    wday = profile.weekday_weights.get(str(wd), 0.0) if wd is not None else 0.0
    budget = _budget_fit(
        profile.typical_budget_aed,
        float(event.price_min) if event.price_min is not None else None,
    )
    match = (
        _EV_W["category"] * cat
        + _EV_W["area"] * area
        + _EV_W["family"] * fam
        + _EV_W["weekday"] * wday
        + _EV_W["budget"] * budget
    )
    return max(-1.0, min(1.0, match))


def score_provider(profile: PreferenceProfile, provider: ServiceProvider) -> float:
    """Taste match for one provider in ~[-1, 1]."""
    cat = profile.service_category_affinity.get(provider.category, 0.0)
    area = _area_weight(profile.area_weights, provider.area)
    budget = _budget_fit(profile.typical_budget_aed, parse_aed(provider.price_from))
    match = _SV_W["area"] * area + _SV_W["category"] * cat + _SV_W["budget"] * budget
    return max(-1.0, min(1.0, match))


def _event_time_score(event: Event, now: datetime) -> float:
    """Recency/imminence in (0, 1]: ~1 for today, ~0.5 a week out, small & flat for
    undated 'ongoing' rows — so taste can reorder near-term events but won't drag a
    far-future one to the top."""
    if event.starts_at is None:
        return 0.15
    starts = event.starts_at
    if starts.tzinfo is None:
        starts = starts.replace(tzinfo=timezone.utc)
    days = max((starts - now).total_seconds() / 86400.0, 0.0)
    return 1.0 / (1.0 + days / 7.0)


def rank_events(
    profile: PreferenceProfile | None, events: list[Event], *, limit: int
) -> list[Event]:
    """Blend taste with imminence: ``time_score · (1 + α · match)``. With α=0 the
    blend collapses to time order, so the result equals the un-personalised feed."""
    alpha = personalization_alpha(profile)
    if alpha <= 0 or not events:
        return events[:limit]
    now = datetime.now(timezone.utc)
    scored = [
        (_event_time_score(e, now) * (1 + alpha * score_event(profile, e)), i, e)
        for i, e in enumerate(events)
    ]
    scored.sort(key=lambda t: (-t[0], t[1]))  # i breaks ties → stable
    return [e for _, _, e in scored][:limit]


def rank_providers(
    profile: PreferenceProfile | None, providers: list[ServiceProvider], *, limit: int
) -> list[ServiceProvider]:
    """Soft-boost the trust ranking by taste: ``score · (1 + α · match · BOOST)``.
    BOOST caps taste's influence so trust still dominates (a low-trust but
    well-matched provider can't leapfrog a clearly-trusted one)."""
    alpha = personalization_alpha(profile)
    if alpha <= 0 or not providers:
        return providers[:limit]
    scored = [
        (p.score * (1 + alpha * score_provider(profile, p) * _PROVIDER_BOOST), i, p)
        for i, p in enumerate(providers)
    ]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [p for _, _, p in scored][:limit]
