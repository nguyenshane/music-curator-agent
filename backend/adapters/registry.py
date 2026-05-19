"""Provider adapter registry.

Auto-discovers enabled providers from config and provides a unified
interface to fetch listening history from all sources.
"""
from __future__ import annotations

import logging
from datetime import datetime

from backend.adapters.base import ListeningHistoryAdapter
from backend.adapters.lastfm import LastFMAdapter
from backend.adapters.musicbrainz import MusicBrainzAdapter
from backend.adapters.spotify import SpotifyAdapter
from backend.adapters.tidal import TidalAdapter, get_tidal_adapter
from backend.adapters.ytmusic import YTMusicAdapter
from backend.config import get_settings, ProviderName

logger = logging.getLogger(__name__)

# All available adapters keyed by provider name
_ALL_ADAPTERS: dict[str, type[ListeningHistoryAdapter]] = {
    "spotify": SpotifyAdapter,
    "lastfm": LastFMAdapter,
    "tidal": TidalAdapter,
    "ytmusic": YTMusicAdapter,
    "musicbrainz": MusicBrainzAdapter,
}


class ProviderRegistry:
    """Registry of enabled provider adapters."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._enabled: dict[str, ListeningHistoryAdapter] = {}
        self._discover()

    def _discover(self) -> None:
        """Auto-discover enabled providers from config."""
        for provider_name, adapter_class in _ALL_ADAPTERS.items():
            if self._settings.is_provider_enabled(provider_name):
                # Use the process singleton for TIDAL so OAuth PKCE state
                # is shared between auth routes and ingestion.
                if provider_name == "tidal":
                    adapter = get_tidal_adapter()
                else:
                    adapter = adapter_class()
                self._enabled[provider_name] = adapter
                logger.info(f"Provider registry: {provider_name} enabled")
            else:
                logger.debug(f"Provider registry: {provider_name} disabled (no credentials)")

    @property
    def enabled_providers(self) -> list[str]:
        return list(self._enabled.keys())

    def get_adapter(self, provider: ProviderName) -> ListeningHistoryAdapter | None:
        return self._enabled.get(provider)

    def fetch_all_listens(
        self,
        user_id: str,
        since: datetime | None = None,
    ) -> list[dict]:
        """Fetch listening history from all enabled providers.

        Returns list of dicts with keys:
        - provider: str
        - events: list[ListenEvent]
        - count: int
        """
        results = []
        for provider_name, adapter in self._enabled.items():
            try:
                events = adapter.fetch_listens(user_id=user_id, since=since)
                results.append({
                    "provider": provider_name,
                    "events": events,
                    "count": len(events),
                })
                logger.info(f"Registry: {provider_name} returned {len(events)} events")
            except Exception as e:
                logger.error(f"Registry: {provider_name} fetch failed: {e}")
                results.append({
                    "provider": provider_name,
                    "events": [],
                    "count": 0,
                    "error": str(e),
                })
        return results
