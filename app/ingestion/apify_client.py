"""Apify REST client — run an actor synchronously and return dataset items.

Used (Phase 2) for structured / recurring scrapes such as fine schedules and
price feeds that a single page-scrape can't capture.
"""

import httpx

from app.config import settings

APIFY_BASE = "https://api.apify.com/v2"


async def run_actor(actor_id: str, run_input: dict, timeout: float = 180.0) -> list[dict]:
    if not settings.APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN not set")
    url = f"{APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items?token={settings.APIFY_TOKEN}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=run_input)
        resp.raise_for_status()
        return resp.json()
