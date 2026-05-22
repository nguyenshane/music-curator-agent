"""Spotify Audio Features fetcher + capability probe.

Spotify deprecated the `/audio-features` endpoints for **new** apps in
late 2024. Existing apps may still have access; new ones get 403. This
module is built to degrade gracefully:

- `probe_audio_features_access` calls the endpoint with a well-known
  public track id and reports `available: True/False` so operators can
  tell at a glance whether their app has access.
- `fetch_audio_features` returns whatever it can. On 403 it returns an
  empty dict (callers should cache `{"_unavailable": true}` on affected
  tracks so we don't refetch every run).

Auth: client-credentials only — no user scope is needed for audio
features. We reuse the existing Spotify adapter for token issuance.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SPOTIFY_AUDIO_FEATURES_URL = "https://api.spotify.com/v1/audio-features"
# A public track that has existed forever — used as a known-good probe id.
# Daft Punk — "One More Time".
PROBE_TRACK_ID = "0DiWol3AO6WpXZgp0goxAV"


def probe_audio_features_access(client_token: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """Hit /audio-features/{id} once and report whether the app has access.

    Distinguishes the three cases we care about: 200 = available; 403 =
    explicitly denied (deprecated for new apps); anything else = unknown
    (transport error, 5xx, rate limit). The diagnostic is logged so
    operators can decide whether to register an older app or skip this
    feature entirely.
    """
    headers = {"Authorization": f"Bearer {client_token}"}
    owns = client is None
    if client is None:
        client = httpx.Client()
    try:
        resp = client.get(
            f"{SPOTIFY_AUDIO_FEATURES_URL}/{PROBE_TRACK_ID}",
            headers=headers,
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        return {"available": False, "status_code": None, "reason": str(e)}
    finally:
        if owns:
            client.close()
    if resp.status_code == 200:
        return {"available": True, "status_code": 200, "reason": "ok"}
    if resp.status_code == 403:
        return {
            "available": False,
            "status_code": 403,
            "reason": (
                "Spotify denies /audio-features to this app. Likely "
                "registered after Nov 2024 when Spotify deprecated audio "
                "features for new third-party apps. Register an older app "
                "or rely on history/feedback signals only."
            ),
        }
    return {"available": False, "status_code": resp.status_code, "reason": resp.text[:200]}


def fetch_audio_features(
    spotify_track_ids: list[str],
    client_token: str,
    *,
    client: httpx.Client | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch audio features for up to 100 Spotify track ids at a time.

    Returns a dict mapping `spotify_track_id -> features dict`. Missing
    entries mean Spotify returned null for that id (track not on
    Spotify, deleted, etc). On a 403 the whole call returns {} and the
    caller should cache `{"_unavailable": true}` on the affected Tracks.
    """
    if not spotify_track_ids:
        return {}
    out: dict[str, dict[str, Any]] = {}
    headers = {"Authorization": f"Bearer {client_token}"}
    owns = client is None
    if client is None:
        client = httpx.Client()
    try:
        for chunk_start in range(0, len(spotify_track_ids), 100):
            chunk = spotify_track_ids[chunk_start : chunk_start + 100]
            try:
                resp = client.get(
                    SPOTIFY_AUDIO_FEATURES_URL,
                    params={"ids": ",".join(chunk)},
                    headers=headers,
                    timeout=10.0,
                )
            except httpx.HTTPError as e:
                logger.warning("Spotify audio-features transport error: %s", e)
                continue
            if resp.status_code == 403:
                logger.warning("Spotify denied /audio-features (403); skipping further fetches")
                return out
            if resp.status_code != 200:
                logger.warning(
                    "Spotify audio-features unexpected status %d: %s",
                    resp.status_code, resp.text[:200],
                )
                continue
            payload = resp.json() or {}
            for entry in payload.get("audio_features") or []:
                if not entry:
                    continue
                tid = entry.get("id")
                if tid:
                    out[tid] = entry
    finally:
        if owns:
            client.close()
    return out
