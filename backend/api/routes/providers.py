"""Provider capability probes — operator diagnostics."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.adapters.spotify import SpotifyAdapter
from backend.adapters.spotify.audio_features import probe_audio_features_access

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("/spotify/capabilities")
def spotify_capabilities() -> dict[str, Any]:
    """Reports whether this Spotify app can fetch /audio-features.

    Spotify deprecated audio-features for apps registered after Nov 2024.
    If `audio_features.available` is False, the playlist generator will
    skip the audio-similarity term and rely on history + feedback signals
    only.
    """
    try:
        adapter = SpotifyAdapter()
        token = adapter._get_client_token()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Spotify client-credentials failed: {e}")
    return {"audio_features": probe_audio_features_access(token)}
