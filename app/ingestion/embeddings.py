"""Embeddings provider abstraction (Voyage default, OpenAI optional).

Degrades gracefully: callers can check `embeddings_available()` and fall back to
keyword search when no provider key is set.
"""

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def embeddings_available() -> bool:
    if settings.EMBEDDINGS_PROVIDER == "voyage":
        return bool(settings.VOYAGE_API_KEY)
    if settings.EMBEDDINGS_PROVIDER == "openai":
        return bool(settings.OPENAI_API_KEY)
    return False


async def _embed(texts: list[str], input_type: str) -> list[list[float]]:
    provider = settings.EMBEDDINGS_PROVIDER
    if provider == "voyage" and settings.VOYAGE_API_KEY:
        import voyageai

        client = voyageai.AsyncClient(api_key=settings.VOYAGE_API_KEY)
        result = await client.embed(
            texts,
            model=settings.VOYAGE_MODEL,
            input_type=input_type,
            output_dimension=settings.EMBEDDING_DIM,
        )
        return result.embeddings
    if provider == "openai" and settings.OPENAI_API_KEY:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        result = await client.embeddings.create(model="text-embedding-3-small", input=texts)
        return [item.embedding for item in result.data]
    raise RuntimeError("No embeddings provider configured (set VOYAGE_API_KEY or OPENAI_API_KEY)")


async def embed_documents(texts: list[str]) -> list[list[float]]:
    return await _embed(texts, "document")


async def embed_query(text: str) -> list[float]:
    return (await _embed([text], "query"))[0]
