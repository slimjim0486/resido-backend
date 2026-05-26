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
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # ─── Auth ───
    OTP_EXPIRE_MINUTES: int = 10
    OTP_RESEND_SECONDS: int = 60
    OTP_MAX_ATTEMPTS: int = 5
    OTP_REQUESTS_PER_HOUR: int = 5
    APPLE_CLIENT_ID: str = ""  # iOS bundle ID or Services ID used as the token audience.
    RESEND_API_KEY: str = ""
    RESEND_FROM_EMAIL: str = "Resido <no-reply@resido.app>"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    OTP_EMAIL_FROM: str = "Resido <no-reply@resido.app>"

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
    # Off-the-shelf Apify Google Maps actor that powers the Services vertical
    # (Tier A-style durable provider rows; see backend/INGESTION.md). Reuses
    # APIFY_TOKEN — no new secret. Unset/blank → seed uses curated samples.
    APIFY_MAPS_ACTOR: str = "compass/crawler-google-places"
    # Providers scraped per (category × area) grid cell each run.
    SERVICES_PER_CELL: int = 15
    # Re-scrape a grid cell only when its freshest row is older than this (days).
    # Monthly cadence: durable rows, not a churny feed.
    SERVICES_TTL_DAYS: int = 30
    # Open each place's detail page during a scrape to capture opening hours.
    # The biggest cost/time lever — turn off (SERVICES_SCRAPE_DETAILS=false) for
    # fast, cheap re-seeds; you lose `hours` but keep everything else.
    SERVICES_SCRAPE_DETAILS: bool = True
    # Extract real advertised pricing from each provider's website via Claude
    # (Haiku) on the write path — Google Maps gives no usable price for these
    # businesses. Gated together with ANTHROPIC_API_KEY: no key → no-op, rows keep
    # null pricing (graceful degradation). Turn off for fast scrapes that skip the
    # extra fetch + LLM call per provider. See ingestion/providers_pricing.py.
    SERVICES_EXTRACT_PRICING: bool = True
    # Retire a provider after it's missed this many days of scrapes (≈ 2 monthly
    # cycles) — soft-hides stale/closed listings without dropping ranking-noise.
    SERVICES_STALE_GRACE_DAYS: int = 60
    # Daily cleanup removes only high-confidence cross-source duplicates. Events
    # are deleted; providers are soft-hidden to preserve durable attribution.
    DEDUPLICATOR_CONFIDENCE_THRESHOLD: float = 0.90

    # ─── Object storage: Cloudflare R2 (scraped-image mirroring) ───
    # Scraped image URLs (Google Maps photos, ticket-site images) are short-lived
    # — token-signed, hotlink-protected, or rate-limited. At ingest we re-host them
    # in the R2 `resido` bucket and store the stable public URL instead. All five
    # must be set (they live on the Railway service) or mirroring no-ops and the
    # original source URL is kept. Set the same names in Railway's variables.
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET: str = "resido"
    # The bucket's Public Development URL (or custom domain), no trailing slash,
    # e.g. https://pub-xxxxxxxx.r2.dev — NOT the account-level S3 API endpoint.
    R2_PUBLIC_URL: str = ""

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

    @property
    def r2_enabled(self) -> bool:
        """True only when every R2 credential is present; gates image mirroring."""
        return all(
            (
                self.R2_ACCOUNT_ID,
                self.R2_ACCESS_KEY_ID,
                self.R2_SECRET_ACCESS_KEY,
                self.R2_BUCKET,
                self.R2_PUBLIC_URL,
            )
        )

    @property
    def r2_endpoint_host(self) -> str:
        """Account-level S3 API host R2 uploads target (derived, not configured)."""
        return f"{self.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
