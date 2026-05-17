from __future__ import annotations

from datetime import datetime

from backend.adapters.base import ListeningHistoryAdapter
from backend.adapters.types import ListenEvent
from backend.config import get_settings


class YTMusicAdapter(ListeningHistoryAdapter):
    provider_name = "ytmusic"

    def __init__(self) -> None:
        self._settings = get_settings()

    def fetch_listens(self, *, user_id: str, since: datetime | None = None) -> list[ListenEvent]:
        if not self._settings.ytmusic_oauth_token:
            return []
        # Real history endpoints are not exposed in official API; implementation will be via oauth-signed web client.
        return []
