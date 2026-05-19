"""LastFMAdapter normalization + pagination tests with a mocked transport."""
from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from backend.adapters.lastfm import LastFMAdapter


@pytest.fixture(autouse=True)
def _lastfm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scope env vars to this module so they don't leak to other tests."""
    monkeypatch.setenv("LASTFM_API_KEY", "test-key")
    monkeypatch.setenv("LASTFM_USER", "shane_handle")


def _page(tracks: list[dict], page: int, total_pages: int) -> dict:
    return {
        "recenttracks": {
            "@attr": {"page": str(page), "totalPages": str(total_pages)},
            "track": tracks,
        }
    }


def _track(name: str, artist: str, uts: int, mbid: str = "") -> dict:
    return {
        "name": name,
        "mbid": mbid,
        "artist": {"#text": artist, "mbid": ""},
        "date": {"uts": str(uts), "#text": "x"},
    }


def test_paginates_and_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    page1 = _page([_track("A", "X", 1_700_000_010, "m1"), _track("B", "Y", 1_700_000_020)], 1, 2)
    page2 = _page([_track("C", "Z", 1_700_000_030)], 2, 2)

    seen_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        qs = parse_qs(request.url.query.decode("utf-8"))
        page = int(qs["page"][0])
        seen_pages.append(page)
        assert qs["user"] == ["shane_handle"], "must use LASTFM_USER from settings"
        body = page1 if page == 1 else page2
        return httpx.Response(200, content=json.dumps(body))

    transport = httpx.MockTransport(handler)
    adapter = LastFMAdapter(client=httpx.Client(transport=transport))

    events = adapter.fetch_listens(user_id="shane")

    assert seen_pages == [1, 2]
    assert [e.track.title for e in events] == ["A", "B", "C"]
    # mbid-bearing track uses mbid as track_id; others fall back to artist::title.
    assert events[0].track.track_id == "m1"
    assert events[1].track.track_id == "y::b"


def test_skips_now_playing_items() -> None:
    payload = _page([
        {"name": "Live", "artist": {"#text": "X"}, "@attr": {"nowplaying": "true"}},  # no date
        _track("Past", "X", 1_700_000_000),
    ], 1, 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload))

    adapter = LastFMAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    events = adapter.fetch_listens(user_id="shane")
    assert len(events) == 1
    assert events[0].track.title == "Past"
