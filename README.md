# Resido — Backend

*(working name)* Backend for an **AI co-pilot for living in Dubai**: ask anything across
bureaucracy + lifestyle and get current, **cited**, actionable answers; an AI agent manages
the user's checklist, deadlines, documents, and service leads.

## Stack
FastAPI (async SQLAlchemy) · Postgres + `pgvector` · Redis · Alembic · Anthropic Claude
(tool-use agent + prompt caching) · Voyage embeddings · Exa + Firecrawl + Apify ingestion.

## Layout
```
app/
├── api/v1/        auth · chat · me
├── agent/         Claude tool-use loop (READ/WRITE/EDIT tools) + prompts
├── ingestion/     Exa → Firecrawl → embed → pgvector  (+ Apify for structured feeds)
├── models/        users, profile, checklist, deadlines, documents, sources, kb_chunks, leads
├── services/      workspace (shared by API + agent), kb retrieval
├── db/, core/, utils/, config.py, database.py, dependencies.py, main.py
alembic/           migrations (0001 enables pgvector + creates schema)
scripts/seed_kb.py seed the Visa + Health-Insurance KB
```

## Run locally
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill secrets (DATABASE_URL, ANTHROPIC_API_KEY, VOYAGE_API_KEY, EXA_API_KEY, FIRECRAWL_API_KEY)
alembic upgrade head
uvicorn app.main:app --reload
# seed the knowledge base (needs Firecrawl + embeddings keys):
python -m scripts.seed_kb
```

## Deploy (Railway)
`railway.json` runs `alembic upgrade head` then serves uvicorn on `$PORT`, with `/health`
as the healthcheck. Set the service's env vars (see `.env.example`) and point it at this repo.

**Scheduled ingestion** runs as two cron services in the same project:
`railway.refresh-kb.json` (Tier A KB refresh, every 6h) and `railway.seed-events.json`
(Tier B events feed, daily). Setup + per-job env vars: see `DEPLOY_CRON.md`.
