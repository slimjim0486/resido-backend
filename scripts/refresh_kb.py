"""Register the authoritative source allowlist, then refresh a staggered batch
of sources whose TTL has elapsed. Safe to run on a schedule (e.g. hourly on
Railway) — it self-limits per run and the hash gate keeps unchanged pages cheap.

    .venv/bin/python -m scripts.refresh_kb [--limit N] [--register-only]
"""

import argparse
import asyncio

from app.database import async_session_maker
from app.ingestion.refresh import refresh_sources
from app.ingestion.registry import register_sources


async def run(limit: int, register_only: bool) -> None:
    async with async_session_maker() as session:
        added = await register_sources(session)
        print(f"Registry: {added} new source(s) added.")
        if register_only:
            return
        s = await refresh_sources(session, limit=limit)
        print(
            f"Refresh: due={s['due']} re-embedded={s['embedded']} "
            f"unchanged/empty={s['skipped']} failed={s['failed']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Register + TTL-refresh the Tier A KB.")
    parser.add_argument("--limit", type=int, default=20, help="max sources to refresh this run")
    parser.add_argument("--register-only", action="store_true", help="only sync the registry")
    args = parser.parse_args()
    asyncio.run(run(args.limit, args.register_only))


if __name__ == "__main__":
    main()
