"""Haiku distillation of user taste profiles (Phase 2 AI layer).

Refreshes each active user's preference `summary` + nuanced `inferred` notes from
their saved/requested history (see PERSONALIZATION.md / preferences_distill.py).
Self-limiting and idempotent: only users with enough signal whose profile is new
or stale are touched, oldest-first, capped per run — so a daily Railway cron is safe.
No Claude key → no-op (the rules-templated summary stands).

    .venv/bin/python -m scripts.distill_preferences [--min-signals 3] [--stale-hours 12] [--limit 200]
    .venv/bin/python -m scripts.distill_preferences --user <uuid>   # one user, ignores staleness
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.config import settings
from app.database import async_session_maker
from app.services import preferences
from app.services import preferences_distill as distill


async def run(*, min_signals: int, stale_hours: int, limit: int, user: str | None) -> None:
    if not settings.ai_enabled:
        print("ANTHROPIC_API_KEY not set — distillation is a no-op (rules summary stands).")
        return
    async with async_session_maker() as session:
        if user:
            try:
                candidates = [UUID(user)]
            except ValueError:
                print(f"Invalid --user uuid: {user}")
                return
        else:
            stale_before = datetime.now(timezone.utc) - timedelta(hours=stale_hours)
            candidates = await preferences.select_distillation_candidates(
                session, min_signals=min_signals, stale_before=stale_before, limit=limit
            )
        if not candidates:
            print("Distill: 0 profiles due. Nothing to do.")
            return

        print(f"Distill: {len(candidates)} profile(s) due. Running Haiku…")
        distilled = 0
        for uid in candidates:
            try:
                if await distill.distill_user(session, uid):
                    distilled += 1
            except Exception as exc:  # one bad user must not abort the run
                print(f"  ! {uid}: {exc}")
        print(f"Distill: distilled {distilled}/{len(candidates)} profile(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Haiku-distill user taste profiles.")
    parser.add_argument("--min-signals", type=int, default=3, help="skip profiles below this signal count")
    parser.add_argument("--stale-hours", type=int, default=12, help="re-distill only if older than this")
    parser.add_argument("--limit", type=int, default=200, help="max profiles per run")
    parser.add_argument("--user", type=str, default=None, help="distill one user id (ignores staleness)")
    args = parser.parse_args()
    asyncio.run(
        run(
            min_signals=args.min_signals,
            stale_hours=args.stale_hours,
            limit=args.limit,
            user=args.user,
        )
    )


if __name__ == "__main__":
    main()
