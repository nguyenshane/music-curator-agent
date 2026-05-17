from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from backend.adapters.base import ListeningHistoryAdapter
from backend.adapters.types import ExternalTrackRef, ListenEvent
from backend.config import get_settings

TIDAL_AUTH_URL = "https://auth.tidal.com/v1/oauth2/token"
TIDAL_API_BASE = "https://api.tidal.com/v1"

logger = logging.getLogger(__name__)


class TidalAdapter(ListeningHistoryAdapter):
    provider_name = "tidal"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        # Per-user OAuth state
        self._user_tokens: dict[str, dict[str, Any]] = {}

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
                TIDAL_AUTH_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
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
        """Generate Tidal OAuth2 authorization URL with PKCE."""
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = self._pkce_challenge(code_verifier)

        state = secrets.token_urlsafe(32)

        params = {
            "response_type": "code",
            "client_id": self._settings.tidal_client_id,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "scope": "r_usr u_usr即便 u_sub",
        }
        if redirect_uri:
            params["redirect_uri"] = redirect_uri

        auth_url = f"{TIDAL_API_BASE}/oauth2/authorize?{urlencode(params)}"
        self._user_tokens[user_id] = {
            "code_verifier": code_verifier,
            "state": state,
            "redirect_uri": redirect_uri,
        }
        return auth_url

    @staticmethod
    def _pkce_challenge(verifier: str) -> str:
        import hashlib
        import base64
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        return challenge

    def exchange_code_for_tokens(self, user_id: str, auth_code: str, redirect_uri: str | None = None) -> dict[str, str]:
        """Exchange authorization code for user tokens."""
        state = self._user_tokens.get(user_id, {}).get("state")
        code_verifier = self._user_tokens.get(user_id, {}).get("code_verifier")

        if not code_verifier:
            raise RuntimeError("No PKCE state found. Call get_authorization_url first.")

        token_data = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": redirect_uri or self._user_tokens.get(user_id, {}).get("redirect_uri", ""),
            "client_id": self._settings.tidal_client_id,
            "code_verifier": code_verifier,
        }

        with httpx.Client() as client:
            response = client.post(
                TIDAL_AUTH_URL,
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

        # Try to use existing token
        if tokens.get("access_token") and tokens.get("expires_at"):
            if datetime.now(timezone.utc) < tokens["expires_at"] - timedelta(seconds=60):
                return tokens["access_token"]

        # Try to refresh
        if tokens.get("refresh_token"):
            return self._refresh_user_token(user_id)

        raise RuntimeError(f"No Tidal user tokens for user_id={user_id}. Complete OAuth flow first.")

    def _refresh_user_token(self, user_id: str) -> str:
        """Refresh user access token."""
        tokens = self._user_tokens[user_id]
        refresh_token = tokens["refresh_token"]

        with httpx.Client() as client:
            response = client.post(
                TIDAL_AUTH_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self._settings.tidal_client_id,
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

    # ── Listening History ──────────────────────────────────────────────

    def fetch_listens(self, *, user_id: str, since: datetime | None = None) -> list[ListenEvent]:
        """Fetch listening history from Tidal.

        Requires OAuth2 user tokens. Returns empty list if not authenticated.
        """
        # Check if user tokens exist
        if user_id not in self._user_tokens or not self._user_tokens[user_id].get("access_token"):
            logger.info(f"TidalAdapter: no user tokens for {user_id}, skipping")
            return []

        try:
            access_token = self._get_user_token(user_id)
        except RuntimeError as e:
            logger.warning(f"TidalAdapter: {e}")
            return []

        # Fetch recent listens
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        params: dict[str, Any] = {"limit": 200}
        if since:
            params["after"] = int(since.timestamp() * 1000)  # milliseconds

        events: list[ListenEvent] = []
        with httpx.Client() as client:
            response = client.get(
                f"{TIDAL_API_BASE}/users/v1/me/listens",
                headers=headers,
                params=params,
                timeout=10.0,
            )

            if response.status_code == 401:
                logger.warning("TidalAdapter: token expired, try refreshing")
                return []

            response.raise_for_status()
            payload: dict[str, Any] = response.json()

        listens = payload.get("data", [])
        for item in listens:
            played_at_str = item.get("playedAt") or item.get("timestamp")
            if not played_at_str:
                continue

            # Parse timestamp (could be int ms or ISO string)
            if isinstance(played_at_str, (int, float)):
                played_at = datetime.fromtimestamp(played_at_str / 1000, tz=timezone.utc)
            else:
                played_at = datetime.fromisoformat(str(played_at_str).replace("Z", "+00:00"))

            # Get track info
            track_data = item.get("track", {})
            if not track_data:
                # Maybe it's a different structure
                track_data = item

            artist_name = ""
            artists = track_data.get("artists", [])
            if artists:
                artist_name = artists[0].get("name", "Unknown") if isinstance(artists[0], dict) else str(artists[0])

            title = track_data.get("title", track_data.get("name", "Unknown"))
            track_id = str(track_data.get("id", ""))
            isrc = track_data.get("isrc", "")

            events.append(
                ListenEvent(
                    provider=self.provider_name,
                    user_id=user_id,
                    played_at=played_at,
                    track=ExternalTrackRef(
                        provider=self.provider_name,
                        track_id=track_id,
                        title=title,
                        artist=artist_name,
                        isrc=isrc,
                    ),
                )
            )

        logger.info(f"TidalAdapter: fetched {len(events)} listens for {user_id}")
        return events
