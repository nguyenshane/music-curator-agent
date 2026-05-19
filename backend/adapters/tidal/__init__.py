from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from backend.adapters.base import ListeningHistoryAdapter
from backend.adapters.types import ListenEvent
from backend.config import get_settings

# TIDAL Developer Platform OAuth2 + API surface.
# Apps are registered at https://developer.tidal.com.
TIDAL_AUTHORIZE_URL = "https://login.tidal.com/authorize"
TIDAL_TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"
TIDAL_API_BASE = "https://openapi.tidal.com/v2"

# Scopes for the Developer Platform. Note: the legacy `r_usr`/`w_usr` scopes
# are NOT valid here. The Developer API also does NOT expose listening
# history — TIDAL is wired as a playlist sync target only.
TIDAL_SCOPES = [
    "user.read",
    "playlists.read",
    "playlists.write",
    "collection.read",
]
TIDAL_SCOPES_STR = " ".join(TIDAL_SCOPES)

DEFAULT_REDIRECT_URI = "https://nguyenshane.com/tidal/"

logger = logging.getLogger(__name__)


class TidalAdapter(ListeningHistoryAdapter):
    provider_name = "tidal"

    def __init__(self) -> None:
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        # Per-user OAuth state and tokens. Lives on the instance, so the
        # adapter must be process-singleton (see `get_tidal_adapter()`) for
        # PKCE state to survive between authorize and exchange across
        # separate API requests.
        self._user_tokens: dict[str, dict[str, Any]] = {}

    @property
    def _settings(self):  # noqa: ANN202 — Settings is a frozen dataclass
        """Read settings live so a runtime env change (e.g. .env reload) is
        picked up without rebuilding the adapter. Avoids the stale-client_id
        bug where the adapter was instantiated before TIDAL_CLIENT_ID/SECRET
        were exported.
        """
        return get_settings()

    # ── Client Credentials (public metadata) ──────────────────────────

    def _get_client_token(self) -> str:
        """Get access token via Client Credentials Flow."""
        if self._access_token and self._token_expires_at:
            if datetime.now(timezone.utc) < self._token_expires_at - timedelta(seconds=60):
                return self._access_token

        client_id = self._settings.tidal_client_id
        client_secret = self._settings.tidal_client_secret

        if not client_id or not client_secret:
            raise RuntimeError("Tidal adapter requires TIDAL_CLIENT_ID and TIDAL_CLIENT_SECRET")

        with httpx.Client() as client:
            response = client.post(
                TIDAL_TOKEN_URL,
                auth=(client_id, client_secret),
                data={"grant_type": "client_credentials"},
                timeout=10.0,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            self._access_token = data["access_token"]
            self._token_expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=data.get("expires_in", 3600)
            )
        return self._access_token

    # ── OAuth2 Authorization Code Flow (with PKCE) ────────────────────

    def get_authorization_url(self, user_id: str, *, redirect_uri: str | None = None) -> str:
        """Generate Tidal OAuth2 authorization URL with PKCE.

        `redirect_uri` must exactly match a callback registered on the
        developer.tidal.com app for this client_id.
        """
        client_id = self._settings.tidal_client_id
        if not client_id:
            raise RuntimeError("TIDAL_CLIENT_ID is required for OAuth2")

        effective_redirect_uri = redirect_uri or DEFAULT_REDIRECT_URI

        code_verifier = secrets.token_urlsafe(64)
        code_challenge = self._pkce_challenge(code_verifier)
        state = secrets.token_urlsafe(32)

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": effective_redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "scope": TIDAL_SCOPES_STR,
        }

        auth_url = f"{TIDAL_AUTHORIZE_URL}?{urlencode(params)}"
        self._user_tokens[user_id] = {
            "code_verifier": code_verifier,
            "state": state,
            "redirect_uri": effective_redirect_uri,
        }
        return auth_url

    @staticmethod
    def _pkce_challenge(verifier: str) -> str:
        import hashlib
        import base64
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        return challenge

    def exchange_code_for_tokens(
        self,
        user_id: str,
        auth_code: str,
        *,
        state: str | None = None,
        redirect_uri: str | None = None,
    ) -> dict[str, str]:
        """Exchange authorization code for user tokens."""
        client_id = self._settings.tidal_client_id
        client_secret = self._settings.tidal_client_secret
        if not client_id or not client_secret:
            raise RuntimeError("TIDAL_CLIENT_ID and TIDAL_CLIENT_SECRET are required")

        expected_state = self._user_tokens.get(user_id, {}).get("state")
        code_verifier = self._user_tokens.get(user_id, {}).get("code_verifier")
        stored_redirect_uri = self._user_tokens.get(user_id, {}).get("redirect_uri")

        if not code_verifier:
            raise RuntimeError("No PKCE state found. Call get_authorization_url first.")
        if expected_state and state and state != expected_state:
            raise RuntimeError("OAuth state mismatch for Tidal callback")

        effective_redirect_uri = redirect_uri or stored_redirect_uri
        if not effective_redirect_uri:
            raise RuntimeError("redirect_uri is required to exchange Tidal authorization code")

        token_data = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": effective_redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
        }

        with httpx.Client() as client:
            response = client.post(
                TIDAL_TOKEN_URL,
                auth=(client_id, client_secret),
                data=token_data,
                timeout=10.0,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()

            self._user_tokens[user_id] = {
                **self._user_tokens.get(user_id, {}),
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token"),
                "expires_at": datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600)),
            }
            return self._user_tokens[user_id]

    def _get_user_token(self, user_id: str) -> str:
        """Get or refresh user access token."""
        tokens = self._user_tokens.get(user_id, {})

        if tokens.get("access_token") and tokens.get("expires_at"):
            if datetime.now(timezone.utc) < tokens["expires_at"] - timedelta(seconds=60):
                return tokens["access_token"]

        if tokens.get("refresh_token"):
            return self._refresh_user_token(user_id)

        raise RuntimeError(f"No Tidal user tokens for user_id={user_id}. Complete OAuth flow first.")

    def _refresh_user_token(self, user_id: str) -> str:
        """Refresh user access token."""
        client_id = self._settings.tidal_client_id
        client_secret = self._settings.tidal_client_secret
        if not client_id or not client_secret:
            raise RuntimeError("TIDAL_CLIENT_ID and TIDAL_CLIENT_SECRET are required")

        tokens = self._user_tokens[user_id]
        refresh_token = tokens["refresh_token"]

        with httpx.Client() as client:
            response = client.post(
                TIDAL_TOKEN_URL,
                auth=(client_id, client_secret),
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                timeout=10.0,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()

            self._user_tokens[user_id]["access_token"] = data["access_token"]
            self._user_tokens[user_id]["expires_at"] = datetime.now(timezone.utc) + timedelta(
                seconds=data.get("expires_in", 3600)
            )
            if data.get("refresh_token"):
                self._user_tokens[user_id]["refresh_token"] = data["refresh_token"]

            return data["access_token"]

    # ── Listening History (intentionally not supported) ──────────────────

    def fetch_listens(self, *, user_id: str, since: datetime | None = None) -> list[ListenEvent]:
        """TIDAL Developer Platform does not expose playback history.

        TIDAL is wired as a playlist sync target only. Use Spotify or Last.fm
        for listening-history ingestion. Returns an empty list so the daily
        DAG can run without error.
        """
        logger.info(
            "TidalAdapter.fetch_listens: TIDAL Developer API does not expose "
            "listening history; returning [] (sync-target only)."
        )
        return []


# ── Process-singleton accessor ────────────────────────────────────────
# Required so PKCE state (`_user_tokens[user_id]`) created during
# `/auth/tidal/authorize` survives until `/auth/tidal/exchange` runs.
# Without this, each request would build a new adapter and lose the
# code_verifier, guaranteeing token-exchange failure inside the 1-minute
# TIDAL authorization-code TTL.

_singleton: TidalAdapter | None = None


def get_tidal_adapter() -> TidalAdapter:
    global _singleton
    if _singleton is None:
        _singleton = TidalAdapter()
    return _singleton
