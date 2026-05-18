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

TIDAL_TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"
TIDAL_AUTHORIZE_URL = "https://auth.tidal.com/v1/oauth2/authorize"
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
        """Generate Tidal OAuth2 authorization URL with PKCE."""
        client_id = self._settings.tidal_client_id
        if not client_id:
            raise RuntimeError("TIDAL_CLIENT_ID is required for OAuth2")
        if not redirect_uri:
            raise RuntimeError("redirect_uri is required for Tidal OAuth2 authorization")

        code_verifier = secrets.token_urlsafe(64)
        code_challenge = self._pkce_challenge(code_verifier)

        state = secrets.token_urlsafe(32)

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "scope": "r_usr w_usr",
        }

        auth_url = f"{TIDAL_AUTHORIZE_URL}?{urlencode(params)}"
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

    def exchange_code_for_tokens(self, user_id: str, auth_code: str, *, state: str | None = None, redirect_uri: str | None = None) -> dict[str, str]:
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

    # ── Listening History ──────────────────────────────────────────────

    TIDAL_PAGE_SIZE = 50
    TIDAL_MAX_PAGES = 20  # safety cap: up to 1000 recent plays per fetch

    def _get_session_info(self, client: httpx.Client, access_token: str) -> dict[str, Any]:
        """Resolve the numeric userId and countryCode for the current access token.

        TIDAL's API requires the actual userId (not "me") in resource paths and
        a countryCode query parameter on most user endpoints. `/v1/sessions`
        echoes both for the bearer token's user.
        """
        response = client.get(
            f"{TIDAL_API_BASE}/sessions",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _parse_played_at(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _normalize_listen(self, item: dict[str, Any], user_id: str) -> ListenEvent | None:
        # TIDAL collection items wrap the resource under "item"; some legacy
        # payloads inline the track directly. Support both.
        track_data = item.get("item") or item.get("track") or item
        if not isinstance(track_data, dict):
            return None

        played_at = self._parse_played_at(
            item.get("playedAt") or item.get("timestamp") or item.get("created")
        )
        if played_at is None:
            return None

        artists = track_data.get("artists") or []
        artist_name = ""
        if artists:
            first = artists[0]
            artist_name = first.get("name", "") if isinstance(first, dict) else str(first)

        track_id = track_data.get("id")
        if track_id is None:
            return None

        return ListenEvent(
            provider=self.provider_name,
            user_id=user_id,
            played_at=played_at,
            track=ExternalTrackRef(
                provider=self.provider_name,
                track_id=str(track_id),
                title=track_data.get("title") or track_data.get("name") or "Unknown",
                artist=artist_name,
                isrc=track_data.get("isrc", "") or "",
            ),
        )

    def fetch_listens(self, *, user_id: str, since: datetime | None = None) -> list[ListenEvent]:
        """Fetch recent plays from Tidal.

        Requires OAuth2 user tokens. Returns empty list if not authenticated.
        Paginates via limit/offset and stops once entries fall before `since`
        (TIDAL's recently-played endpoint does not support server-side time
        filtering, so we filter client-side and short-circuit).
        """
        if user_id not in self._user_tokens or not self._user_tokens[user_id].get("access_token"):
            logger.info("TidalAdapter: no user tokens for %s, skipping", user_id)
            return []

        try:
            access_token = self._get_user_token(user_id)
        except RuntimeError as e:
            logger.warning("TidalAdapter: %s", e)
            return []

        events: list[ListenEvent] = []
        with httpx.Client() as client:
            # Resolve numeric userId + countryCode for the token (cached on the
            # user record so subsequent fetches skip this round trip).
            cached = self._user_tokens[user_id]
            tidal_user_id = cached.get("tidal_user_id")
            country_code = cached.get("country_code")
            if not tidal_user_id or not country_code:
                try:
                    session = self._get_session_info(client, access_token)
                except httpx.HTTPStatusError as e:
                    logger.warning("TidalAdapter: /sessions failed: %s", e)
                    return []
                tidal_user_id = session.get("userId")
                country_code = session.get("countryCode")
                if not tidal_user_id or not country_code:
                    logger.warning("TidalAdapter: /sessions returned no userId/countryCode")
                    return []
                cached["tidal_user_id"] = tidal_user_id
                cached["country_code"] = country_code

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }

            offset = 0
            stop = False
            for _ in range(self.TIDAL_MAX_PAGES):
                params: dict[str, Any] = {
                    "countryCode": country_code,
                    "limit": self.TIDAL_PAGE_SIZE,
                    "offset": offset,
                }
                response = client.get(
                    f"{TIDAL_API_BASE}/users/{tidal_user_id}/recentlyPlayed",
                    headers=headers,
                    params=params,
                    timeout=10.0,
                )

                if response.status_code == 401:
                    logger.warning("TidalAdapter: 401 unauthorized; token may be invalid")
                    return events
                if response.status_code == 404:
                    logger.warning(
                        "TidalAdapter: recentlyPlayed endpoint not available for this account"
                    )
                    return events
                response.raise_for_status()
                payload: dict[str, Any] = response.json()

                # Modern collection shape: {items, limit, offset, totalNumberOfItems}.
                # Legacy shape: {data: [...]}. Support both.
                items = payload.get("items")
                if items is None:
                    items = payload.get("data", [])
                if not items:
                    break

                for raw in items:
                    listen = self._normalize_listen(raw, user_id)
                    if listen is None:
                        continue
                    if since is not None and listen.played_at < since:
                        # Results are ordered most-recent-first; older than the
                        # watermark means we can stop paginating entirely.
                        stop = True
                        break
                    events.append(listen)

                if stop or len(items) < self.TIDAL_PAGE_SIZE:
                    break
                offset += self.TIDAL_PAGE_SIZE

        logger.info("TidalAdapter: fetched %d listens for %s", len(events), user_id)
        return events
