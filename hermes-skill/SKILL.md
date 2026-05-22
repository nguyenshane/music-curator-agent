# Hermes Skill

Hermes-side instructions for working with the Shane Music Curator Agent.

## Contents

- [TIDAL OAuth2 flow](#tidal-oauth2-flow) — connect a TIDAL account through the helper endpoints.
- [Playlist suggestions](#playlist-suggestions) — fetch and explain today's recommended playlist.
- [Feedback signals](#feedback-signals) — tell the system what the user liked or hated.
- [Provider capabilities](#provider-capabilities) — diagnose what a registered Spotify app can actually do.
- Lane labeling and explainability — *to be added.*

---

## TIDAL OAuth2 flow

**Read this entire section before starting a flow.** TIDAL authorization codes
expire in **~60 seconds**, so every round trip after the user lands on the
callback page counts. If you stall, the code dies and the user has to log in
again.

### Hard policy: redirect_uri is server-pinned

The redirect URI is fixed by the `TIDAL_REDIRECT_URI` environment variable
on the backend and **cannot be overridden by any caller**, including Hermes.
This eliminates the entire class of "wrong redirect_uri" failures that cost
a fresh 60-second window every time.

- The `/auth/tidal/authorize` and `/auth/tidal/exchange` request bodies
  **do not accept** a `redirect_uri` field. Sending one returns **HTTP 422**.
- The current value is exposed via `GET /auth/tidal/config` → `redirect_uri`.
  Hermes should read this and verify it matches what's registered on
  developer.tidal.com, but must never try to send a different one.
- If `TIDAL_REDIRECT_URI` is not configured, `/auth/tidal/authorize`
  returns **HTTP 400** with `TIDAL_REDIRECT_URI is not configured...` —
  ask the operator to set it and restart the server.

### Endpoints

All endpoints are on the curator agent backend (default `http://localhost:8000`).

| Method | Path                        | Purpose                                           |
|--------|-----------------------------|---------------------------------------------------|
| GET    | `/auth/tidal/config`        | Live `client_id`, redirect_uri, scopes, endpoints |
| POST   | `/auth/tidal/authorize`     | Body: `{user_id}` only. Returns `{authorize_url, state}` |
| POST   | `/auth/tidal/exchange`      | Body: `{user_id, code, state}` only. Exchanges code for tokens |
| POST   | `/auth/tidal/refresh`       | Force-refresh access token                        |
| GET    | `/auth/tidal/status`        | Whether tokens / PKCE state exist + TTL           |
| POST   | `/auth/tidal/reset`         | Drop PKCE + token state (recovery)                |

### Required flow (happy path)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Step 1.  GET /auth/tidal/config                                          │
│          - Confirms which client_id AND redirect_uri the adapter will    │
│            use right now. Both come from env; both are pinned.           │
│          - If client_id_configured = false OR redirect_uri_configured    │
│            = false → STOP. Ask the operator to set the env vars and      │
│            restart. Do not proceed.                                      │
│          - Verify the redirect_uri matches what's registered on the      │
│            developer.tidal.com app.                                      │
│                                                                          │
│ Step 2.  POST /auth/tidal/authorize  {user_id}                           │
│          - Body MUST contain only `user_id`. Do NOT include              │
│            `redirect_uri` — that will 422.                               │
│          - Returns {authorize_url, state, redirect_uri, client_id}.      │
│          - Sanity-check: parse authorize_url, confirm its `client_id`    │
│            and `redirect_uri` query params equal what /config returned.  │
│          - Hand authorize_url to the user.                               │
│                                                                          │
│ Step 3.  (User logs into TIDAL and consents.)                            │
│          TIDAL redirects to the pinned redirect_uri with                 │
│          ?code=AUTH_CODE&state=STATE — START THE 60s CLOCK.              │
│                                                                          │
│ Step 4.  POST /auth/tidal/exchange  {user_id, code, state}               │
│          - Body MUST contain only those three fields. No redirect_uri.   │
│          - Must run inside the 60s window.                               │
│          - Returns {ok: true, has_refresh_token, expires_at}.            │
│                                                                          │
│ Step 5.  GET /auth/tidal/status?user_id=...                              │
│          - Confirm has_tokens = true and expires_in_seconds is sensible. │
└──────────────────────────────────────────────────────────────────────────┘
```

### Pre-flight checks Hermes MUST do

Before issuing the authorize URL to the user:

1. **Verify `client_id` matches.** Call `GET /auth/tidal/config`, then call
   `POST /auth/tidal/authorize`. Parse the returned `authorize_url` and
   compare its `client_id` query param to the one from `/config`. If they
   differ, `/reset` and try again — the adapter view is inconsistent.
2. **Verify `redirect_uri` is registered.** Read `redirect_uri` from
   `/config`. That value must exactly match (scheme, host, port, path,
   trailing slash) what's registered on the developer.tidal.com app.
   Mismatches cause error 11102 on TIDAL's login page. You CANNOT change
   the URI via the API — only by updating `TIDAL_REDIRECT_URI` on the
   server and restarting.
3. **Verify scopes are enabled on the app.** `/config` returns the scopes
   the URL will request. They must all be enabled on the developer app.

### Recovery / error handling

| Symptom | Diagnosis | Action |
|---|---|---|
| `/config` returns `client_id_configured: false` | Env not exported / settings snapshot stale | Export `TIDAL_CLIENT_ID`/`TIDAL_CLIENT_SECRET`, restart server, retry from step 1. Settings are read live, so a restart should not be needed for *fresh* exports — but it is the safest recovery. |
| `/config` returns `redirect_uri_configured: false` | `TIDAL_REDIRECT_URI` not exported | Set `TIDAL_REDIRECT_URI` to the value registered on the developer app and restart. Do not proceed without it. |
| `/auth/tidal/authorize` returns **HTTP 422** with `redirect_uri` in the error | You sent a `redirect_uri` field in the body | Remove it. The body must contain only `user_id`. The URI is pinned by env. |
| `/auth/tidal/authorize` returns **HTTP 400** with `TIDAL_REDIRECT_URI is not configured` | Server has no env value set | Operator fix; cannot recover client-side. |
| `client_id` in `/authorize` URL ≠ `client_id` from `/config` | Stale singleton state from a previous bad run | `POST /auth/tidal/reset {user_id}`, then retry from step 2. |
| TIDAL login page shows **error 11102** | Either Authorization Code grant not enabled on the developer app, or `redirect_uri` mismatch, or scope not enabled | Confirm grant types / scopes on developer.tidal.com. New TIDAL developer apps default to Client Credentials only — Authorization Code requires manual approval ("Extended Access") from TIDAL. |
| TIDAL login page shows "Something went wrong" without an error code | Usually `redirect_uri` mismatch | Re-check the value in `/config` against the registered URI. |
| `/exchange` returns 502 with `invalid_grant` | Code already used, or older than 60s | The code is dead. Call `/reset` and restart from step 2. |
| `/exchange` returns 400 with `No PKCE state found` | The `/authorize` call was made against a *different* server process, or `/reset` was called between authorize and exchange | Restart from step 2 against the same server process. |
| `/exchange` returns 400 with `OAuth state mismatch` | The `state` echoed by TIDAL does not match the one stored at `/authorize` (CSRF or stale callback) | Restart from step 2; never reuse a `state` value across flows. |

### Status polling

`GET /auth/tidal/status?user_id=...` is safe to poll. Useful fields:

```json
{
  "user_id": "shane",
  "has_pkce_state": true,
  "has_tokens": true,
  "has_refresh_token": true,
  "expires_at": "2026-05-19T18:32:11+00:00",
  "expires_in_seconds": 3527,
  "redirect_uri": "https://nguyenshane.com/tidal/",
  "state": "..."
}
```

Refresh proactively when `expires_in_seconds` drops below ~120:

```
POST /auth/tidal/refresh  {"user_id": "shane"}
```

### What Hermes must NOT do

- **Do not pass `redirect_uri` in any request body.** The server-side
  policy is `extra=forbid` and will 422. The URI is pinned by env on
  purpose — overriding it was the source of repeated burned codes.
- **Do not hand-edit the authorize URL.** Use whatever `/auth/tidal/authorize`
  returns verbatim. The `code_verifier` is held server-side; mutating the
  URL will make `/exchange` fail with a PKCE mismatch you cannot recover from.
- **Do not call `/exchange` twice with the same code.** TIDAL invalidates the
  code on first use. Check `/status` if you suspect a duplicate callback.
- **Do not store `state` outside the server.** The server already tracks it
  per `user_id`. Re-deriving it client-side is unnecessary and error-prone.
- **Do not skip step 1 (`/config`).** That is the cheapest insurance against
  the entire class of "wrong client_id" / "wrong redirect_uri" bugs that
  cost a fresh 60s window each time they hit.

### Listening history note

TIDAL is wired as a **playlist sync target only**. `fetch_listens` returns
`[]` by design — the TIDAL Developer Platform does not expose playback
history to third-party apps. Pull listening history from Spotify or
Last.fm. Use TIDAL for outbound playlist writes.

---

## Playlist suggestions

Endpoints for fetching today's recommended playlist. The same generator
runs as the daily DAG `recommendation_scoring` stage, so a typical request
hits a persisted row (fast). On a cold cache the route regenerates inline.

| Method | Path                                    | Purpose                                        |
|--------|-----------------------------------------|------------------------------------------------|
| GET    | `/playlists/today?user_id=...&limit=20` | Most recently persisted playlist; auto-regenerate if none |
| GET    | `/playlists/today?user_id=...&regenerate=true` | Force regeneration before returning     |
| POST   | `/playlists/today` `{user_id, limit}`   | Force regeneration (idiomatic for writes)      |

### Response shape

```json
{
  "user_id": "shane",
  "generated_at": "2026-05-19T14:00:00+00:00",
  "context": "afternoon_weekday",
  "items": [
    {
      "track_id": 42,
      "title": "Focus Loop",
      "artist": "Code Ensemble",
      "source": "history",
      "score": 0.7321,
      "trace": {
        "taste_match": 0.85,
        "context_match": 0.72,
        "freshness": 0.43,
        "novelty": 0.60,
        "diversity": 1.0,
        "rejection_penalty": 0.0,
        "audio_similarity": 0.78
      }
    }
  ],
  "notes": null
}
```

`source` is either `"history"` (a track the user has heard before) or
`"discovery"` (a new track pulled from Last.fm `track.getSimilar`
seeded from the user's top artists). Mention this in explanations.

`trace` contains the exact feature inputs to the FR-7 scorer
(`taste*0.30 + context*0.20 + freshness*0.10 + novelty*0.10 + diversity*0.25 + audio_similarity*0.05 - rejection_penalty`).
Tracks lacking Spotify audio-features cache hold `audio_similarity: 0.5`
(neutral) and aren't penalised relative to ones that have it.
Hermes should use these to render the explanation rather than re-deriving
features client-side — the persisted row is the single source of truth.

### When to regenerate vs. read

- **Default to GET without `regenerate`.** The daily DAG already generates
  one playlist per user per run; serving the latest is correct.
- **Use `regenerate=true` (or POST)** only when the user explicitly asks
  for a fresh take, or when a meaningful context change happened (e.g.
  the user just gave a strong rejection signal and you want it reflected
  immediately).

### Empty result handling

If the user has no ingested listening history yet, `items` is `[]` and
`notes` is `"no listening history yet; ingest some listens first"`.
Surface this to the user as "I don't have enough data yet — connect
Spotify or Last.fm and let it ingest a few days." Do not silently retry.

### Lane labeling and explainability

*To be added in a follow-up.*

---

## Feedback signals

Recording feedback is how the system *learns*. Without it the
recommendation set drifts but never improves. Hermes should capture at
least 3–5 reactions per playlist to keep the model useful.

| Method | Path             | Body / Query                                          |
|--------|------------------|--------------------------------------------------------|
| POST   | `/feedback`      | `{user_id, track_id, signal}`                          |
| GET    | `/feedback/recent?user_id=...&limit=50` | List recent feedback events       |

`signal` values and their weights:

| Signal | Weight | Effect |
|--------|--------|--------|
| `love` | +2.0  | Strong positive boost. Future picks will lean into this artist/track. |
| `like` | +1.0  | Mild positive. |
| `skip` | -1.0  | Mild penalty. The track is dropped from recommendation candidates with exponential time decay (τ = 14 days). |
| `hate` | -2.5  | Strong penalty. Effectively removes the track for ~30+ days. |

Request body is `extra=forbid` — only `user_id`, `track_id`, `signal`.
Sending anything else returns **HTTP 422**. `track_id` is the **internal**
`Track.id`, not a provider id; pull it from a playlist item's `track_id`.

### When to call

- After surfacing a playlist, prompt the user for reactions and post
  each one. Don't batch — call once per signal. The endpoint is cheap.
- On a `hate`, the system will not show that track for weeks. Use it
  sparingly; prefer `skip` for "not now."
- `love` is the single strongest taste signal — far more useful than
  raw play count. Encourage the user to use it.

### Response shape

```json
{
  "ok": true,
  "id": 42,
  "user_id": "shane",
  "track_id": 17,
  "signal": "hate",
  "weight": -2.5
}
```

Errors:
- **404** if `track_id` doesn't exist.
- **422** if extra fields are sent or `signal` isn't one of the four.

---

## Provider capabilities

Operator diagnostics — confirm what a Spotify app is allowed to do
before depending on it.

| Method | Path                              | Purpose                                  |
|--------|-----------------------------------|------------------------------------------|
| GET    | `/providers/spotify/capabilities` | Reports whether `/audio-features` works  |

### Why this exists

Spotify deprecated `/audio-features` for apps registered after November
2024. The recommendation pipeline uses audio similarity as a *soft*
signal (5% weight) when available, but the rest of the score works fine
without it. The probe lets you tell at a glance whether your Spotify
app has access, so you don't chase phantom bugs in scoring quality.

### Response shape

```json
{
  "audio_features": {
    "available": true,
    "status_code": 200,
    "reason": "ok"
  }
}
```

When unavailable:

```json
{
  "audio_features": {
    "available": false,
    "status_code": 403,
    "reason": "Spotify denies /audio-features to this app. Likely registered after Nov 2024..."
  }
}
```

### Action

- If `available: true`, no action needed. Audio similarity flows into
  the score automatically.
- If `available: false`, surface this to the operator. They can either
  register an older Spotify app and rotate `SPOTIFY_CLIENT_ID` /
  `SPOTIFY_CLIENT_SECRET`, or accept that recommendations will rely on
  history + feedback signals only (still strong, just one fewer lever).
