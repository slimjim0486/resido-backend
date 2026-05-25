"""Ingestion pipeline: discover (Exa) → scrape (Firecrawl) → chunk → embed →
upsert into pgvector. Each chunk records its source URL + fetched_at for citation.
"""

import hashlib
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.ingestion import exa_client, firecrawl_client
from app.ingestion.embeddings import embed_documents, embeddings_available
from app.models.kb_chunk import KBChunk
from app.models.source import Source

logger = get_logger(__name__)


def chunk_text(text: str, max_chars: int = 1200, overlap: int = 150) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


async def _get_or_create_source(
    session: AsyncSession, url: str, category: str, title: str | None
) -> Source:
    result = await session.execute(select(Source).where(Source.url == url))
    source = result.scalar_one_or_none()
    if source is None:
        source = Source(url=url, category=category, title=title)
        session.add(source)
    if title and not source.title:
        source.title = title
    return source


async def _has_chunks(session: AsyncSession, url: str) -> bool:
    result = await session.execute(select(KBChunk.id).where(KBChunk.url == url).limit(1))
    return result.first() is not None


async def _fetch_content(url: str) -> dict:
    """Fetch a known URL's text. Exa's index first (cheap, bypasses the
    proxy/JS blockers that stop Firecrawl on u.ae), Firecrawl as the
    clean-markdown fallback for pages Exa hasn't indexed well."""
    try:
        hit = (await exa_client.get_contents([url])).get(url)
        if hit and len((hit.get("text") or "").strip()) > 200:
            return {"markdown": hit["text"], "title": hit.get("title")}
    except Exception as exc:  # pragma: no cover - fall through to Firecrawl
        logger.warning("exa_contents_failed", url=url, error=str(exc))
    return await firecrawl_client.scrape_markdown(url)


async def ingest_url(
    session: AsyncSession,
    url: str,
    category: str,
    *,
    title: str | None = None,
    text: str | None = None,
) -> int:
    # ``text`` lets callers pass content already returned by Exa search,
    # avoiding a second fetch for the same page.
    if text and len(text.strip()) > 200:
        markdown = text
    else:
        scraped = await _fetch_content(url)
        markdown = scraped["markdown"]
        title = title or scraped.get("title")
    title = title or url

    pieces = chunk_text(markdown)
    if not pieces:
        logger.warning("ingest_empty", url=url)
        return 0

    content_hash = _content_hash(markdown)
    source = await _get_or_create_source(session, url, category, title)
    now = datetime.now(timezone.utc)

    # Cost gate: if the page is byte-identical to what we last embedded and its
    # chunks are still present, skip the chunk-replace + re-embed entirely —
    # only reset the TTL clock. Embeddings are the recurring spend; this is the
    # lever that keeps steady-state refresh ≈ free.
    if source.content_hash == content_hash and await _has_chunks(session, url):
        source.last_fetched_at = now
        await session.commit()
        logger.info("ingest_unchanged", url=url)
        return 0

    embeddings: list[list[float]] | None = None
    if embeddings_available():
        embeddings = await embed_documents(pieces)

    # Refresh: drop prior chunks for this URL before inserting the new ones.
    await session.execute(delete(KBChunk).where(KBChunk.url == url))
    await session.flush()  # assigns source.id for the FK below on first insert

    for i, piece in enumerate(pieces):
        session.add(
            KBChunk(
                source_id=source.id,
                category=category,
                title=title,
                url=url,
                content=piece,
                embedding=embeddings[i] if embeddings else None,
                token_count=len(piece) // 4,
            )
        )
    source.content_hash = content_hash
    source.last_fetched_at = now
    await session.commit()
    logger.info("ingested", url=url, chunks=len(pieces), embedded=bool(embeddings))
    return len(pieces)


async def ingest_query(
    session: AsyncSession,
    query: str,
    category: str,
    *,
    num_results: int = 5,
    include_domains: list[str] | None = None,
) -> int:
    results = await exa_client.search(
        query, num_results=num_results, include_domains=include_domains, with_text=True
    )
    total = 0
    for r in results:
        try:
            total += await ingest_url(
                session, r["url"], category, title=r.get("title"), text=r.get("text")
            )
        except Exception as exc:  # pragma: no cover - per-URL resilience
            logger.warning("ingest_failed", url=r.get("url"), error=str(exc))
    return total
