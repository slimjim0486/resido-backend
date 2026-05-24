"""Knowledge-base retrieval (RAG). Vector search via pgvector when embeddings are
available; keyword fallback otherwise so the agent still works during bootstrap.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.embeddings import embed_query, embeddings_available
from app.models.kb_chunk import KBChunk


async def search_kb(
    session: AsyncSession,
    query: str,
    *,
    category: str | None = None,
    limit: int = 5,
) -> list[KBChunk]:
    stmt = select(KBChunk)
    if category:
        stmt = stmt.where(KBChunk.category == category)

    if embeddings_available():
        vector = await embed_query(query)
        stmt = stmt.order_by(KBChunk.embedding.cosine_distance(vector)).limit(limit)
    else:
        stmt = stmt.where(KBChunk.content.ilike(f"%{query}%")).limit(limit)

    result = await session.execute(stmt)
    return list(result.scalars().all())
