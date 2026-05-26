"""Backfill advertised pricing onto existing Service providers.

Walks providers that have a website, fetches each site (Exa→Firecrawl) and asks
Claude (Haiku) for the lowest advertised AED price + unit, then writes the
price_* columns. The same extraction the scrape now runs inline
(ingestion/providers_pricing.py) — this just applies it to rows already in the DB.

Resumable: by default only touches providers never priced before
(price_fetched_at IS NULL). Every attempted row gets price_fetched_at stamped, so
a re-run skips it whether or not a price was found. Use --all to re-price.

    .venv/bin/python -m scripts.backfill_pricing --dry-run --limit 10
    .venv/bin/python -m scripts.backfill_pricing                       # all unpriced
    .venv/bin/python -m scripts.backfill_pricing --category cleaning --area "Dubai Marina"
    .venv/bin/python -m scripts.backfill_pricing --all --limit 50      # re-price first 50
"""

import argparse
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.database import async_session_maker
from app.ingestion import providers_pricing
from app.models.service import ServiceProvider as SP


def _build_query(args):
    stmt = select(SP).where(SP.is_active.is_(True), SP.website.isnot(None))
    if not args.all:
        stmt = stmt.where(SP.price_fetched_at.is_(None))  # resumable: untried only
    if args.category:
        stmt = stmt.where(SP.category == args.category)
    if args.area:
        stmt = stmt.where(SP.area == args.area)
    # Best-trust first, so a capped run prices the providers users see soonest.
    stmt = stmt.order_by(SP.score.desc())
    if args.limit:
        stmt = stmt.limit(args.limit)
    return stmt


async def _run(args) -> None:
    if not providers_pricing._enabled():
        raise SystemExit(
            "Pricing extraction disabled — set ANTHROPIC_API_KEY and "
            "SERVICES_EXTRACT_PRICING=true."
        )

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    sem = asyncio.Semaphore(args.concurrency)

    async with async_session_maker() as session:
        providers = list((await session.execute(_build_query(args))).scalars().all())
        print(
            f"{len(providers)} providers to price "
            f"({'re-pricing all' if args.all else 'unpriced only'}"
            f"{', dry run' if args.dry_run else ''}, concurrency={args.concurrency}).\n"
        )

        async def _price(p: SP) -> tuple[SP, dict | None]:
            async with sem:
                return p, await providers_pricing.price_from_website(p.website, client=client)

        priced = attempted = 0
        # Stream results so a long run shows progress and commits in batches.
        for coro in asyncio.as_completed([_price(p) for p in providers]):
            p, data = await coro
            attempted += 1
            if data:
                priced += 1
                bits = [data["price_from"]]
                if data.get("price_to"):
                    bits.append(f"– {data['price_to']}")
                if data.get("price_unit"):
                    bits.append(data["price_unit"])
                label = " ".join(bits)
                if data.get("price_notes"):
                    label += f"  ({data['price_notes']})"
                print(f"  PRICE  {p.name[:48]:48}  {label}")
                if not args.dry_run:
                    p.price_from = data["price_from"]
                    p.price_to = data.get("price_to")
                    p.price_unit = data.get("price_unit")
                    p.price_notes = data.get("price_notes")
            if not args.dry_run:
                # Stamp every attempt (incl. misses) so re-runs skip it.
                p.price_fetched_at = datetime.now(timezone.utc)
                if attempted % 25 == 0:
                    await session.commit()

        if not args.dry_run:
            await session.commit()

        pct = (priced / attempted * 100) if attempted else 0
        print(
            f"\n>>> {priced}/{attempted} providers got a concrete price ({pct:.0f}%)."
            f"{'  (dry run — nothing written)' if args.dry_run else ''}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill website pricing onto providers.")
    parser.add_argument("--limit", type=int, default=0, help="cap providers processed (0 = all)")
    parser.add_argument("--category", help="restrict to one grid category key")
    parser.add_argument("--area", help="restrict to one area")
    parser.add_argument("--all", action="store_true", help="re-price even already-tried providers")
    parser.add_argument("--concurrency", type=int, default=5, help="parallel fetch+extract workers")
    parser.add_argument("--dry-run", action="store_true", help="print prices, write nothing")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
