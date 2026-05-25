"""Application settings, loaded from environment / .env (pydantic-settings)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ─── App ───
    APP_NAME: str = "Resido"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    SECRET_KEY: str = "change-me-please"

    # ─── Database ───
    # Accepts a raw Railway URL (postgresql://...) or a driver-qualified one; the
    # async (+asyncpg) and sync (+psycopg) forms are derived in the properties below.
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/resido"
    SYNC_DATABASE_URL: str = ""  # optional override; derived from DATABASE_URL when blank

    # ─── Redis ───
    REDIS_URL: str = "redis://localhost:6379/0"

    # ─── JWT ───
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # ─── AI: Claude ───
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-6"
    # Cheaper/faster model for high-volume structured extraction (events feed).
    CLAUDE_EXTRACT_MODEL: str = "claude-haiku-4-5"

    # ─── Embeddings ───
    EMBEDDINGS_PROVIDER: str = "voyage"  # voyage | openai
    VOYAGE_API_KEY: str = ""
    VOYAGE_MODEL: str = "voyage-4-large"  # SOTA (Jan 2026); voyage-3.5 = budget option
    OPENAI_API_KEY: str = ""
    # Voyage 3.5/4 support 2048|1024|512|256; we request 1024 to match the pgvector column.
    EMBEDDING_DIM: int = 1024

    # ─── Ingestion / AI search ───
    FIRECRAWL_API_KEY: str = ""
    EXA_API_KEY: str = ""
    APIFY_TOKEN: str = ""
    # Apify actor that scrapes the lifestyle events feed (Tier B), e.g.
    # "<username>/dubai-events-scraper". Unset → seed uses curated samples.
    APIFY_EVENTS_ACTOR: str = ""
    # Exa→Claude structured event extraction (Tier B+, see EVENTS_EXTRACTION.md).
    EVENTS_EXTRACT: bool = True
    EVENTS_EXTRACT_MAX_PAGES: int = 3  # pages/category sent to Claude

    # ─── CORS ───
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:8080"

    @staticmethod
    def _with_driver(url: str, driver: str) -> str:
        """Force a SQLAlchemy driver onto a postgres URL, regardless of input form."""
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql+"):
            url = "postgresql://" + url.split("://", 1)[1]
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", f"postgresql+{driver}://", 1)
        return url

    @property
    def async_database_url(self) -> str:
        return self._with_driver(self.DATABASE_URL, "asyncpg")

    @property
    def sync_database_url(self) -> str:
        return self._with_driver(self.SYNC_DATABASE_URL or self.DATABASE_URL, "psycopg")

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ANTHROPIC_API_KEY)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
