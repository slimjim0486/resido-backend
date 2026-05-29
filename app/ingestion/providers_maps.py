"""Services ingestion: run the Apify Google Maps actor per (category × area) cell,
normalize defensively, score, and upsert. See backend/INGESTION.md (Services).

Mirrors the events Apify pattern (`events.py`): the actor is configurable
(``APIFY_MAPS_ACTOR``, default the off-the-shelf ``compass/crawler-google-places``)
and the normalizer is defensive about field names so we can swap actors without
touching the pipeline. Each cell runs in its own try/except so one bad query can't
sink the grid run.
"""

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.ingestion import apify_client, providers_pricing, r2_storage
from app.ingestion.services_registry import Category, grid, search_term
from app.services import providers as providers_service

logger = get_logger(__name__)


def _photo_key(row: dict) -> str:
    """Stable R2 key for a provider photo, keyed on the durable Google place_id so a
    monthly re-scrape overwrites the same object (bounded object count, fresh photo)."""
    digest = hashlib.sha1(row["place_id"].encode()).hexdigest()
    return f"providers/{digest}"

# Google Maps detail-page scrapes are slow; give a cell run plenty of headroom.
_CELL_TIMEOUT = 300.0


def _first(item: dict, *keys: str):
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _latlng(item: dict) -> tuple[float | None, float | None]:
    loc = item.get("location")
    if isinstance(loc, dict):
        return _to_float(loc.get("lat")), _to_float(loc.get("lng"))
    return _to_float(item.get("lat")), _to_float(item.get("lng"))


def _whatsapp(phone_unformatted) -> str | None:
    """Derive a wa.me-ready number from a UAE *mobile* only (landlines can't do
    WhatsApp, so we'd rather show no button than a dead one)."""
    if not phone_unformatted:
        return None
    digits = "".join(ch for ch in str(phone_unformatted) if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:  # local "05XXXXXXXX"
        digits = "971" + digits[1:]
    return digits if digits.startswith("9715") else None


def _photo(item: dict) -> str | None:
    url = _first(item, "imageUrl", "imageUrls", "images", "photo", "thumbnail")
    if isinstance(url, list):
        url = url[0] if url else None
    if isinstance(url, dict):  # some actors wrap as {"imageUrl": ...}
        url = url.get("imageUrl") or url.get("url")
    return str(url)[:1024] if url else None


def _hours(item: dict):
    """Opening hours as scraped (list of {day, hours}); stored verbatim as JSONB."""
    h = _first(item, "openingHours", "hours", "openingHoursBusiness")
    return h if isinstance(h, (list, dict)) else None


# Google's `additionalInfo` groups attributes by category → [{label: bool}, …].
# We keep only the *true* labels, from the categories worth surfacing as feature
# chips (service-relevant first), and skip the noisy ones (parking/payments).
_HIGHLIGHT_CATEGORIES = (
    "Service options",
    "Offerings",
    "Highlights",
    "From the business",
    "Planning",
    "Amenities",
    "Accessibility",
)
_MAX_HIGHLIGHTS = 6


def _highlights(item: dict) -> list[str] | None:
    info = item.get("additionalInfo")
    if not isinstance(info, dict):
        return None
    out: list[str] = []
    for category in _HIGHLIGHT_CATEGORIES:
        for entry in info.get(category) or []:
            if isinstance(entry, dict):
                for label, enabled in entry.items():
                    if enabled is True and label not in out:
                        out.append(str(label)[:80])
    return out[:_MAX_HIGHLIGHTS] or None


_REVIEW_LIST_KEYS = (
    "reviews",
    "reviewsData",
    "userReviews",
    "placeReviews",
    "latestReviews",
)
_REVIEW_TEXT_KEYS = (
    "text",
    "reviewText",
    "textTranslated",
    "comment",
    "snippet",
    "summary",
)
_REVIEW_RATING_KEYS = ("stars", "rating", "score", "reviewRating")
_MAX_REVIEW_SAMPLE = 20

_POSITIVE_THEMES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Reliable and punctual", ("on time", "punctual", "same day", "quick", "fast", "prompt")),
    ("Professional technicians", ("professional", "technician", "team", "staff", "skilled")),
    ("Good communication", ("responsive", "response", "communicat", "whatsapp", "called", "updated")),
    ("Strong quality of work", ("quality", "excellent", "thorough", "clean", "fixed", "repair")),
    ("Fair value", ("price", "value", "reasonable", "affordable", "transparent", "quote")),
    ("Friendly service", ("friendly", "polite", "helpful", "courteous")),
    ("Gentle animal handling", ("gentle", "caring", "compassion", "kind", "calm", "handled")),
    ("Clean pet facilities", ("clean facility", "hygien", "clean cages", "well kept")),
    ("Helpful pet updates", ("updates", "photos", "videos", "kept us informed")),
    ("Helpful clinical communication", ("explained", "doctor explained", "nurse", "reception", "appointment")),
    ("Easy appointment access", ("appointment", "walk in", "same day", "available", "open 24")),
    ("Clean medical facility", ("clean clinic", "clean facility", "hygien", "sterile")),
)

