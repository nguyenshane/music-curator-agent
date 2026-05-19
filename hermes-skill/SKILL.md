# Hermes Skill

Hermes-side instructions for working with the Shane Music Curator Agent.

## Contents

- [TIDAL OAuth2 flow](#tidal-oauth2-flow) — connect a TIDAL account through the helper endpoints.
- Lane labeling and explainability — *to be added.*

---

## TIDAL OAuth2 flow

**Read this entire section before starting a flow.** TIDAL authorization codes
expire in **~60 seconds**, so every round trip after the user lands on the
callback page counts. If you stall, the code dies and the user has to log in
again.

### Endpoints

All endpoints are on the curator agent backend (default `http://localhost:8000`).

| Method | Path                        | Purpose                                           |
|--------|-----------------------------|---------------------------------------------------|
| GET    | `/auth/tidal/config`        | Live `client_id`, redirect_uri, scopes, endpoints |
| POST   | `/auth/tidal/authorize`     | Build authorize URL; returns `{authorize_url, state}` |
| POST   | `/auth/tidal/exchange`      | Exchange `code` + `state` for tokens              |
| POST   | `/auth/tidal/refresh`       | Force-refresh access token                        |
| GET    | `/auth/tidal/status`        | Whether tokens / PKCE state exist + TTL           |
| POST   | `/auth/tidal/reset`         | Drop PKCE + token state (recovery)                |

### Required flow (happy path)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Step 1.  GET /auth/tidal/config                                          │
│          - Confirms which client_id the adapter will use RIGHT NOW.      │
│          - Verify it matches the app registered at developer.tidal.com.  │
│          - If client_id_configured = false → stop, the env is wrong.     │
│                                                                          │
│ Step 2.  POST /auth/tidal/authorize  {user_id, redirect_uri?}            │
│          - Returns {authorize_url, state, redirect_uri, client_id}.      │
│          - Sanity-check: parse the URL, confirm its `client_id` query    │
│            param equals the `client_id` returned in step 1.              │
│          - Hand authorize_url to the user.                               │
│                                                                          │
│ Step 3.  (User logs into TIDAL and consents.)                            │
│          TIDAL redirects to the registered redirect_uri with             │
│          ?code=AUTH_CODE&state=STATE — START THE 60s CLOCK.              │
│                                                                          │
│ Step 4.  POST /auth/tidal/exchange  {user_id, code, state}               │
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
2. **Verify `redirect_uri` is registered.** The default value from
   `/config` must exactly match (scheme, host, port, path, trailing slash)
   what's registered on the developer.tidal.com app. Mismatches cause
   error 11102 on TIDAL's login page.
3. **Verify scopes are enabled on the app.** `/config` returns the scopes
   the URL will request. They must all be enabled on the developer app.

### Recovery / error handling

| Symptom | Diagnosis | Action |
|---|---|---|
| `/config` returns `client_id_configured: false` | Env not exported / settings snapshot stale | Export `TIDAL_CLIENT_ID`/`TIDAL_CLIENT_SECRET`, restart server, retry from step 1. Settings are read live, so a restart should not be needed for *fresh* exports — but it is the safest recovery. |
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

## Lane labeling and explainability

*To be added in a follow-up.*
