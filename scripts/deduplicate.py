"""Daily duplicate cleanup for events and service providers.

Runs a deterministic confidence scorer over live customer-visible rows and
removes pairs at or above the configured threshold.

    python -m scripts.deduplicate
    python -m scripts.deduplicate --dry-run --threshold 0.9
"""

import argparse
import asyncio

from app.config import settings
from app.database import async_session_maker
from app.services.deduplicator import run_deduplicator


async def run(*, threshold: float, dry_run: bool, scope: str) -> None:
    async with async_session_maker() as session:
        summary = await run_deduplicator(
            session,
            threshold=threshold,
            dry_run=dry_run,
            scope=scope,
        )
    mode = "DRY RUN" if dry_run else "APPLIED"
    print(
        f"Deduplicator ({mode}): checked "
        f"{summary.events_checked} event(s), {summary.providers_checked} provider(s)."
    )
    print(
        f"Deduplicator ({mode}): removed "
        f"{summary.event_duplicates} event duplicate(s), "
        f"{summary.provider_duplicates} provider duplicate(s) "
        f"at threshold {summary.threshold:.2f}."
    )
    for decision in summary.decisions or []:
        print(
            f"- {decision.item_type}: duplicate={decision.duplicate_id} "
            f"kept={decision.kept_id} confidence={decision.confidence:.2f} "
            f"({decision.reason})"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove high-confidence duplicate rows.")
    parser.add_argument(
        "--threshold",
        type=float,
        default=settings.DEDUPLICATOR_CONFIDENCE_THRESHOLD,
        help="minimum confidence required to remove a duplicate (default: 0.90)",
    )
    parser.add_argument(
        "--scope",
        choices=("all", "events", "providers"),
        default="all",
        help="which customer-visible item table to deduplicate",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="score and print duplicates without changing the database",
    )
    args = parser.parse_args()
    asyncio.run(run(threshold=args.threshold, dry_run=args.dry_run, scope=args.scope))


if __name__ == "__main__":
    main()