_WATCHOUT_THEMES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Some timing complaints", ("late", "delay", "delayed", "waiting", "no show", "reschedule")),
    ("Price concerns in a few reviews", ("expensive", "overpriced", "costly", "hidden", "charged")),
    ("Mixed communication reports", ("no response", "unresponsive", "ignored", "rude")),
    ("Quality issues mentioned", ("poor", "bad", "damaged", "issue", "problem", "not fixed")),
    ("Animal-care concerns mentioned", ("rough", "neglect", "unclean", "dirty kennel", "stress")),
    ("Upselling concerns in a few reviews", ("upsell", "unnecessary test", "extra charge")),
    ("Appointment wait concerns", ("long wait", "waiting time", "delayed appointment", "queue")),
    ("Billing concerns in a few reviews", ("insurance", "billing", "claim", "charged", "expensive")),
)


def _reviews(item: dict) -> list[dict]:
    raw = _first(item, *_REVIEW_LIST_KEYS)
    if isinstance(raw, dict):
        raw = raw.get("items") or raw.get("reviews") or raw.get("data")
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)][:_MAX_REVIEW_SAMPLE]


def _review_text(review: dict) -> str:
    value = _first(review, *_REVIEW_TEXT_KEYS)
    return str(value).strip() if value else ""


def _review_rating(review: dict) -> float | None:
    return _to_float(_first(review, *_REVIEW_RATING_KEYS))


def _theme_hits(texts: list[str], themes: tuple[tuple[str, tuple[str, ...]], ...]) -> list[str]:
    haystack = "\n".join(texts).lower()
    hits = [
        label
        for label, needles in themes
        if any(needle in haystack for needle in needles)
    ]
    return hits[:4]


def _rating_basis(rating: float | None, reviews_count: int) -> str:
    if rating is None or reviews_count <= 0:
        return "No public rating signal yet"
    if reviews_count >= 250:
        volume = "high"
    elif reviews_count >= 50:
        volume = "solid"
    else:
        volume = "limited"
    return f"{rating:.1f} from {reviews_count} Google reviews ({volume} volume)"


def _review_curation(
    item: dict,
    *,
    rating: float | None,
    reviews_count: int,
    highlights: list[str] | None,
    subcategory: str = "general",
) -> dict | None:
    """Compact review digest for recommendation UX.

    We never persist verbatim review text; this stores only themes and a plain
    confidence note. If the actor doesn't return review bodies, the curation still
    explains the rating/review-count signal without pretending we read reviews.
    """
    reviews = _reviews(item)
    texts = [text for r in reviews if (text := _review_text(r))]
    sampled_ratings = [r for review in reviews if (r := _review_rating(review)) is not None]
    positives = _theme_hits(texts, _POSITIVE_THEMES)
    watchouts = _theme_hits(texts, _WATCHOUT_THEMES)
    if subcategory == "shelters_adoption" and not rating and reviews_count <= 0:
        return {
            "summary": (
                "Shelters and adoption groups are included for civic value; compare contact details, "
                "adoption process, and current availability rather than star ratings."
            ),
            "positives": ["Adoption and rescue resource"],
            "watchouts": [],
            "sample_size": 0,
            "sample_average_rating": None,
            "rating_basis": "Not ranked by rating",
        }
    if not positives and highlights:
        positives = [f"Offers {h.lower()}" for h in highlights[:3]]

    basis = _rating_basis(rating, reviews_count)
    if texts:
        if positives:
            summary = f"Reviews most often point to {', '.join(p.lower() for p in positives[:2])}."
        elif rating and rating >= 4.5:
            summary = "Recent review text is broadly positive, but no single theme dominates."
        else:
            summary = "Review text is available, but themes are mixed."
    elif rating is not None and reviews_count > 0:
        summary = (
            "No review text was available in the scrape; use the public rating "
            "and review volume as the trust signal."
        )
    else:
        return None

    return {
        "summary": summary,
        "positives": positives,
        "watchouts": watchouts,
        "sample_size": len(texts),
        "sample_average_rating": (
            round(sum(sampled_ratings) / len(sampled_ratings), 1)
            if sampled_ratings else None
        ),
        "rating_basis": basis,
    }


