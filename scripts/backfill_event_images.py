"""One-off: give existing events a card image via og:image → R2.

Events ingested through the Exa path carry no image (the live pipeline now keeps
Exa's `image` field — see events_exa.py — but rows seeded before that don't).
This fills the gap for *existing* rows: fetch each event page's og:image,
re-host it in R2 under the same `events/<sha1(url)>` key the pipeline uses, and
store our public URL. Idempotent — rows already on R2 are skipped.

Needs R2 creds + DB access; run it where they live:

    .venv/bin/python -m scripts.backfill_event_images              # live events
    .venv/bin/python -m scripts.backfill_event_images --all        # incl. expired
    .venv/bin/python -m scripts.backfill_event_images --dry-run    # report coverage
    .venv/bin/python -m scripts.backfill_event_images --limit 20

Many event URLs are listicle anchors (`article#slug`); several events from one
article therefore share that article's og:image. That's expected — there's no
per-event art — and still beats the gradient fallback.
"""

import argparse
import asyncio
from datetime import datetime, timezone

import httpx
from sqlalchemy import not_, or_, select

from app.config import settings
from app.database import async_session_maker
from app.ingestion import og_image, r2_storage
from app.ingestion.events import _image_key
from app.models.event import Event


def _on_r2(url: str | None) -> bool:
    return bool(url) and url.startswith(settings.R2_PUBLIC_URL.rstrip("/"))


async def run(limit: int | None, dry_run: bool, include_expired: bool, force: bool) -> None:
    if not dry_run and not settings.r2_enabled:
        raise SystemExit(
            "R2 not configured (R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / "
            "R2_BUCKET / R2_PUBLIC_URL). Add the creds to .env or run where they live."
        )

    # Default: rows not yet on R2 — never had an image, or kept a source URL when an
    # earlier mirror failed (reprocessing the latter makes the backfill self-healing).
    # --force: re-fetch + re-upload *every* event, overwriting the R2 object in place
    # (same key) — used to re-mirror after the download Accept header changed.
    pub = settings.R2_PUBLIC_URL.rstrip("/") + "%"
    stmt = select(Event)
    if not force:
        stmt = stmt.where(or_(Event.image_url.is_(None), not_(Event.image_url.like(pub))))
    if not include_expired:
        now = datetime.now(timezone.utc)
        stmt = stmt.where(Event.is_published.is_(True)).where(
            or_(Event.expires_at.is_(None), Event.expires_at >= now)
        )

    sem = asyncio.Semaphore(6)
    async with async_session_maker() as session, httpx.AsyncClient() as client:
        rows = list((await session.execute(stmt)).scalars().all())
        if limit:
            rows = rows[:limit]
        scope = "all" if include_expired else "live"
        print(f"events ({scope}) not yet on R2: {len(rows)}" + (" — dry run" if dry_run else ""))
        if not rows:
            return

        found = 0
        mirrored = 0

        async def _one(ev: Event) -> None:
            nonlocal found, mirrored
            async with sem:
                og = await og_image.fetch_og_image(client, ev.url)
                if not og:
                    return
                found += 1
                if dry_run:
                    return
                new = await r2_storage.mirror_image(og, key=_image_key({"url": ev.url}), client=client)
            # Prefer the R2 URL; fall back to the source og URL if mirroring failed
            # (news og images are usually hotlink-friendly, so a card still renders).
            ev.image_url = new
            if _on_r2(new):
                mirrored += 1

        await asyncio.gather(*(_one(r) for r in rows))

        if dry_run:
            print(f"og:image found for {found}/{len(rows)} (no writes)")
            return
        await session.commit()
        print(
            f"og:image found {found}/{len(rows)} · mirrored to R2 {mirrored} · "
            f"{found - mirrored} kept on source URL · {len(rows) - found} left on gradient"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill event card images via og:image → R2.")
    parser.add_argument("--limit", type=int, default=None, help="max events this run")
    parser.add_argument("--dry-run", action="store_true", help="report og:image coverage, no writes")
    parser.add_argument("--all", action="store_true", help="include expired events too")
    parser.add_argument(
        "--force", action="store_true",
        help="re-fetch + re-upload every event (overwrite R2 object in place)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.limit, args.dry_run, args.all, args.force))


if __name__ == "__main__":
    main()
