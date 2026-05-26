"""Source a distinct, renderable image per event via an Exa search.

The lifestyle feed extracts many events from a single listicle, so inheriting
the article's `og:image` gives every event the same (often mismatched) picture —
e.g. a coffee spot showing the article's fashion-editorial hero. A quick Exa
search keyed on the event's own title + venue instead returns pages about *that*
event/venue, whose images are distinct and on-topic.

Exa's top "image" is sometimes a logo SVG, a favicon, or a .avif file Flutter
can't render, so we hand R2 an ordered list of candidates (Exa results, then the
article og:image as a last resort) and let `r2_storage.mirror_best` keep the
first that mirrors as a real jpeg/png/webp.
"""

import asyncio

import httpx

from app.config import settings
from app.core.logging import get_logger
from app.ingestion import exa_client, r2_storage
from app.ingestion.events import _image_key

logger = get_logger(__name__)

_CONCURRENCY = 5   # be gentle with the Exa rate limit
_NUM_RESULTS = 5   # more candidates → better odds one mirrors as a renderable photo


def _query(title: str, venue: str | None, area: str | None) -> str:
    return " ".join(p for p in (title, venue, area, "Dubai") if p)


async def find_candidates(title: str | None, venue: str | None, area: str | None) -> list[str]:
    """Ordered image URLs from an Exa search for this event (may be empty)."""
    if not title:
        return []
    try:
        results = await exa_client.search(_query(title, venue, area), num_results=_NUM_RESULTS)
    except Exception as exc:  # image is a nice-to-have; never sink ingest
        logger.warning("event_image_search_failed", title=title, error=str(exc))
        return []
    return [r["image"] for r in results if r.get("image")]


async def resolve_images(rows: list[dict], *, client: httpx.AsyncClient | None = None) -> None:
    """For each event row, find a distinct image and mirror it to R2, in place.

    Candidates are the per-event Exa results followed by whatever ``image_url`` the
    row already held (the article og:image) as a last resort. ``image_url`` is set
    to the R2 URL of the first candidate that mirrors as a renderable photo, or
    None (→ gradient) if none do. No-op when R2 isn't configured."""
    if not rows or not settings.r2_enabled:
        return

    sem = asyncio.Semaphore(_CONCURRENCY)
    owns_client = client is None
    client = client or httpx.AsyncClient()
    try:
        async def _one(row: dict) -> None:
            async with sem:
                candidates = await find_candidates(row.get("title"), row.get("venue"), row.get("area"))
                if row.get("image_url"):
                    candidates.append(row["image_url"])  # article image as fallback
                row["image_url"] = await r2_storage.mirror_best(
                    candidates, key=_image_key(row), client=client
                )

        await asyncio.gather(*(_one(r) for r in rows))
    finally:
        if owns_client:
            await client.aclose()