def normalize(
    item: dict,
    *,
    category: str,
    subcategory: str,
    area: str,
    source: str,
    rank: int | None = None,
) -> dict | None:
    """Map a loosely-shaped Maps actor item onto our ServiceProvider fields. Returns
    None when the item lacks the minimum (name + place_id) to be useful."""
    name = _first(item, "title", "name")
    place_id = _first(item, "placeId", "place_id", "cid", "fid")
    if not name or not place_id:
        return None

    lat, lng = _latlng(item)
    rating = _to_float(_first(item, "totalScore", "rating", "stars"))
    reviews_count = _to_int(_first(item, "reviewsCount", "reviews", "userRatingCount"))
    highlights = _highlights(item)
    return {
        "name": str(name)[:255],
        "category": category,
        "subcategory": subcategory,
        # Canonical grid area (so cell filters line up); fall back to the scraped
        # neighbourhood/city when we somehow lack it.
        "area": area or _first(item, "neighborhood", "neighbourhood", "city"),
        "address": _first(item, "address", "street"),
        "lat": lat,
        "lng": lng,
        "rating": rating,
        "reviews_count": reviews_count,
        "phone": _first(item, "phone", "phoneUnformatted"),
        "whatsapp": _whatsapp(_first(item, "phoneUnformatted", "phone")),
        "website": (str(_first(item, "website", "webUrl") or "")[:1024]) or None,
        "maps_url": (str(_first(item, "url", "mapsUrl", "googleMapsUrl") or "")[:1024]) or None,
        "place_id": str(place_id)[:255],
        "price_level": (str(_first(item, "price", "priceLevel") or "")[:16]) or None,
        "photo_url": _photo(item),
        "hours": _hours(item),
        "highlights": highlights,
        "review_curation": _review_curation(
            item,
            rating=rating,
            reviews_count=reviews_count,
            highlights=highlights,
            subcategory=subcategory,
        ),
        "google_rank": _to_int(item.get("rank")) or rank,
        "is_sponsored": bool(_first(item, "isAdvertisement", "isAd", "sponsored") or False),
        "source": source,
    }


async def _scrape_cell(category: Category, area: str, *, per_cell: int) -> list[dict]:
    """Run the actor for one cell and return raw dataset items."""
    actor = settings.APIFY_MAPS_ACTOR
    if not actor:
        raise RuntimeError("No Apify maps actor configured (set APIFY_MAPS_ACTOR)")
    scrape_details = settings.SERVICES_SCRAPE_DETAILS or settings.SERVICES_MAX_REVIEWS > 0
    run_input = {
        "searchStringsArray": [search_term(category, area)],
        "maxCrawledPlacesPerSearch": per_cell,
        "language": "en",
        "countryCode": "ae",
        "skipClosedPlaces": True,
        # Opening hours require opening each place's detail page (the costliest
        # toggle); gated so re-seeds can run fast without it.
        "scrapePlaceDetailPage": scrape_details,
    }
    if settings.SERVICES_MAX_REVIEWS > 0:
        run_input.update(
            {
                "maxReviews": settings.SERVICES_MAX_REVIEWS,
                "reviewsSort": "mostRelevant",
                "reviewsOrigin": "google",
                "scrapeReviewsPersonalData": False,
            }
        )
    return await apify_client.run_actor(actor, run_input, timeout=_CELL_TIMEOUT)


def normalize_cell(
    raw: list[dict], *, category: str, subcategory: str, area: str, source: str
) -> list[dict]:
    """Normalize + score a cell's raw items (no DB). Used by ingest and the dry run."""
    rows: list[dict] = []
    for i, item in enumerate(raw, start=1):
        row = normalize(
            item,
            category=category,
            subcategory=subcategory,
            area=area,
            source=source,
            rank=i,
        )
        if row:
            row["score"] = providers_service.bayesian_score(
                row.get("rating"), row.get("reviews_count")
            )
            rows.append(row)
    return rows


