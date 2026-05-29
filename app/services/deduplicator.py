"""Daily duplicate cleanup for customer-visible content.

The ingestion paths already upsert on strong source IDs (`events.url` and
`service_providers.place_id`), but cross-source scrapes can still create near
duplicates with different URLs/place IDs. This module scores those fuzzy pairs
deterministically and removes only high-confidence matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from math import asin, cos, radians, sin, sqrt
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import re
import unicodedata

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.event import Event
from app.models.service import ServiceProvider

logger = get_logger(__name__)


@dataclass(slots=True)
class DuplicateDecision:
    item_type: str
    duplicate_id: str
    kept_id: str
    confidence: float
    reason: str


@dataclass(slots=True)
class DeduplicationSummary:
    threshold: float
    dry_run: bool
    events_checked: int = 0
    providers_checked: int = 0
    event_duplicates: int = 0
    provider_duplicates: int = 0
    decisions: list[DuplicateDecision] | None = None


_SPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^a-z0-9\s+]")
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "ref",
    "source",
}
_BUSINESS_SUFFIXES = {
    "ae",
    "app",
    "co",
    "com",
    "dubai",
    "llc",
    "ltd",
    "services",
    "service",
    "uae",
}


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    value = value.lower().replace("&", " and ")
    value = _NON_WORD.sub(" ", value)
    return _SPACE.sub(" ", value).strip()


def _tokens(value: str | None, *, drop_business_suffixes: bool = False) -> set[str]:
    words = set(_clean_text(value).split())
    if drop_business_suffixes:
        words -= _BUSINESS_SUFFIXES
    return {w for w in words if len(w) > 1}


def _ratio(left: str | None, right: str | None) -> float:
    left_clean = _clean_text(left)
    right_clean = _clean_text(right)
    if not left_clean or not right_clean:
        return 0.0
    if left_clean == right_clean:
        return 1.0
    return SequenceMatcher(None, left_clean, right_clean).ratio()


def _token_set_ratio(left: str | None, right: str | None, *, business: bool = False) -> float:
    left_tokens = _tokens(left, drop_business_suffixes=business)
    right_tokens = _tokens(right, drop_business_suffixes=business)
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = left_tokens & right_tokens
    if not intersection:
        return 0.0
    precision = len(intersection) / min(len(left_tokens), len(right_tokens))
    recall = len(intersection) / max(len(left_tokens), len(right_tokens))
    return (0.65 * precision) + (0.35 * recall)


def _text_score(left: str | None, right: str | None, *, business: bool = False) -> float:
    return max(_ratio(left, right), _token_set_ratio(left, right, business=business))


def _normalize_url(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(value.strip())
    host = parsed.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+", "/", parsed.path).rstrip("/").lower()
    query = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=False)
        if key not in _TRACKING_KEYS and not key.startswith(_TRACKING_PREFIXES)
    ]
    return urlunparse(("", host, path, "", urlencode(sorted(query)), ""))


def _url_score(left: str | None, right: str | None) -> float:
    left_norm = _normalize_url(left)
    right_norm = _normalize_url(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    left_parsed = urlparse("//" + left_norm)
    right_parsed = urlparse("//" + right_norm)
    if left_parsed.netloc != right_parsed.netloc:
        return 0.0
    path_similarity = SequenceMatcher(None, left_parsed.path, right_parsed.path).ratio()
    return 0.45 + (0.45 * path_similarity)


def _phone(value: str | None) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        digits = "971" + digits[1:]
    return digits


def _phone_score(left: str | None, right: str | None) -> float:
    left_phone = _phone(left)
    right_phone = _phone(right)
    if not left_phone or not right_phone:
        return 0.0
    if left_phone == right_phone:
        return 1.0
    # Tolerate one source including an extension or country-code variant.
    return 0.82 if left_phone[-7:] == right_phone[-7:] else 0.0


def _hours_between(left: datetime | None, right: datetime | None) -> float | None:
    if not left or not right:
        return None
    return abs((left - right).total_seconds()) / 3600


def _event_time_score(left: Event, right: Event) -> float:
    hours = _hours_between(left.starts_at, right.starts_at)
    if hours is None:
        return 0.55 if not left.starts_at and not right.starts_at else 0.25
    if hours <= 2:
        return 1.0
    if hours <= 24:
        return 0.9
    if hours <= 48:
        return 0.65
    return 0.0


def _geo_km(left: ServiceProvider, right: ServiceProvider) -> float | None:
    if None in (left.lat, left.lng, right.lat, right.lng):
        return None
    lat1, lng1, lat2, lng2 = map(radians, (left.lat, left.lng, right.lat, right.lng))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 6371.0 * (2 * asin(sqrt(a)))


def _geo_score(left: ServiceProvider, right: ServiceProvider) -> float:
    distance = _geo_km(left, right)
    if distance is None:
        return 0.35
    if distance <= 0.1:
        return 1.0
    if distance <= 0.5:
        return 0.9
    if distance <= 2:
        return 0.65
    if distance <= 5:
        return 0.35
    return 0.0


def _cap(value: float, maximum: float) -> float:
    return min(value, maximum)


def event_confidence(left: Event, right: Event) -> tuple[float, str]:
    title = _text_score(left.title, right.title)
    venue = _text_score(left.venue, right.venue)
    area = _text_score(left.area, right.area)
    url = _url_score(left.url, right.url)
    time = _event_time_score(left, right)
    category = 1.0 if _clean_text(left.category) == _clean_text(right.category) else 0.0

    confidence = (
        (0.42 * title)
        + (0.16 * venue)
        + (0.18 * time)
        + (0.12 * url)
        + (0.06 * area)
        + (0.06 * category)
    )
    same_normalized_url = _normalize_url(left.url) == _normalize_url(right.url)
    same_event_on_same_url = title >= 0.75 or (title >= 0.55 and time >= 0.9 and venue >= 0.5)
    if same_normalized_url and same_event_on_same_url:
        confidence = max(confidence, 0.98)
    if title < 0.78 and url < 0.9:
        confidence = _cap(confidence, 0.86)
    if category == 0.0:
        confidence = _cap(confidence, 0.88)
    if time == 0.0 and url < 0.9:
        confidence = _cap(confidence, 0.84)

    reason = (
        f"title={title:.2f} venue={venue:.2f} time={time:.2f} "
        f"url={url:.2f} area={area:.2f} category={category:.2f}"
    )
    return round(confidence, 4), reason


def provider_confidence(left: ServiceProvider, right: ServiceProvider) -> tuple[float, str]:
    name = _text_score(left.name, right.name, business=True)
    phone = max(_phone_score(left.phone, right.phone), _phone_score(left.whatsapp, right.whatsapp))
    website = _url_score(left.website, right.website)
    address = _text_score(left.address, right.address)
    geo = _geo_score(left, right)
    area = _text_score(left.area, right.area)
    category = 1.0 if _clean_text(left.category) == _clean_text(right.category) else 0.0

    confidence = (
        (0.34 * name)
        + (0.18 * phone)
        + (0.14 * website)
        + (0.16 * geo)
        + (0.10 * address)
        + (0.05 * category)
        + (0.03 * area)
    )
    if phone == 1.0 and name >= 0.78:
        confidence = max(confidence, 0.94)
    if website >= 0.92 and name >= 0.82:
        confidence = max(confidence, 0.92)
    if name < 0.78 and phone < 1.0 and website < 0.92:
        confidence = _cap(confidence, 0.86)
    distance = _geo_km(left, right)
    if distance is not None and distance > 5 and phone < 1.0 and website < 0.92:
        confidence = _cap(confidence, 0.85)
    if category == 0.0:
        confidence = _cap(confidence, 0.88)

    reason = (
        f"name={name:.2f} phone={phone:.2f} website={website:.2f} "
        f"geo={geo:.2f} address={address:.2f} area={area:.2f} category={category:.2f}"
    )
    return round(confidence, 4), reason


def _event_quality(row: Event) -> tuple:
    return (
        bool(row.is_published),
        row.expires_at is None or row.expires_at >= datetime.now(timezone.utc),
        row.starts_at is not None,
        row.image_url is not None,
        row.price_min is not None,
        len(row.description or ""),
        row.fetched_at or row.created_at,
    )


def _provider_quality(row: ServiceProvider) -> tuple:
    return (
        bool(row.is_active),
        row.score or 0.0,
        row.reviews_count or 0,
        row.rating or 0.0,
        row.phone is not None or row.whatsapp is not None,
        row.website is not None,
        row.photo_url is not None,
        row.price_from is not None,
        row.fetched_at or row.created_at,
    )


def _pair_key(left_id: str, right_id: str) -> tuple[str, str]:
    return tuple(sorted((left_id, right_id)))


def _event_candidates(events: list[Event]):
    seen: set[tuple[str, str]] = set()
    for i, left in enumerate(events):
        for right in events[i + 1 :]:
            if left.id == right.id:
                continue
            title_hint = _token_set_ratio(left.title, right.title)
            if title_hint < 0.45 and _url_score(left.url, right.url) < 0.45:
                continue
            hours = _hours_between(left.starts_at, right.starts_at)
            if left.starts_at and right.starts_at and hours is not None and hours > 72:
                continue
            key = _pair_key(str(left.id), str(right.id))
            if key not in seen:
                seen.add(key)
                yield left, right


def _provider_candidates(providers: list[ServiceProvider]):
    seen: set[tuple[str, str]] = set()
    by_category: dict[str, list[ServiceProvider]] = {}
    for provider in providers:
        by_category.setdefault(provider.category, []).append(provider)
    for category_providers in by_category.values():
        for i, left in enumerate(category_providers):
            for right in category_providers[i + 1 :]:
                if left.id == right.id:
                    continue
                name_hint = _token_set_ratio(left.name, right.name, business=True)
                contact_hint = max(
                    _phone_score(left.phone, right.phone),
                    _url_score(left.website, right.website),
                )
                if name_hint < 0.45 and contact_hint < 0.8:
                    continue
                distance = _geo_km(left, right)
                if distance is not None and distance > 15 and contact_hint < 0.9:
                    continue
                key = _pair_key(str(left.id), str(right.id))
                if key not in seen:
                    seen.add(key)
                    yield left, right


async def deduplicate_events(
    session: AsyncSession, *, threshold: float, dry_run: bool
) -> tuple[int, int, list[DuplicateDecision]]:
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(Event)
        .where(Event.is_published.is_(True))
        .where(or_(Event.expires_at.is_(None), Event.expires_at >= now))
        .order_by(Event.category, Event.starts_at.nullslast(), Event.title)
    )
    events = list(result.scalars().all())
    removed: set[str] = set()
    decisions: list[DuplicateDecision] = []

    for left, right in _event_candidates(events):
        if str(left.id) in removed or str(right.id) in removed:
            continue
        confidence, reason = event_confidence(left, right)
        if confidence < threshold:
            continue
        keep, duplicate = (left, right)
        if _event_quality(right) > _event_quality(left):
            keep, duplicate = right, left
        removed.add(str(duplicate.id))
        decisions.append(
            DuplicateDecision("event", str(duplicate.id), str(keep.id), confidence, reason)
        )
        logger.info(
            "duplicate_event",
            duplicate_id=str(duplicate.id),
            kept_id=str(keep.id),
            confidence=confidence,
            reason=reason,
            dry_run=dry_run,
        )
        if not dry_run:
            await session.delete(duplicate)

    if not dry_run and decisions:
        await session.commit()
    return len(events), len(decisions), decisions


async def deduplicate_providers(
    session: AsyncSession, *, threshold: float, dry_run: bool
) -> tuple[int, int, list[DuplicateDecision]]:
    result = await session.execute(
        select(ServiceProvider)
        .where(ServiceProvider.is_active.is_(True))
        .order_by(ServiceProvider.category, ServiceProvider.area, ServiceProvider.name)
    )
    providers = list(result.scalars().all())
    hidden: set[str] = set()
    decisions: list[DuplicateDecision] = []

    for left, right in _provider_candidates(providers):
        if str(left.id) in hidden or str(right.id) in hidden:
            continue
        confidence, reason = provider_confidence(left, right)
        if confidence < threshold:
            continue
        keep, duplicate = (left, right)
        if _provider_quality(right) > _provider_quality(left):
            keep, duplicate = right, left
        hidden.add(str(duplicate.id))
        decisions.append(
            DuplicateDecision("provider", str(duplicate.id), str(keep.id), confidence, reason)
        )
        logger.info(
            "duplicate_provider",
            duplicate_id=str(duplicate.id),
            kept_id=str(keep.id),
            confidence=confidence,
            reason=reason,
            dry_run=dry_run,
        )
        if not dry_run:
            duplicate.is_active = False

    if not dry_run and decisions:
        await session.commit()
    return len(providers), len(decisions), decisions


async def run_deduplicator(
    session: AsyncSession,
    *,
    threshold: float = 0.9,
    dry_run: bool = False,
    scope: str = "all",
) -> DeduplicationSummary:
    """Remove visible duplicates whose deterministic confidence is above threshold.

    Events are deleted because they are a self-expiring feed with no content FK.
    Providers are soft-hidden (`is_active = False`) because provider rows are
    durable and may be useful for lead attribution or future scrape revival.
    """
    if not 0.0 < threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    if scope not in {"all", "events", "providers"}:
        raise ValueError("scope must be one of: all, events, providers")

    summary = DeduplicationSummary(threshold=threshold, dry_run=dry_run, decisions=[])

    if scope in {"all", "events"}:
        checked, duplicates, decisions = await deduplicate_events(
            session, threshold=threshold, dry_run=dry_run
        )
        summary.events_checked = checked
        summary.event_duplicates = duplicates
        summary.decisions.extend(decisions)

    if scope in {"all", "providers"}:
        checked, duplicates, decisions = await deduplicate_providers(
            session, threshold=threshold, dry_run=dry_run
        )
        summary.providers_checked = checked
        summary.provider_duplicates = duplicates
        summary.decisions.extend(decisions)

    logger.info(
        "deduplicator_finished",
        threshold=threshold,
        dry_run=dry_run,
        scope=scope,
        events_checked=summary.events_checked,
        event_duplicates=summary.event_duplicates,
        providers_checked=summary.providers_checked,
        provider_duplicates=summary.provider_duplicates,
    )
    return summary
