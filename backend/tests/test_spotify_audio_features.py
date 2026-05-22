"""Spotify audio-features probe + fetcher with a mocked transport."""
from __future__ import annotations

import json

import httpx

from backend.adapters.spotify.audio_features import (
    PROBE_TRACK_ID,
    fetch_audio_features,
    probe_audio_features_access,
)


def test_probe_returns_available_on_200():
    def handler(request: httpx.Request) -> httpx.Response:
        assert PROBE_TRACK_ID in request.url.path
        return httpx.Response(200, content=json.dumps({"id": PROBE_TRACK_ID, "energy": 0.5}))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = probe_audio_features_access("token", client=client)
    assert result["available"] is True
    assert result["status_code"] == 200


def test_probe_reports_403_with_reason():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=json.dumps({"error": {"message": "Forbidden"}}))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = probe_audio_features_access("token", client=client)
    assert result["available"] is False
    assert result["status_code"] == 403
    assert "deprecated" in result["reason"].lower() or "denies" in result["reason"].lower()


def test_fetch_returns_keyed_map_on_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps({
            "audio_features": [
                {"id": "abc", "energy": 0.7, "valence": 0.3},
                {"id": "def", "energy": 0.2, "valence": 0.9},
                None,  # Spotify returns null for unknown ids
            ]
        }))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = fetch_audio_features(["abc", "def", "ghi"], "token", client=client)
    assert set(result.keys()) == {"abc", "def"}
    assert result["abc"]["energy"] == 0.7


def test_fetch_returns_empty_on_403():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content="{}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = fetch_audio_features(["abc"], "token", client=client)
    assert result == {}
