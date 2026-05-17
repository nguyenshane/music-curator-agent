# Shane Music Curator Agent (Current Progress)

This repo is in **scaffold + dry-run** stage. You can already run a working API skeleton, validate deterministic scoring behavior, and dry-run orchestration safely before real provider integrations are turned on.

## What works now

### API endpoints (working)

- `GET /health` → confirms API is up.
- `GET /jobs/dag` → returns the current daily pipeline DAG.
- `POST /jobs/dry-run` → executes a non-destructive dry run across all DAG steps.
- `GET /recommendations/score` → returns deterministic sample recommendation score.

### Recommendation + feedback primitives (working)

- Deterministic FR-7 score formula (`taste/context/freshness/novelty/diversity - rejection_penalty`).
- Feedback decay helper and rejection penalty helper.

### Ingestion + dedup (working with mock adapter)

- Adapter contract exists (`ListeningHistoryAdapter`).
- `MockAdapter` provides deterministic sample listens.
- Ingestion service writes tracks/listens and skips duplicates deterministically.
- Canonical identity keying implemented (`ISRC` first, metadata fallback).

### Database scaffold (working)

- SQLAlchemy models exist for:
  - `Track`
  - `Listen`
  - `FeedbackEvent`
  - `Lane`
- Session factory scaffold exists for DB wiring.

### Provider onboarding behavior (working)

Provider credentials are **optional**.
If keys are not present, provider is treated as disabled.

- Spotify enabled only with both `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET`
- Last.fm enabled with `LASTFM_API_KEY`
- TIDAL enabled only with both `TIDAL_CLIENT_ID` + `TIDAL_CLIENT_SECRET`
- YTMusic enabled with `YTMUSIC_OAUTH_TOKEN`
- MusicBrainz enabled with `MUSICBRAINZ_USER_AGENT`

## What you can do immediately

1. Run Hermes-safe orchestration smoke checks via `/jobs/dry-run`.
2. Validate ranking math behavior from `/recommendations/score` and unit tests.
3. Test ingestion idempotency/dedup logic with the mock adapter.
4. Enable only one provider at a time by setting only that provider’s env vars.
5. Build next modules (sessionization, lane extraction, discovery) on top of this base.

## Quickstart

```bash
cp backend/.env.example backend/.env
set -a; source backend/.env; set +a
uvicorn backend.api.main:app --reload
```

## Tests

```bash
PYTHONPATH=. pytest backend/tests
```

> In restricted environments, dependency installation may block full test execution.

## Planning

- Next phase execution plan: `docs/next-phase-plan.md`
