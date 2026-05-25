"""Seed / refresh the lifestyle 'what's on' feed (Tier B).

If an Apify events actor is configured (APIFY_TOKEN + APIFY_EVENTS_ACTOR), pulls
live listings. Otherwise inserts a small curated sample (clearly sourced as
'sample') so the Home hero lights up for the demo. Run from backend/:

    .venv/bin/python -m scripts.seed_events
"""

import asyncio
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.database import async_session_maker
from app.ingestion.events import ingest_apify_events
from app.services import events as events_service


def _sample_events() -> list[dict]:
    """A handful of representative Dubai events, dated relative to today so the
    feed always looks current in a demo."""
    now = datetime.now(timezone.utc)

    def at(days: int, hour: int = 19) -> datetime:
        return (now + timedelta(days=days)).replace(
            hour=hour, minute=0, second=0, microsecond=0
        )

    raw = [
        {
            "title": "Friday Beach Brunch at Kite Beach",
            "description": "Bottomless brunch with live DJ sets right on the sand — book a cabana before it fills up.",
            "category": "dining",
            "venue": "Kite Beach",
            "area": "Umm Suqeim",
            "url": "https://www.platinumlist.net/sample/kite-beach-brunch",
            "image_url": "https://images.unsplash.com/photo-1530103862676-de8c9debad1d?w=900",
            "price_from": "AED 195",
            "starts_at": at(2, 13),
            "ends_at": at(2, 17),
        },
        {
            "title": "Desert Stargazing & BBQ — Al Qudra",
            "description": "Dune drive, Bedouin dinner, and a guided night-sky session well away from the city lights.",
            "category": "outdoors",
            "venue": "Al Qudra Desert",
            "area": "Seih Al Salam",
            "url": "https://www.platinumlist.net/sample/al-qudra-stargazing",
            "image_url": "https://images.unsplash.com/photo-1509316785289-025f5b846b35?w=900",
            "price_from": "AED 250",
            "starts_at": at(3, 17),
            "ends_at": at(3, 23),
        },
        {
            "title": "Family Day at Dubai Garden Glow",
            "description": "Glowing gardens, a dinosaur park, and an ice world — easy win for a weekend with the kids.",
            "category": "family",
            "venue": "Dubai Garden Glow",
            "area": "Zabeel Park",
            "url": "https://www.platinumlist.net/sample/garden-glow",
            "image_url": "https://images.unsplash.com/photo-1518609878373-06d740f60d8b?w=900",
            "price_from": "AED 70",
            "starts_at": at(1, 16),
            "ends_at": at(1, 22),
        },
        {
            "title": "Live Jazz Night at Cuckoo's",
            "description": "Intimate live sets every week — arrive early for a good table.",
            "category": "nightlife",
            "venue": "Cuckoo's",
            "area": "Al Quoz",
            "url": "https://www.platinumlist.net/sample/cuckoos-jazz",
            "image_url": "https://images.unsplash.com/photo-1511192336575-5a79af67a629?w=900",
            "price_from": "Free",
            "starts_at": at(4, 20),
            "ends_at": at(4, 23),
        },
        {
            "title": "Dubai Fountain Boardwalk & Souk Walk",
            "description": "Sunset stroll past the fountain shows and into Souk Al Bahar — great for visitors in town.",
            "category": "events",
            "venue": "Downtown Dubai",
            "area": "Downtown",
            "url": "https://www.visitdubai.com/sample/fountain-boardwalk",
            "image_url": "https://images.unsplash.com/photo-1512453979798-5ea266f8880c?w=900",
            "price_from": "AED 25",
            "starts_at": at(5, 18),
            "ends_at": at(5, 21),
        },
    ]

    for ev in raw:
        ev["source"] = "sample"
        ev["is_published"] = True
        # Mirror the ingestion rule: keep until a day after it ends.
        ev["expires_at"] = ev["ends_at"] + timedelta(days=1)
    return raw


async def main() -> None:
    async with async_session_maker() as session:
        if settings.APIFY_TOKEN and settings.APIFY_EVENTS_ACTOR:
            n = await ingest_apify_events(session)
            print(f"Apify ({settings.APIFY_EVENTS_ACTOR}): inserted {n} new events.")
        else:
            n = await events_service.upsert_events(session, _sample_events())
            print(
                "No Apify events actor configured (set APIFY_EVENTS_ACTOR) — "
                f"inserted {n} curated sample events so the feed is demoable."
            )


if __name__ == "__main__":
    asyncio.run(main())
