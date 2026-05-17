from __future__ import annotations

from datetime import datetime

from backend.adapters.base import ListeningHistoryAdapter
from backend.adapters.types import ListenEvent


class MusicBrainzAdapter(ListeningHistoryAdapter):
    provider_name = "musicbrainz"

    def fetch_listens(self, *, user_id: str, since: datetime | None = None) -> list[ListenEvent]:
        # MusicBrainz does not directly expose personal listening history.
        # Keep adapter concrete for provider registry compatibility.
        return []
