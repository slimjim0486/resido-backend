"""TTL refresh for the Services directory — re-scrape only stale grid cells.

Mirrors the Tier A KB refresh (scripts/refresh_kb.py): durable rows on a slow
cadence, where a cell is "due" only when its last successful cell check has aged
past its registry TTL. Self-limits work per run, so wiring it to a weekly Railway
cron is safe.

    .venv/bin/python -m scripts.refresh_services [--ttl-days 60] [--limit N]
"""

import argparse
import asyncio

from app.config import settings
from app.database import async_session_maker
from app.ingestion import providers_maps
from app.services import providers as providers_service
from scripts.seed_services import curated_pet_adoption_rows


async def run(ttl_days: int, limit: int | None) -> None:
    if not settings.APIFY_TOKEN:
        print("APIFY_TOKEN not set — nothing to refresh (run seed_services for samples).")
        return
    async with async_session_maker() as session:
        freshness = await providers_service.cell_freshness(session)
        due = providers_maps.due_cells(freshness, ttl_days=ttl_days)
        if limit:
            due = due[:limit]
        if not due:
            print(f"Refresh: 0 cells due (TTL {ttl_days}d). Nothing to do.")
            return
        print(f"Refresh: {len(due)} cell(s) due (fallback TTL {ttl_days}d). Scraping…")
        for category, area in due:
            per_cell = category.refresh_per_cell or settings.SERVICES_PER_CELL
            ttl = category.refresh_ttl_days or ttl_days
            print(
                f"  - {category.row_category}/{category.subcategory} in {area} "
                f"(ttl={ttl}d, per_cell={per_cell})"
            )
        inserted = await providers_maps.ingest_grid(session, cells=due)
        curated = await providers_service.upsert_providers(session, curated_pet_adoption_rows())
        print(
            f"Refresh: scraped {len(due)} cell(s); {inserted} new provider(s) inserted; "
            f"upserted {curated} curated pet adoption resource(s)."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="TTL-refresh the Services directory.")
    parser.add_argument(
        "--ttl-days", type=int, default=settings.SERVICES_TTL_DAYS,
        help="re-scrape cells whose last successful check is older than this",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="max cells to refresh this run (default: 30; set 0 for no cap)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.ttl_days, args.limit or None))


if __name__ == "__main__":
    main()
