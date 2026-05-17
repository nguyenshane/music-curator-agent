from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, quote

import httpx

from backend.adapters.base import ListeningHistoryAdapter
from backend.adapters.types import ExternalTrackRef, ListenEvent
from backend.config import get_settings

logger = logging.getLogger(__name__)

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

# Scopes needed for full listening history access
SPOTIFY_SCOPES = [
    "user-top-read",           # /me/top/tracks, /me/top/artists
    "user-read-recently-played", # /me/player/recently-played
    "playlist-read-private",    # /me/playlists, /playlists/{id}/tracks
    "playlist-read-collaborative",
    "user-read-email",
]
SPOTIFY_SCOPES_STR = ",".join(SPOTIFY_SCOPES)


class SpotifyAdapter(ListeningHistoryAdapter):
    provider_name = "spotify"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client_token: str | None = None
        self._client_token_expires_at: datetime | None = None
        # Per-user OAuth state
        self._user_tokens: dict[str, dict[str, Any]] = {}  # user_id -> {access_token, refresh_token, expires_at}
        self._auth_codes: dict[str, str] = {}  # code_verifier -> auth_code (for PKCE)

    # ── Client Credentials (public data) ──────────────────────────────

    def _get_client_token(self) -> str:
        """Get access token via Client Credentials Flow (public data only)."""
        if self._client_token and self._client_token_expires_at and datetime.now(timezone.utc) < self._client_token_expires_at:
            return self._client_token

        client_id = self._settings.spotify_client_id
        client_secret = self._settings.spotify_client_secret

        if not client_id or not client_secret:
            raise RuntimeError("Spotify adapter requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET")

        with httpx.Client() as client:
            response = client.post(
                SPOTIFY_TOKEN_URL,
                auth=(client_id, client_secret),
                data={"grant_type": "client_credentials"},
                timeout=10.0,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            self._client_token = data["access_token"]
            self._client_token_expires_at = datetime.now(timezone.utc).replace(tzinfo=timezone.utc) + timedelta(
                seconds=data.get("expires_in", 3600)
            )
        return self._client_token

    # ── OAuth2 Authorization Code Flow (with PKCE) ────────────────────

    def get_authorization_url(self, user_id: str, *, redirect_uri: str | None = None) -> str:
        """Generate Spotify OAuth2 authorization URL with PKCE.

        User must visit this URL to grant permission. After authorization,
        Spotify redirects to redirect_uri with ?code=AUTH_CODE&state=STATE.
        Call exchange_code_for_tokens() with that auth_code.
        """
        client_id = self._settings.spotify_client_id
        if not client_id:
            raise RuntimeError("SPOTIFY_CLIENT_ID is required for OAuth2")

        # PKCE: generate code verifier and challenge
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = self._pkce_code_challenge(code_verifier)

        params = {
            "response_type": "code",
            "client_id": client_id,
            "scope": SPOTIFY_SCOPES_STR,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": user_id,  # used to identify the user
            "prompt": "consent",  # force consent screen to get refresh token
        }

        return f"{SPOTIFY_AUTH_URL}?{urlencode(params)}"

    @staticmethod
    def _pkce_code_challenge(verifier: str) -> str:
        """Generate PKCE code challenge from verifier (S256 method)."""
        import hashlib
        import base64

        sha256_hash = hashlib.sha256(verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(sha256_hash).decode("ascii").rstrip("=")
        return code_challenge

    def exchange_code_for_tokens(
        self,
        auth_code: str,
        *,
        redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        """Exchange authorization code for access + refresh tokens.

        Call this after the user authorizes and is redirected back with ?code=...
        """
        client_id = self._settings.spotify_client_id
        client_secret = self._settings.spotify_client_secret

        if not client_id or not client_secret:
            raise RuntimeError("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET are required")

        with httpx.Client() as client:
            response = client.post(
                SPOTIFY_TOKEN_URL,
                auth=(client_id, client_secret),
                data={
                    "grant_type": "authorization_code",
                    "code": auth_code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": self._auth_codes.get(auth_code, ""),
                },
                timeout=10.0,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        # Store tokens keyed by state (user_id)
        state = data.get("state", "default")
        self._user_tokens[state] = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "expires_at": datetime.now(timezone.utc).replace(tzinfo=timezone.utc)
                         + timedelta(seconds=data.get("expires_in", 3600)),
        }
        return self._user_tokens[state]

    def _get_user_token(self, user_id: str) -> str:
        """Get valid access token for user. Auto-refreshes if expired."""
        tokens = self._user_tokens.get(user_id)
        if tokens and tokens["expires_at"] and datetime.now(timezone.utc) < tokens["expires_at"]:
            return tokens["access_token"]

        if not tokens or not tokens.get("refresh_token"):
            raise RuntimeError(
                f"No OAuth2 tokens for user '{user_id}'. "
                "User must authorize first via get_authorization_url() then call exchange_code_for_tokens()."
            )

        # Refresh the token
        client_id = self._settings.spotify_client_id
        client_secret = self._settings.spotify_client_secret

        with httpx.Client() as client:
            response = client.post(
                SPOTIFY_TOKEN_URL,
                auth=(client_id, client_secret),
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                },
                timeout=10.0,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        self._user_tokens[user_id] = {
            "access_token": data["access_token"],
            "refresh_token": tokens["refresh_token"],  # refresh_token stays the same
            "expires_at": datetime.now(timezone.utc).replace(tzinfo=timezone.utc)
                         + timedelta(seconds=data.get("expires_in", 3600)),
        }
        return data["access_token"]

    # ── User-specific data endpoints (require OAuth2) ─────────────────

    def _fetch_user_top_tracks(self, user_id: str, *, limit: int = 50) -> list[ListenEvent]:
        """Fetch user's top tracks (requires OAuth2)."""
        token = self._get_user_token(user_id)
        endpoint = f"/me/top/tracks?limit={limit}&time_range=short_term"

        with httpx.Client() as client:
            response = client.get(
                f"{SPOTIFY_API_BASE}{endpoint}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        events: list[ListenEvent] = []
        now = datetime.now(timezone.utc)
        for i, item in enumerate(data.get("items", [])):
            track = item["track"]
            played_at = now.replace(hour=10 - i % 10, minute=(i * 5) % 60, tzinfo=timezone.utc)
            event = self._parse_track_to_event(track, user_id, played_at)
            if event:
                events.append(event)

        logger.info(f"SpotifyAdapter: fetched {len(events)} top tracks for user={user_id}")
        return events

    def _fetch_user_recently_played(self, user_id: str, *, limit: int = 50) -> list[ListenEvent]:
        """Fetch user's recently played tracks (requires OAuth2)."""
        token = self._get_user_token(user_id)
        endpoint = f"/me/player/recently-played?limit={limit}"

        with httpx.Client() as client:
            response = client.get(
                f"{SPOTIFY_API_BASE}{endpoint}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        events: list[ListenEvent] = []
        for item in data.get("items", []):
            track = item.get("track", {})
            played_at = datetime.fromisoformat(item["played_at"].replace("Z", "+00:00"))
            event = self._parse_track_to_event(track, user_id, played_at)
            if event:
                events.append(event)

        logger.info(f"SpotifyAdapter: fetched {len(events)} recently played for user={user_id}")
        return events

    def _fetch_user_playlists(self, user_id: str, *, limit: int = 20) -> list[ListenEvent]:
        """Fetch tracks from user's playlists (requires OAuth2)."""
        token = self._get_user_token(user_id)
        endpoint = f"/me/playlists?limit={limit}"

        with httpx.Client() as client:
            response = client.get(
                f"{SPOTIFY_API_BASE}{endpoint}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            response.raise_for_status()
            playlists_data: dict[str, Any] = response.json()

        events: list[ListenEvent] = []
        now = datetime.now(timezone.utc)
        for playlist in playlists_data.get("items", []):
            playlist_id = playlist["id"]
            tracks_endpoint = f"/playlists/{playlist_id}/tracks?limit=10"

            with httpx.Client() as client2:
                tracks_resp = client2.get(
                    f"{SPOTIFY_API_BASE}{tracks_endpoint}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10.0,
                )
                tracks_resp.raise_for_status()
                tracks_data: dict[str, Any] = tracks_resp.json()

            for i, track_item in enumerate(tracks_data.get("items", [])):
                track = track_item.get("track", {})
                if not track or track.get("id") == "":
                    continue
                played_at = now.replace(hour=14, minute=i, tzinfo=timezone.utc)
                event = self._parse_track_to_event(track, user_id, played_at)
                if event:
                    events.append(event)

        logger.info(f"SpotifyAdapter: fetched {len(events)} tracks from user playlists for user={user_id}")
        return events

    # ── Shared helpers ────────────────────────────────────────────────

    def _parse_track_to_event(
        self,
        track: dict[str, Any],
        user_id: str,
        played_at: datetime,
    ) -> ListenEvent | None:
        """Parse a Spotify track dict into a ListenEvent."""
        if not track or track.get("id") == "" or track.get("id") is None:
            return None

        artists = track.get("artists", [])
        primary_artist = artists[0]["name"] if artists else "Unknown"
        isrc = None
        for key, value in track.get("external_ids", {}).items():
            if key == "isrc":
                isrc = value
                break

        return ListenEvent(
            provider=self.provider_name,
            user_id=user_id,
            played_at=played_at,
            track=ExternalTrackRef(
                provider=self.provider_name,
                track_id=track["id"],
                title=track["name"],
                artist=primary_artist,
                album=track.get("album", {}).get("name"),
                isrc=isrc,
                duration_ms=track.get("duration_ms"),
            ),
        )

    def _fetch_search_tracks(self, user_id: str, *, limit: int = 50) -> list[ListenEvent]:
        """Fallback: fetch tracks via Spotify search (works with client credentials)."""
        token = self._get_client_token()
        query = "top hits 2026"
        encoded_query = quote(query, safe="")
        endpoint = f"/search?q={encoded_query}&type=track&limit={limit}"

        with httpx.Client() as client:
            response = client.get(
                f"{SPOTIFY_API_BASE}{endpoint}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        events: list[ListenEvent] = []
        now = datetime.now(timezone.utc)
        for i, item in enumerate(data.get("tracks", {}).get("items", [])):
            played_at = now.replace(hour=10 - i % 10, minute=(i * 5) % 60, tzinfo=timezone.utc)
            event = self._parse_track_to_event(item, user_id, played_at)
            if event:
                events.append(event)

        return events

    def _fetch_top_tracks(self, user_id: str, *, limit: int = 50) -> list[ListenEvent]:
        """Fetch user top tracks if OAuth2 available, else fallback to search."""
        if user_id in self._user_tokens:
            try:
                return self._fetch_user_top_tracks(user_id, limit=limit)
            except RuntimeError:
                pass  # Fall through to search
        return self._fetch_search_tracks(user_id, limit=limit)

    def _fetch_recent_playlists(self, user_id: str, *, limit: int = 20) -> list[ListenEvent]:
        """Fetch user playlists if OAuth2 available, else fallback to public playlists."""
        if user_id in self._user_tokens:
            try:
                return self._fetch_user_playlists(user_id, limit=limit)
            except RuntimeError:
                return []
        return []

    # ── Public API ────────────────────────────────────────────────────

    def fetch_listens(self, *, user_id: str, since: datetime | None = None) -> list[ListenEvent]:
        """Fetch listening history from Spotify.

        Priority order:
        1. If OAuth2 tokens available: user's recently played + top tracks + playlists
        2. Fallback: public data via search (no user-specific data)
        """
        try:
            all_events: list[ListenEvent] = []

            # Try user-specific data first
            if user_id in self._user_tokens:
                try:
                    recently_played = self._fetch_user_recently_played(user_id)
                    all_events.extend(recently_played)
                except Exception as e:
                    logger.warning(f"SpotifyAdapter: recently played failed for {user_id}: {e}")

                try:
                    top_tracks = self._fetch_user_top_tracks(user_id)
                    all_events.extend(top_tracks)
                except Exception as e:
                    logger.warning(f"SpotifyAdapter: top tracks failed for {user_id}: {e}")

                try:
                    playlists = self._fetch_user_playlists(user_id)
                    all_events.extend(playlists)
                except Exception as e:
                    logger.warning(f"SpotifyAdapter: playlists failed for {user_id}: {e}")

            # Always try public fallback
            top_events = self._fetch_top_tracks(user_id)
            all_events.extend(top_events)

            # Deduplicate by (provider, provider_track_id)
            seen: set[str] = set()
            deduped: list[ListenEvent] = []
            for event in all_events:
                key = f"{event.provider}:{event.track.track_id}"
                if key not in seen:
                    seen.add(key)
                    deduped.append(event)

            if since:
                deduped = [e for e in deduped if e.played_at >= since]

            logger.info(f"SpotifyAdapter: fetched {len(deduped)} unique listens for user={user_id}")
            return deduped

        except httpx.HTTPStatusError as e:
            logger.error(f"SpotifyAdapter: HTTP error {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"SpotifyAdapter: unexpected error - {e}")
            raise
