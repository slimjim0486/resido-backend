"""Exa REST client.

Discovers authoritative URLs for a query and can return their page text
straight from Exa's own index. Serving text from the index sidesteps the
proxy/JS-render blockers that stop Firecrawl from reaching sites like ``u.ae``,
and folds discovery + fetch into a single billed call.
"""

import httpx

from app.config import settings

EXA_BASE = "https://api.exa.ai"

# Cap per-page text so a single huge page can't blow up embedding cost; ~8k
# chars is comfortably more than our 1200-char chunks need.
_MAX_CHARS = 8000


async def search(
    query: str,
    num_results: int = 5,
    include_domains: list[str] | None = None,
    *,
    with_text: bool = False,
    timeout: float = 30.0,
) -> list[dict]:
    """Search Exa for authoritative URLs.

    When ``with_text`` is set, Exa also returns each page's text from its index
    so the caller can skip a separate scrape entirely.
    """
    if not settings.EXA_API_KEY:
        raise RuntimeError("EXA_API_KEY not set")
    payload: dict = {"query": query, "numResults": num_results, "type": "auto"}
    if include_domains:
        payload["includeDomains"] = include_domains
    if with_text:
        payload["contents"] = {"text": {"maxCharacters": _MAX_CHARS}}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{EXA_BASE}/search",
            headers={"x-api-key": settings.EXA_API_KEY},
            json=payload,
        )
        resp.raise_for_status()
        return [
            {
                "url": r["url"],
                "title": r.get("title"),
                "text": (r.get("text") or "") if with_text else "",
                "published_date": r.get("publishedDate"),
            }
            for r in resp.json().get("results", [])
            if r.get("url")
        ]


async def get_contents(urls: list[str], *, timeout: float = 30.0) -> dict[str, dict]:
    """Fetch page text for already-known URLs from Exa's index, keyed by URL.

    This is the primary fetch path for sources Firecrawl cannot reach (notably
    the JS-rendered, proxy-blocked ``u.ae``).
    """
    if not settings.EXA_API_KEY:
        raise RuntimeError("EXA_API_KEY not set")
    if not urls:
        return {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{EXA_BASE}/contents",
            headers={"x-api-key": settings.EXA_API_KEY},
            json={"urls": urls, "text": {"maxCharacters": _MAX_CHARS}},
        )
        resp.raise_for_status()
        return {
            r["url"]: {"text": r.get("text") or "", "title": r.get("title")}
            for r in resp.json().get("results", [])
            if r.get("url")
        }