async def ingest_cell(
    session: AsyncSession, category: Category, area: str, *, per_cell: int
) -> int:
    """Scrape one (category, area) cell, normalize, and upsert. Returns inserted."""
    source = (settings.APIFY_MAPS_ACTOR or "google_maps").split("/")[-1]
    raw = await _scrape_cell(category, area, per_cell=per_cell)
    rows = normalize_cell(
        raw,
        category=category.row_category,
        subcategory=category.subcategory,
        area=area,
        source="google_maps",
    )
    # Re-host scraped Google photos in R2 (their source URLs are token-signed and
    # expire) before persisting. No-op when R2 isn't configured.
    await r2_storage.mirror_field(rows, src_field="photo_url", key_fn=_photo_key)
    # Extract real advertised pricing from each provider's website (Google Maps
    # gives none for these businesses). Mutates rows in place; no-op without a
    # Claude key or when SERVICES_EXTRACT_PRICING is off.
    priced = await providers_pricing.enrich_pricing(
        [
            r
            for r in rows
            if r.get("subcategory") != "shelters_adoption"
            and r.get("category") != "medical"
        ]
    )
    inserted = await providers_service.upsert_providers(session, rows)
    # Reconcile this cell: providers not seen for ~2 monthly cycles get soft-hidden.
    # On a first scrape this is a no-op (survivors were just refreshed).
    retired = await providers_service.retire_stale(
        session,
        category.row_category,
        area,
        grace_days=settings.SERVICES_STALE_GRACE_DAYS,
        subcategory=category.subcategory,
    )
    await providers_service.record_cell_refresh(
        session,
        category=category.row_category,
        subcategory=category.subcategory,
        area=area,
        fetched_count=len(raw),
        kept_count=len(rows),
        inserted_count=inserted,
        source=source,
    )
    logger.info(
        "providers_cell",
        category=category.row_category, subcategory=category.subcategory, area=area, source=source,
        fetched=len(raw), kept=len(rows), priced=priced, inserted=inserted, retired=retired,
    )
    return inserted


async def ingest_grid(
    session: AsyncSession,
    *,
    cells: list[tuple[Category, str]] | None = None,
    per_cell: int | None = None,
) -> int:
    """Scrape a set of grid cells (default: the whole registry), resilient per-cell.
    Returns the total newly-inserted providers."""
    cells = cells if cells is not None else grid()
    inserted = 0
    for category, area in cells:
        try:
            per = per_cell or category.refresh_per_cell or settings.SERVICES_PER_CELL
            inserted += await ingest_cell(session, category, area, per_cell=per)
        except Exception as exc:  # one bad cell must not sink the run
            await session.rollback()
            logger.warning(
                "providers_cell_failed",
                category=category.row_category,
                subcategory=category.subcategory,
                area=area,
                error=str(exc),
            )
    return inserted


async def dry_run_cell(category: Category, area: str, *, per_cell: int) -> list[dict]:
    """Read-only: scrape one cell and return normalized+scored rows. No DB write."""
    raw = await _scrape_cell(category, area, per_cell=per_cell)
    return normalize_cell(
        raw,
        category=category.row_category,
        subcategory=category.subcategory,
        area=area,
        source="google_maps",
    )


def due_cells(
    freshness: dict[tuple[str, str, str], datetime],
    *,
    ttl_days: int,
    now: datetime | None = None,
) -> list[tuple[Category, str]]:
    """Grid cells never scraped, or whose freshest row has aged past the TTL.

    Each registry category can override the default TTL. Returned cells are
    oldest-first so a capped cron rotates fairly instead of always refreshing the
    first registry categories.
    """
    now = now or datetime.now(timezone.utc)
    due: list[tuple[datetime, str, str, tuple[Category, str]]] = []
    never = datetime.min.replace(tzinfo=timezone.utc)
    for category, area in grid():
        last = freshness.get((category.row_category, category.subcategory, area))
        category_ttl = category.refresh_ttl_days or ttl_days
        threshold = now - timedelta(days=category_ttl)
        if last is None or last < threshold:
            due.append((last or never, category.key, area, (category, area)))
    due.sort(key=lambda item: (item[0], item[1], item[2]))
    return [cell for *_, cell in due]
