"""Last.fm recent-tracks adapter.

Paginates `user.getrecenttracks`, retries transient failures with exponential
backoff, and resolves the Last.fm username from settings (falling back to the
internal user_id if `LASTFM_USER` is not configured).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.adapters.base import ListeningHistoryAdapter
from backend.adapters.types import ExternalTrackRef, ListenEvent
from backend.config import get_settings

LASTFM_API_BASE = "https://ws.audioscrobbler.com/2.0/"
LASTFM_RECENT_TRACKS_PAGE_SIZE = 200  # API max
LASTFM_MAX_PAGES = 25  # safety cap: 5000 tracks per fetch
LASTFM_MAX_RETRIES = 3
LASTFM_BACKOFF_BASE_SEC = 0.5

logger = logging.getLogger(__name__)


class LastFMAdapter(ListeningHistoryAdapter):
    provider_name = "lastfm"

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._settings = get_settings()
        # Optional injected client for tests (e.g. with httpx.MockTransport).
        self._injected_client = client

    def _resolve_username(self, user_id: str) -> str:
        """Return the Last.fm username for this internal user.

        The internal `user_id` may be an opaque identifier (e.g. "shane") that
        doesn't match the user's Last.fm handle. Prefer `LASTFM_USER` from
        config; fall back to `user_id` only when not configured.
        """
        return self._settings.lastfm_user or user_id

    def _request_with_retry(self, client: httpx.Client, params: dict[str, Any]) -> dict[str, Any]:
        """GET with bounded exponential-backoff retry on 5xx and transport errors."""
        last_exc: Exception | None = None
        for attempt in range(LASTFM_MAX_RETRIES):
            try:
                response = client.get(LASTFM_API_BASE, params=params, timeout=10.0)
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"Last.fm 5xx: {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, httpx.TransportError) as e:
                last_exc = e
                if attempt == LASTFM_MAX_RETRIES - 1:
                    break
                sleep_s = LASTFM_BACKOFF_BASE_SEC * (2 ** attempt)
                logger.warning(
                    "LastFM transient error (attempt %d/%d): %s; retrying in %.2fs",
                    attempt + 1, LASTFM_MAX_RETRIES, e, sleep_s,
                )
                time.sleep(sleep_s)
        assert last_exc is not None
        raise last_exc

    def fetch_listens(self, *, user_id: str, since: datetime | None = None) -> list[ListenEvent]:
        api_key = self._settings.lastfm_api_key
        if not api_key:
            return []

        username = self._resolve_username(user_id)
        events: list[ListenEvent] = []

        client_ctx: Any = (
            _NullCtx(self._injected_client)
            if self._injected_client is not None
            else httpx.Client()
        )
        with client_ctx as client:
            for page in range(1, LASTFM_MAX_PAGES + 1):
                params: dict[str, Any] = {
                    "method": "user.getrecenttracks",
                    "user": username,
                    "api_key": api_key,
                    "format": "json",
                    "limit": LASTFM_RECENT_TRACKS_PAGE_SIZE,
                    "page": page,
                }
                if since:
                    params["from"] = int(since.timestamp())

                payload = self._request_with_retry(client, params)
                recent = payload.get("recenttracks", {})
                tracks = recent.get("track", [])
                if not tracks:
                    break

                for item in tracks:
                    # `@attr.nowplaying` items have no `date.uts` — skip them.
                    date_uts = (item.get("date") or {}).get("uts")
                    if not date_uts:
                        continue
                    played_at = datetime.fromtimestamp(int(date_uts), tz=timezone.utc)
                    artist_name = (item.get("artist") or {}).get("#text", "Unknown")
                    title = item.get("name", "")
                    mbid = item.get("mbid") or ""
                    track_id = mbid or f"{artist_name}::{title}".lower()
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
                            ),
                        )
                    )

                attr = recent.get("@attr", {})
                try:
                    total_pages = int(attr.get("totalPages", "1"))
                except ValueError:
                    total_pages = 1
                if page >= total_pages:
                    break

        logger.info("LastFMAdapter: fetched %d listens for %s", len(events), username)
        return events


class _NullCtx:
    """Trivial context manager that yields an injected client without closing it."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def __enter__(self) -> httpx.Client:
        return self._client

    def __exit__(self, *args: object) -> None:
        return None
