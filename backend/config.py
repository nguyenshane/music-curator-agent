from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

ProviderName = Literal["spotify", "lastfm", "tidal", "ytmusic", "musicbrainz"]


@dataclass(frozen=True)
class Settings:
    app_env: str
    log_level: str
    database_url: str
    spotify_client_id: str | None
    spotify_client_secret: str | None
    lastfm_api_key: str | None
    lastfm_user: str | None
    tidal_client_id: str | None
    tidal_client_secret: str | None
    ytmusic_oauth_token: str | None
    musicbrainz_user_agent: str | None

    def is_provider_enabled(self, provider: ProviderName) -> bool:
        if provider == "spotify":
            return bool(self.spotify_client_id and self.spotify_client_secret)
        if provider == "lastfm":
            return bool(self.lastfm_api_key)
        if provider == "tidal":
            return bool(self.tidal_client_id and self.tidal_client_secret)
        if provider == "ytmusic":
            return bool(self.ytmusic_oauth_token)
        if provider == "musicbrainz":
            return bool(self.musicbrainz_user_agent)
        return False


def get_settings() -> Settings:
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        database_url=os.getenv("DATABASE_URL", "sqlite+pysqlite:///:memory:"),
        spotify_client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        spotify_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        lastfm_api_key=os.getenv("LASTFM_API_KEY"),
        lastfm_user=os.getenv("LASTFM_USER"),
        tidal_client_id=os.getenv("TIDAL_CLIENT_ID"),
        tidal_client_secret=os.getenv("TIDAL_CLIENT_SECRET"),
        ytmusic_oauth_token=os.getenv("YTMUSIC_OAUTH_TOKEN"),
        musicbrainz_user_agent=os.getenv("MUSICBRAINZ_USER_AGENT"),
    )
