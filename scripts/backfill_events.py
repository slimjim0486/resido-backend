"""Backfill the event facets added in migration 0011 (price_min, family_friendly)
for rows ingested before it existed. Idempotent — safe to re-run; the next
seed/refresh sets these on write, so this is only for the existing feed.

Run from backend/:

    .venv/bin/python -m scripts.backfill_events
"""

import asyncio

from sqlalchemy import select

from app.core.pricing import parse_aed
from app.database import async_session_maker
from app.ingestion.events import infer_family_friendly
from app.models.event import Event


async def backfill() -> tuple[int, int]:
    """Returns (price_min set, family_friendly set)."""
    priced = familied = 0
    async with async_session_maker() as session:
        rows = (await session.execute(select(Event))).scalars().all()
        for e in rows:
            new_price = parse_aed(e.price_from)
            if new_price is not None and e.price_min is None:
                e.price_min = new_price
                priced += 1
            if e.family_friendly is None:
                verdict = infer_family_friendly(e.category, e.title, e.description)
                if verdict is not None:
                    e.family_friendly = verdict
                    familied += 1
        await session.commit()
    return priced, familied


async def main() -> None:
    priced, familied = await backfill()
    print(f"Backfilled price_min on {priced} events, family_friendly on {familied}.")


if __name__ == "__main__":
    asyncio.run(main())
