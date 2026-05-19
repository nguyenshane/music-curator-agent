"""TIDAL OAuth2 helper endpoints for orchestrator agents (Hermes).

Designed so the full Authorization Code + PKCE flow can be driven through
a handful of fast, deterministic HTTP calls. Critical for TIDAL because the
authorization code expires in ~60 seconds — every round trip counts.

Endpoints
---------
GET    /auth/tidal/config        Current adapter view of TIDAL settings.
                                 Use BEFORE issuing an authorize URL to
                                 confirm the client_id, redirect_uri, and
                                 scopes match what's registered on
                                 developer.tidal.com. The client_id is
                                 returned in full so Hermes can verify the
                                 URL it later receives matches.

POST   /auth/tidal/authorize     Build a fresh authorization URL with a
                                 new PKCE verifier + state. Returns the
                                 URL plus the state value Hermes must echo
                                 back at exchange time.

POST   /auth/tidal/exchange      Exchange the auth code for tokens.
                                 Single round trip — should be invoked
                                 within the 60s code TTL.

POST   /auth/tidal/refresh       Refresh access token using the stored
                                 refresh token.

GET    /auth/tidal/status        Token state for a user_id: whether a
                                 token exists, expiry, scopes (best
                                 effort), and PKCE staging state. Safe to
                                 poll.

POST   /auth/tidal/reset         Drop in-memory PKCE + token state for a
                                 user (recovery from a wedged flow).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.adapters.tidal import (
    DEFAULT_REDIRECT_URI,
    TIDAL_SCOPES,
    TIDAL_AUTHORIZE_URL,
    TIDAL_TOKEN_URL,
    get_tidal_adapter,
)

router = APIRouter(prefix="/auth/tidal", tags=["auth-tidal"])


# ── Request / response models ────────────────────────────────────────


class AuthorizeBody(BaseModel):
    user_id: str = Field(..., description="Internal user identifier the PKCE state will be keyed under.")
    redirect_uri: str | None = Field(
        None,
        description="Override the configured default. Must match a redirect URI registered on developer.tidal.com.",
    )


class ExchangeBody(BaseModel):
    user_id: str
    code: str = Field(..., description="Authorization code from TIDAL's callback (?code=...).")
    state: str = Field(..., description="State value from TIDAL's callback (?state=...). Must match what /authorize returned.")
    redirect_uri: str | None = Field(
        None,
        description="Must match the redirect_uri used at /authorize. Defaults to whatever was stored there.",
    )


class RefreshBody(BaseModel):
    user_id: str


class ResetBody(BaseModel):
    user_id: str


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/config")
def config() -> dict[str, Any]:
    """Live view of the TIDAL OAuth config the adapter would use right now.

    Returns the actual `client_id` so Hermes can verify the authorize URL
    it later receives uses the *same* one — that's how we caught the bug
    where a stale snapshot of settings produced URLs with the wrong client.
    """
    adapter = get_tidal_adapter()
    settings = adapter._settings  # property → live read
    client_id = settings.tidal_client_id
    return {
        "client_id": client_id,
        "client_id_configured": bool(client_id),
        "client_secret_configured": bool(settings.tidal_client_secret),
        "default_redirect_uri": DEFAULT_REDIRECT_URI,
        "scopes": TIDAL_SCOPES,
        "authorize_endpoint": TIDAL_AUTHORIZE_URL,
        "token_endpoint": TIDAL_TOKEN_URL,
    }


@router.post("/authorize")
def authorize(body: AuthorizeBody) -> dict[str, Any]:
    """Generate a fresh authorize URL.

    Returns the URL plus the `state` value the caller must include in the
    subsequent `/exchange` request. The PKCE verifier is held in memory
    on the singleton adapter — callers do not need to (and cannot) pass
    it back.
    """
    adapter = get_tidal_adapter()
    try:
        url = adapter.get_authorization_url(body.user_id, redirect_uri=body.redirect_uri)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    staged = adapter._user_tokens.get(body.user_id, {})
    return {
        "authorize_url": url,
        "state": staged.get("state"),
        "redirect_uri": staged.get("redirect_uri"),
        "client_id": adapter._settings.tidal_client_id,
        "note": "TIDAL authorization codes expire in ~60s — call /auth/tidal/exchange immediately on callback.",
    }


@router.post("/exchange")
def exchange(body: ExchangeBody) -> dict[str, Any]:
    """Exchange the authorization code for access + refresh tokens.

    Idempotent on success: re-calling with the same already-consumed code
    will fail (TIDAL invalidates codes after use), so check `/auth/tidal/status`
    first if you suspect a duplicate callback.
    """
    adapter = get_tidal_adapter()
    try:
        tokens = adapter.exchange_code_for_tokens(
            body.user_id,
            body.code,
            state=body.state,
            redirect_uri=body.redirect_uri,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001 — httpx errors bubble here
        raise HTTPException(status_code=502, detail=f"TIDAL token endpoint rejected exchange: {e}")
    expires_at = tokens.get("expires_at")
    return {
        "ok": True,
        "user_id": body.user_id,
        "has_refresh_token": bool(tokens.get("refresh_token")),
        "expires_at": expires_at.isoformat() if isinstance(expires_at, datetime) else None,
    }


@router.post("/refresh")
def refresh(body: RefreshBody) -> dict[str, Any]:
    """Force a refresh of the stored access token. Useful if the watchdog
    sees an upcoming expiry and wants to pre-warm."""
    adapter = get_tidal_adapter()
    try:
        token = adapter._refresh_user_token(body.user_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No stored tokens for user_id={body.user_id}")
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"TIDAL refresh failed: {e}")
    return {
        "ok": True,
        "user_id": body.user_id,
        "expires_at": adapter._user_tokens[body.user_id]["expires_at"].isoformat(),
        "token_prefix": token[:8] + "…",
    }


@router.get("/status")
def status(user_id: str) -> dict[str, Any]:
    """Inspect what state the adapter currently holds for a user."""
    adapter = get_tidal_adapter()
    record = adapter._user_tokens.get(user_id)
    if record is None:
        return {"user_id": user_id, "has_pkce_state": False, "has_tokens": False}
    expires_at = record.get("expires_at")
    expires_in_s: int | None = None
    if isinstance(expires_at, datetime):
        expires_in_s = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    return {
        "user_id": user_id,
        "has_pkce_state": bool(record.get("code_verifier")),
        "has_tokens": bool(record.get("access_token")),
        "has_refresh_token": bool(record.get("refresh_token")),
        "expires_at": expires_at.isoformat() if isinstance(expires_at, datetime) else None,
        "expires_in_seconds": expires_in_s,
        "redirect_uri": record.get("redirect_uri"),
        "state": record.get("state"),
    }


@router.post("/reset")
def reset(body: ResetBody) -> dict[str, Any]:
    """Drop all in-memory PKCE + token state for a user. Recovery handle
    when a flow gets wedged (e.g. stale state from a previous run)."""
    adapter = get_tidal_adapter()
    existed = body.user_id in adapter._user_tokens
    adapter._user_tokens.pop(body.user_id, None)
    return {"ok": True, "user_id": body.user_id, "cleared": existed}
