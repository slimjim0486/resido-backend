"""Ingestion pipeline: discover (Exa) → scrape (Firecrawl) → chunk → embed →
upsert into pgvector. Each chunk records its source URL + fetched_at for citation.
"""

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


async def _upsert_source(session: AsyncSession, url: str, category: str, title: str | None) -> Source:
    result = await session.execute(select(Source).where(Source.url == url))
    source = result.scalar_one_or_none()
    if source is None:
        source = Source(url=url, category=category, title=title)
        session.add(source)
    source.last_fetched_at = datetime.now(timezone.utc)
    if title and not source.title:
        source.title = title
    await session.commit()
    await session.refresh(source)
    return source


async def ingest_url(session: AsyncSession, url: str, category: str, *, title: str | None = None) -> int:
    scraped = await firecrawl_client.scrape_markdown(url)
    markdown = scraped["markdown"]
    title = title or scraped.get("title") or url

    pieces = chunk_text(markdown)
    if not pieces:
        logger.warning("ingest_empty", url=url)
        return 0

    source = await _upsert_source(session, url, category, title)

    embeddings: list[list[float]] | None = None
    if embeddings_available():
        embeddings = await embed_documents(pieces)

    # Refresh: drop prior chunks for this URL before inserting the new ones.
    await session.execute(delete(KBChunk).where(KBChunk.url == url))

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
    results = await exa_client.search(query, num_results=num_results, include_domains=include_domains)
    total = 0
    for r in results:
        try:
            total += await ingest_url(session, r["url"], category, title=r.get("title"))
        except Exception as exc:  # pragma: no cover - per-URL resilience
            logger.warning("ingest_failed", url=r.get("url"), error=str(exc))
    return total
