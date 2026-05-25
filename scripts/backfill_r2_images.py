"""One-off: mirror already-stored image URLs into R2, in place — no re-scrape.

After R2 mirroring was added to the ingest path (see backend/INGESTION.md §4c),
existing rows still point at their original scraped URLs (e.g. Google Maps
`lh3.googleusercontent.com` photos, which are token-signed and expire). This
backfills them: download each current URL, re-host it in R2 under the *same key*
the live pipeline would use, and rewrite the row. Idempotent — rows already on
R2 are skipped, so it's safe to re-run.

Needs the R2 creds, so run it where they live (Railway):

    railway run python -m scripts.backfill_r2_images            # everything
    railway run python -m scripts.backfill_r2_images --limit 50 # smoke test first
    railway run python -m scripts.backfill_r2_images --dry-run  # report, no writes

A URL whose source has already expired will fail to download; it's left on its
original URL (logged) and the next monthly scrape will re-fetch + mirror it.
"""

import argparse
import asyncio

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import async_session_maker
from app.ingestion import r2_storage
from app.ingestion.events import _image_key
from app.ingestion.providers_maps import _photo_key
from app.models.event import Event
from app.models.service import ServiceProvider

# (label, model, url column, key builder) — same key scheme as the live pipeline,
# so a backfilled object sits exactly where a future re-scrape will overwrite it.
_TARGETS = [
    ("providers", ServiceProvider, "photo_url", lambda r: _photo_key({"place_id": r.place_id})),
    ("events", Event, "image_url", lambda r: _image_key({"url": r.url})),
]


def _on_r2(url: str | None) -> bool:
    return bool(url) and url.startswith(settings.R2_PUBLIC_URL.rstrip("/"))


async def run(limit: int | None, dry_run: bool) -> None:
    if not dry_run and not settings.r2_enabled:
        raise SystemExit(
            "R2 not configured (R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / "
            "R2_BUCKET / R2_PUBLIC_URL). Run this where the creds live (e.g. `railway run …`)."
        )

    sem = asyncio.Semaphore(8)
    async with async_session_maker() as session, httpx.AsyncClient() as client:
        for label, model, url_attr, key_fn in _TARGETS:
            stmt = select(model).where(getattr(model, url_attr).is_not(None))
            rows = list((await session.execute(stmt)).scalars().all())
            todo = [r for r in rows if not _on_r2(getattr(r, url_attr))]
            already = len(rows) - len(todo)
            if limit:
                todo = todo[:limit]
            print(
                f"{label}: {len(rows)} with image · {already} already on R2 · {len(todo)} to mirror"
                + (" (dry run — no writes)" if dry_run else "")
            )
            if dry_run or not todo:
                continue

            mirrored = 0

            async def _one(row) -> None:
                nonlocal mirrored
                src = getattr(row, url_attr)
                async with sem:
                    new = await r2_storage.mirror_image(src, key=key_fn(row), client=client)
                if _on_r2(new) and new != src:
                    setattr(row, url_attr, new)
                    mirrored += 1

            await asyncio.gather(*(_one(r) for r in todo))
            await session.commit()
            print(
                f"{label}: mirrored {mirrored} · kept {len(todo) - mirrored} on source URL "
                "(download/upload failed — will retry on next scrape)"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill existing image URLs into R2 in place.")
    parser.add_argument("--limit", type=int, default=None, help="max rows per table this run")
    parser.add_argument("--dry-run", action="store_true", help="report counts, write nothing")
    args = parser.parse_args()
    asyncio.run(run(args.limit, args.dry_run))


if __name__ == "__main__":
    main()
