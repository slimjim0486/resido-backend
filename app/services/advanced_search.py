"""Advanced retrieval for the co-pilot.

Combines verified KB search with optional live web search across multiple query
angles. Simple official-process questions should still use ``kb_search`` first;
this is for broader, current, comparative, or underspecified questions where one
query is too brittle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kb_chunk import KBChunk
from app.services.kb import search_kb
from app.services.live_search import AUTHORITATIVE_HOSTS, live_search

_MAX_QUERIES = 4
_DEFAULT_LIMIT = 8


@dataclass(slots=True)
class SearchResult:
    title: str | None
    url: str
    category: str | None
    content: str
    source_type: str
    fetched_at: str | None
    verified: bool
    query: str

    def for_model(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "category": self.category,
            "source_type": self.source_type,
            "verified": self.verified,
            "fetched_at": self.fetched_at,
            "matched_query": self.query,
            "content": self.content[:1500],
        }

    def citation(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "category": self.category,
            "fetched_at": self.fetched_at if self.verified else None,
        }


def _queries(primary: str, extras: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in [primary, *(extras or [])]:
        q = " ".join((raw or "").split())
        key = q.lower()
        if q and key not in seen:
            seen.add(key)
            out.append(q)
        if len(out) >= _MAX_QUERIES:
            break
    return out


def _clip_limit(limit: int | None) -> int:
    if limit is None:
        return _DEFAULT_LIMIT
    return max(3, min(limit, 12))


def _kb_result(chunk: KBChunk, query: str) -> SearchResult:
    fetched = chunk.fetched_at.date().isoformat() if chunk.fetched_at else None
    return SearchResult(
        title=chunk.title,
        url=chunk.url,
        category=chunk.category,
        content=chunk.content,
        source_type="verified_kb",
        fetched_at=fetched,
        verified=True,
        query=query,
    )


def _live_result(row: dict, query: str) -> SearchResult:
    return SearchResult(
        title=row.get("title"),
        url=row["url"],
        category=row.get("category"),
        content=row.get("content") or "",
        source_type="live_web",
        fetched_at=row.get("fetched_at") or date.today().isoformat(),
        verified=False,
        query=query,
    )


async def advanced_search(
    session: AsyncSession,
    query: str,
    *,
    queries: list[str] | None = None,
    category: str | None = None,
    limit: int | None = None,
    include_live: bool = True,
    official_only: bool = False,
) -> dict:
    """Return deduped KB + live results for multiple search angles."""
    wanted = _clip_limit(limit)
    query_set = _queries(query, queries)
    by_url: dict[str, SearchResult] = {}
    verified_results: list[SearchResult] = []

    kb_per_query = max(2, min(5, wanted))
    for q in query_set:
        for chunk in await search_kb(session, q, category=category, limit=kb_per_query):
            if chunk.url not in by_url:
                result = _kb_result(chunk, q)
                by_url[chunk.url] = result
                verified_results.append(result)

    live_results: list[SearchResult] = []
    if include_live:
        domains = sorted(AUTHORITATIVE_HOSTS) if official_only else None
        max_live = max(1, min(3, wanted // 3))
        per_query = max(2, min(4, wanted - min(len(by_url), wanted) or 3))
        for q in query_set:
            rows = await live_search(
                session,
                q,
                category=category,
                num_results=per_query,
                include_domains=domains,
                authoritative_only=official_only,
            )
            for row in rows:
                if row["url"] in by_url:
                    continue
                result = _live_result(row, q)
                by_url[result.url] = result
                live_results.append(result)
                if len(live_results) >= max_live:
                    break
            if len(live_results) >= max_live:
                break

    if live_results:
        live_slots = min(len(live_results), max(1, min(3, wanted // 3)))
        results = verified_results[: wanted - live_slots] + live_results[:live_slots]
    else:
        results = verified_results[:wanted]
    verified_count = sum(1 for r in results if r.verified)
    live_count = sum(1 for r in results if not r.verified)
    source_note = (
        f"Advanced search across {len(query_set)} query angle(s): "
        f"{verified_count} verified KB result(s), {live_count} live web result(s)."
    )
    if live_count:
        source_note += (
            " Live web results are fresh but not yet verified in the curated KB; "
            "confirm fees, legal rules, and deadlines against the official source."
        )

    return {
        "results": [r.for_model() for r in results],
        "citations": [r.citation() for r in results],
        "count": len(results),
        "verified_count": verified_count,
        "live_count": live_count,
        "queries": query_set,
        "source_note": source_note,
    }
