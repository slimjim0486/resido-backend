"""Exa REST client — discover authoritative URLs for a query."""

import httpx

from app.config import settings

EXA_BASE = "https://api.exa.ai"


async def search(
    query: str,
    num_results: int = 5,
    include_domains: list[str] | None = None,
    timeout: float = 30.0,
) -> list[dict]:
    if not settings.EXA_API_KEY:
        raise RuntimeError("EXA_API_KEY not set")
    payload: dict = {"query": query, "numResults": num_results, "type": "auto"}
    if include_domains:
        payload["includeDomains"] = include_domains
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{EXA_BASE}/search",
            headers={"x-api-key": settings.EXA_API_KEY},
            json=payload,
        )
        resp.raise_for_status()
        return [
            {"url": r["url"], "title": r.get("title")}
            for r in resp.json().get("results", [])
            if r.get("url")
        ]
