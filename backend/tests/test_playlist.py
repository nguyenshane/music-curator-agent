"""Playlist generation end-to-end against a small synthetic DB."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.db.models import Base, Listen, Track
from backend.recommendation.features import current_context
from backend.recommendation.playlist import generate_playlist, latest_playlist


def _fresh_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session, user_id: str = "u1") -> datetime:
    """Three tracks with different play counts and recency.

    Returns the synthetic "now" the tests anchor against.
    """
    now = datetime(2026, 5, 19, 14, 0, tzinfo=timezone.utc)  # afternoon weekday
    tracks = [
        Track(canonical_key="isrc:A", title="A", artist="Artist X", isrc="A"),
        Track(canonical_key="isrc:B", title="B", artist="Artist X", isrc="B"),
        Track(canonical_key="isrc:C", title="C", artist="Artist Y", isrc="C"),
    ]
    for t in tracks:
        db.add(t)
    db.flush()

    # Heavy: Artist X / A played 5 times across last 30 days, oldest 12 days ago.
    for i in range(5):
        db.add(Listen(
            user_id=user_id, provider="mock", provider_track_id=f"a{i}",
            played_at=now - timedelta(days=12 - i * 2, hours=2),
            track_id=tracks[0].id,
        ))
    # Mid: Artist X / B played twice, last 6 days ago.
    for i in range(2):
        db.add(Listen(
            user_id=user_id, provider="mock", provider_track_id=f"b{i}",
            played_at=now - timedelta(days=6 - i),
            track_id=tracks[1].id,
        ))
    # Light: Artist Y / C played once, 1 day ago.
    db.add(Listen(
        user_id=user_id, provider="mock", provider_track_id="c0",
        played_at=now - timedelta(days=1, hours=2),
        track_id=tracks[2].id,
    ))
    db.commit()
    return now


def test_empty_history_returns_no_items_with_note():
    with _fresh_db() as db:
        result = generate_playlist(db, "ghost", limit=10)
    assert result["items"] == []
    assert "no listening history" in result["notes"]


def test_generates_ranked_items_with_trace():
    with _fresh_db() as db:
        now = _seed(db)
        result = generate_playlist(db, "u1", limit=10, now=now)

    assert result["context"] == current_context(now)
    assert len(result["items"]) == 3

    # Every item carries the full feature trace for Hermes to render.
    for item in result["items"]:
        assert set(item["trace"].keys()) == {
            "taste_match", "context_match", "freshness",
            "novelty", "diversity", "rejection_penalty",
        }

    # Sorted descending by score.
    scores = [item["score"] for item in result["items"]]
    assert scores == sorted(scores, reverse=True)

    # Diversity penalty kicks in for the second Artist X track — it should
    # have lower diversity than the first Artist X track to appear.
    artist_positions: dict[str, list[int]] = {}
    for idx, item in enumerate(result["items"]):
        artist_positions.setdefault(item["artist"], []).append(idx)
    if len(artist_positions.get("Artist X", [])) >= 2:
        first_idx, second_idx = artist_positions["Artist X"][:2]
        assert (
            result["items"][first_idx]["trace"]["diversity"]
            > result["items"][second_idx]["trace"]["diversity"]
        )


def test_persists_and_latest_returns_it():
    with _fresh_db() as db:
        now = _seed(db)
        generated = generate_playlist(db, "u1", limit=5, now=now)

        latest = latest_playlist(db, "u1")
        assert latest is not None
        assert latest["items"] == generated["items"]
        assert latest["context"] == generated["context"]


def test_today_route_falls_back_to_fresh_generation(monkeypatch):
    """First /playlists/today call (no persisted row) regenerates inline.

    FastAPI runs sync endpoints in a threadpool, so the handler thread is
    different from the test thread. Default sqlite `:memory:` engines give
    each thread its own DB (SingletonThreadPool), so the seeded data would
    be invisible. Force StaticPool to share one connection across threads.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend.api.main import app
    from backend.api.routes import playlists as playlists_route

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory() as db:
        _seed(db, user_id="u1")
    monkeypatch.setattr(playlists_route, "_session_factory", factory)

    client = TestClient(app)
    resp = client.get("/playlists/today", params={"user_id": "u1", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == "u1"
    assert len(body["items"]) >= 1
    assert "trace" in body["items"][0]
