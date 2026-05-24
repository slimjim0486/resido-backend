"""Seed the knowledge base for the Phase-1 categories (Visa + Health Insurance).

Run from the backend/ directory once DATABASE_URL and FIRECRAWL_API_KEY are set:

    .venv/bin/python -m scripts.seed_kb
"""

import asyncio

from app.config import settings
from app.database import async_session_maker
from app.ingestion.pipeline import ingest_url

# Curated authoritative sources. Expand per category in Phase 2.
SEED: dict[str, list[str]] = {
    "visa": [
        "https://u.ae/en/information-and-services/visa-and-emirates-id/residence-visas",
        "https://u.ae/en/information-and-services/visa-and-emirates-id/getting-a-residence-visa",
        "https://u.ae/en/information-and-services/visa-and-emirates-id/renew-residency-visa",
    ],
    "insurance": [
        "https://u.ae/en/information-and-services/health-and-fitness/getting-a-health-insurance",
        "https://u.ae/en/information-and-services/health-and-fitness/health-insurance-in-the-uae",
    ],
}


async def main() -> None:
    if not settings.FIRECRAWL_API_KEY:
        print("⚠️  FIRECRAWL_API_KEY not set — add it to .env to seed the KB.")
        return
    if not settings.VOYAGE_API_KEY and settings.EMBEDDINGS_PROVIDER == "voyage":
        print("ℹ️  No embeddings key — chunks will be stored without vectors "
              "(retrieval falls back to keyword search).")

    total = 0
    async with async_session_maker() as session:
        for category, urls in SEED.items():
            for url in urls:
                try:
                    n = await ingest_url(session, url, category)
                    print(f"  [{category}] {url} -> {n} chunks")
                    total += n
                except Exception as exc:
                    print(f"  [{category}] {url} FAILED: {exc}")
    print(f"\nDone. Ingested {total} chunks.")


if __name__ == "__main__":
    asyncio.run(main())
