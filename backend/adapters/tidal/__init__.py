from __future__ import annotations

from datetime import datetime

from backend.adapters.base import ListeningHistoryAdapter
from backend.adapters.types import ListenEvent
from backend.config import get_settings


class TidalAdapter(ListeningHistoryAdapter):
    provider_name = "tidal"

    def __init__(self) -> None:
        self._settings = get_settings()

    def fetch_listens(self, *, user_id: str, since: datetime | None = None) -> list[ListenEvent]:
        # TIDAL integration requires OAuth user tokens; scaffolding returns no data until token exchange is wired.
        if not (self._settings.tidal_client_id and self._settings.tidal_client_secret):
            return []
        return []
