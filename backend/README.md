# Backend Scaffold

This repository now has a **working backend skeleton** you can run locally for dry runs, scoring checks, and ingestion dedup validation.

## What works right now

### 1) API service boots and exposes working endpoints

- `GET /health` → service health status
- `GET /jobs/dag` → current daily orchestration DAG
- `POST /jobs/dry-run` → Hermes-safe dry-run execution of all DAG steps
- `GET /recommendations/score` → deterministic sample score from FR-7 formula

### 2) Deterministic recommendation + feedback math is implemented

- FR-7 scoring formula implemented in `backend/recommendation/scoring.py`
- Feedback decay and rejection penalty primitives in `backend/feedback/scoring.py`

### 3) Ingestion path works with dedup behavior

- Canonical keying (`ISRC` first, metadata fallback)
- Ingestion service inserts new tracks/listens
- Duplicate listens are detected and skipped deterministically

### 4) Database schema scaffold exists

- SQLAlchemy models for `Track`, `Listen`, `FeedbackEvent`, `Lane`
- Session factory scaffold for wiring DB runtime

### 5) Provider onboarding can be incremental

Provider credentials are optional. Missing credentials means that provider is disabled for now.

- Spotify enabled when both `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` are set
- Last.fm enabled when `LASTFM_API_KEY` is set
- TIDAL enabled when both `TIDAL_CLIENT_ID` and `TIDAL_CLIENT_SECRET` are set
- YTMusic enabled when `YTMUSIC_OAUTH_TOKEN` is set
- MusicBrainz enabled when `MUSICBRAINZ_USER_AGENT` is set

---

## Run

```bash
uvicorn backend.api.main:app --reload
```

## Run tests

```bash
PYTHONPATH=. pytest backend/tests
```

---

## Environment variables and API keys

### 1) Provider keys are optional by design

If a provider key/token is not set, that provider is considered **disabled**.
This means you can roll out providers incrementally (e.g., Spotify first, TIDAL later).

### 2) Never commit real secrets

- Keep real credentials only in a local `.env` file (or secret manager), never in Git.
- `.env.example` is committed as a template and must contain placeholder values only.
- Rotate any key immediately if it is accidentally committed.

### 3) Bootstrap local env

```bash
cp backend/.env.example backend/.env
```

Then update `backend/.env` with only the providers you want enabled.

### 4) Core runtime env

- `APP_ENV` (default: `development`)
- `LOG_LEVEL` (default: `INFO`)
- `DATABASE_URL`
- `ENCRYPTION_KEY` (for future token-at-rest encryption work)

### 5) Loading env in development

This scaffold exposes `backend.config.get_settings()` and `Settings.is_provider_enabled()`.
Use your preferred loader before starting the app, for example:

```bash
set -a; source backend/.env; set +a
uvicorn backend.api.main:app --reload
```

---

## Recommended next steps

1. Add real provider adapters (Spotify/Last.fm first) implementing `ListeningHistoryAdapter`.
2. Add migrations (Alembic) and Postgres wiring instead of in-memory defaults.
3. Add session builder + lane extraction modules and tests.
4. Wire scheduler runner to APScheduler/Celery jobs.
5. Add auth + secret encryption at rest for provider tokens.
