"""Firecrawl REST client — scrape a URL to clean markdown."""

import httpx

from app.config import settings

FIRECRAWL_BASE = "https://api.firecrawl.dev/v1"


class FirecrawlError(RuntimeError):
    pass


async def scrape_markdown(url: str, timeout: float = 60.0) -> dict:
    if not settings.FIRECRAWL_API_KEY:
        raise FirecrawlError("FIRECRAWL_API_KEY not set")
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{FIRECRAWL_BASE}/scrape",
            headers={"Authorization": f"Bearer {settings.FIRECRAWL_API_KEY}"},
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        metadata = data.get("metadata") or {}
        return {"markdown": data.get("markdown", "") or "", "title": metadata.get("title")}
