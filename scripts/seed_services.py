"""Seed / refresh the Services directory (ranked local providers).

If APIFY_TOKEN is set, scrapes the full (category × area) grid via the Apify
Google Maps actor. Otherwise inserts a small curated sample (clearly sourced as
'sample') so the Services screens are demoable without a live scrape — mirrors
seed_events.py.

    .venv/bin/python -m scripts.seed_services            # grid scrape or samples
    .venv/bin/python -m scripts.seed_services --dry-run  # 1 cell, print, no write
    .venv/bin/python -m scripts.seed_services --dry-run --category ac_repair --area "Dubai Marina"
"""

import argparse
import asyncio
import json

from app.config import settings
from app.database import async_session_maker
from app.ingestion import providers_maps
from app.ingestion.services_registry import CATEGORIES, CATEGORY_BY_KEY
from app.services import providers as providers_service


def _sample_providers() -> list[dict]:
    """A handful of representative Dubai providers so the directory lights up for a
    demo. Ratings/review counts vary so the Bayesian ranking is visibly at work."""
    raw = [
        ("cleaning", "Dubai Marina", "Marina Sparkle Home Cleaning", 4.7, 812, "$$"),
        ("cleaning", "JLT", "JLT Maids & More", 4.4, 230, "$"),
        ("cleaning", "Dubai Marina", "FreshNest Cleaning (new)", 5.0, 6, "$$"),
        ("ac_repair", "Business Bay", "CoolPro AC Maintenance", 4.8, 540, "$$"),
        ("ac_repair", "JVC", "ChillTech AC Services", 4.5, 178, "$"),
        ("handyman", "Downtown Dubai", "FixIt Dubai Handyman", 4.6, 401, "$$"),
        ("plumbing", "Bur Dubai", "AquaFix Plumbing", 4.5, 96, "$$"),
        ("electrician", "Deira", "Sharp Sparks Electrical", 4.3, 64, "$"),
        ("movers", "Dubai Hills", "Smooth Move Packers", 4.9, 1240, "$$$"),
        ("pest_control", "Mirdif", "PestAway Dubai", 4.4, 142, "$$"),
        ("maid_service", "Jumeirah", "Pearl Maids Service", 4.6, 318, "$$"),
        ("car_service", "Al Quoz", "AutoCare Garage Dubai", 4.7, 905, "$$"),
        ("laundry", "Dubai Marina", "Crisp & Clean Laundry", 4.5, 274, "$"),
    ]
    rows: list[dict] = []
    for i, (cat, area, name, rating, reviews, price) in enumerate(raw, start=1):
        slug = name.lower().replace(" ", "-").replace("&", "and")
        rows.append(
            {
                "name": name,
                "category": cat,
                "area": area,
                "address": f"{area}, Dubai, UAE",
                "rating": rating,
                "reviews_count": reviews,
                "phone": "+9714 000 0000",
                "whatsapp": "97150000000" + str(i % 10),
                "website": f"https://example.com/{slug}",
                "maps_url": f"https://www.google.com/maps/search/?api=1&query={slug}",
                "place_id": f"sample-{slug}",
                "price_level": price,
                "hours": [{"day": "Mon–Sat", "hours": "9 AM–9 PM"}],
                "review_curation": {
                    "summary": "Customers tend to mention reliable work and clear communication.",
                    "positives": ["Reliable and punctual", "Good communication"],
                    "watchouts": ["Limited review volume"] if reviews < 25 else [],
                    "sample_size": 8 if reviews >= 25 else 0,
                    "sample_average_rating": rating if reviews >= 25 else None,
                    "rating_basis": f"{rating:.1f} from {reviews} Google reviews",
                },
                "google_rank": i,
                "is_sponsored": False,
                "source": "sample",
            }
        )
    return rows


async def _dry_run(category_key: str, area: str) -> None:
    category = CATEGORY_BY_KEY.get(category_key)
    if not category:
        keys = ", ".join(c.key for c in CATEGORIES)
        raise SystemExit(f"Unknown category '{category_key}'. Choose from: {keys}")
    if not settings.APIFY_TOKEN:
        raise SystemExit("APIFY_TOKEN not set — dry run needs a live scrape.")
    print(f"Dry run (NO DB write): {category.label} in {area}…\n")
    rows = await providers_maps.dry_run_cell(category, area, per_cell=settings.SERVICES_PER_CELL)
    rows.sort(key=lambda r: r["score"], reverse=True)
    for r in rows:
        print(
            f"  {r['score']:5.3f}  ★{r.get('rating') or '–'} ({r['reviews_count']:>4} reviews)  "
            f"{r['name']}  [{r.get('area')}]"
        )
    print(f"\n{len(rows)} providers normalized + scored (none written).")
    if rows:
        print("\nFirst row (full normalized shape):")
        print(json.dumps(rows[0], indent=2, default=str))


async def _seed() -> None:
    async with async_session_maker() as session:
        if settings.APIFY_TOKEN:
            n = await providers_maps.ingest_grid(session)
            print(f"Apify ({settings.APIFY_MAPS_ACTOR}): inserted {n} new providers across the grid.")
        else:
            n = await providers_service.upsert_providers(session, _sample_providers())
            print(
                "No APIFY_TOKEN configured — "
                f"inserted {n} curated sample providers so the directory is demoable."
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed / refresh the Services directory.")
    parser.add_argument("--dry-run", action="store_true", help="scrape 1 cell, print, no DB write")
    parser.add_argument("--category", default="ac_repair", help="dry-run category key")
    parser.add_argument("--area", default="Dubai Marina", help="dry-run area")
    args = parser.parse_args()
    if args.dry_run:
        asyncio.run(_dry_run(args.category, args.area))
    else:
        asyncio.run(_seed())


if __name__ == "__main__":
    main()
