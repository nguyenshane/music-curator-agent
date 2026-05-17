from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from backend.adapters.base import ListeningHistoryAdapter
from backend.adapters.types import ExternalTrackRef, ListenEvent
from backend.config import get_settings

LASTFM_API_BASE = "https://ws.audioscrobbler.com/2.0/"


class LastFMAdapter(ListeningHistoryAdapter):
    provider_name = "lastfm"

    def __init__(self) -> None:
        self._settings = get_settings()

    def fetch_listens(self, *, user_id: str, since: datetime | None = None) -> list[ListenEvent]:
        api_key = self._settings.lastfm_api_key
        if not api_key:
            return []

        params: dict[str, Any] = {
            "method": "user.getrecenttracks",
            "user": user_id,
            "api_key": api_key,
            "format": "json",
            "limit": 50,
        }
        if since:
            params["from"] = int(since.timestamp())

        with httpx.Client() as client:
            response = client.get(LASTFM_API_BASE, params=params, timeout=10.0)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()

        tracks = payload.get("recenttracks", {}).get("track", [])
        events: list[ListenEvent] = []
        for item in tracks:
            date_uts = item.get("date", {}).get("uts")
            if not date_uts:
                continue
            played_at = datetime.fromtimestamp(int(date_uts), tz=timezone.utc)
            events.append(
                ListenEvent(
                    provider=self.provider_name,
                    user_id=user_id,
                    played_at=played_at,
                    track=ExternalTrackRef(
                        provider=self.provider_name,
                        track_id=item.get("mbid") or f"{item.get('artist', {}).get('#text', 'unknown')}::{item.get('name', '')}",
                        title=item.get("name", ""),
                        artist=item.get("artist", {}).get("#text", "Unknown"),
                    ),
                )
            )

        return events
