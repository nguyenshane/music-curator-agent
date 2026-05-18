from backend.config import Settings


def test_provider_enablement_flags():
    settings = Settings(
        app_env="development",
        log_level="INFO",
        database_url="sqlite+pysqlite:///:memory:",
        spotify_client_id="sid",
        spotify_client_secret="ssecret",
        lastfm_api_key=None,
        lastfm_user=None,
        tidal_client_id=None,
        tidal_client_secret=None,
        ytmusic_oauth_token="",
        musicbrainz_user_agent="ShaneMusicCurator/0.1 (you@example.com)",
    )

    assert settings.is_provider_enabled("spotify") is True
    assert settings.is_provider_enabled("lastfm") is False
    assert settings.is_provider_enabled("tidal") is False
    assert settings.is_provider_enabled("ytmusic") is False
    assert settings.is_provider_enabled("musicbrainz") is True
