"""Last.fm discovery: seed selection, ghost-track insertion, dedup."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import Base, Listen, Track


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LASTFM_API_KEY", "test-key")
    monkeypatch.setenv("LASTFM_USER", "shane")


def _engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _seed(db, now):
    track = Track(canonical_key="isrc:SEED", title="Anchor", artist="Seed Artist")
    db.add(track)
    db.flush()
    db.add(Listen(
        user_id="u1", provider="lastfm", provider_track_id="seed",
        played_at=now - timedelta(days=2), track_id=track.id,
    ))
    db.commit()
    return track


def _similar_response(tracks: list[tuple[str, str]]) -> dict:
    return {
        "similartracks": {
            "track": [
                {"name": title, "artist": {"name": artist}, "mbid": ""}
                for title, artist in tracks
            ]
        }
    }


def test_discovery_inserts_ghost_tracks_and_dedupes():
    from backend.recommendation.discovery import discover_via_lastfm

    engine = _engine()
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    now = datetime(2026, 5, 19, 14, 0, tzinfo=timezone.utc)
    with factory() as db:
        _seed(db, now)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(_similar_response([
                ("New Track 1", "New Artist A"),
                ("New Track 2", "New Artist B"),
                ("Anchor", "Seed Artist"),  # duplicate of seed; must be skipped
            ])),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    with factory() as db:
        new_ids = discover_via_lastfm(db, "u1", now=now, client=client)

    assert len(new_ids) == 2

    with factory() as db:
        tracks = db.scalars(select(Track)).all()
        artists = {t.artist for t in tracks}
    assert {"New Artist A", "New Artist B", "Seed Artist"} <= artists
    # No duplicate of the seed.
    seed_count = sum(1 for t in tracks if t.artist == "Seed Artist" and t.title == "Anchor")
    assert seed_count == 1


def test_discovery_idempotent_on_second_call():
    from backend.recommendation.discovery import discover_via_lastfm

    engine = _engine()
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    now = datetime(2026, 5, 19, 14, 0, tzinfo=timezone.utc)
    with factory() as db:
        _seed(db, now)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(_similar_response([
            ("New Track 1", "New Artist A"),
        ])))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with factory() as db:
        first = discover_via_lastfm(db, "u1", now=now, client=client)
    with factory() as db:
        second = discover_via_lastfm(db, "u1", now=now, client=client)

    assert len(first) == 1
    assert second == []  # nothing new on the second call


def test_discovery_with_no_seed_tracks_returns_empty():
    from backend.recommendation.discovery import discover_via_lastfm

    engine = _engine()
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    now = datetime(2026, 5, 19, 14, 0, tzinfo=timezone.utc)

    with factory() as db:
        # No listens at all → no seeds.
        new_ids = discover_via_lastfm(db, "u1", now=now)
    assert new_ids == []
