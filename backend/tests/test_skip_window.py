"""Skip-window: tracks present in a recent persisted playlist are excluded."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import Base, DailyPlaylist, Listen, Track
from backend.recommendation.playlist import generate_playlist


def _factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)


def test_recent_picks_are_excluded_when_alternatives_exist():
    factory = _factory()
    now = datetime(2026, 5, 19, 14, 0, tzinfo=timezone.utc)
    with factory() as db:
        tracks = [
            Track(canonical_key=f"k{i}", title=f"T{i}", artist=f"A{i}")
            for i in range(6)
        ]
        for t in tracks:
            db.add(t)
        db.flush()
        for t in tracks:
            db.add(Listen(
                user_id="u1", provider="m", provider_track_id=f"p{t.id}",
                played_at=now - timedelta(days=1), track_id=t.id,
            ))
        # Yesterday's playlist included the first 3 tracks.
        db.add(DailyPlaylist(
            user_id="u1",
            generated_at=now - timedelta(days=1),
            context="afternoon_weekday",
            items=[{"track_id": tracks[i].id} for i in range(3)],
        ))
        db.commit()
        listened_ids = {t.id for t in tracks}
        recent_ids = {tracks[i].id for i in range(3)}

    with factory() as db:
        result = generate_playlist(
            db, "u1", limit=10, now=now,
            enable_discovery=False, enable_audio_features=False,
        )

    chosen = {item["track_id"] for item in result["items"]}
    # None of the recent picks survive when alternatives are available.
    assert chosen.isdisjoint(recent_ids)
    assert chosen <= listened_ids - recent_ids


def test_relaxes_when_skip_window_would_empty_the_set():
    factory = _factory()
    now = datetime(2026, 5, 19, 14, 0, tzinfo=timezone.utc)
    with factory() as db:
        tracks = [Track(canonical_key=f"k{i}", title=f"T{i}", artist=f"A{i}") for i in range(2)]
        for t in tracks:
            db.add(t)
        db.flush()
        for t in tracks:
            db.add(Listen(
                user_id="u1", provider="m", provider_track_id=f"p{t.id}",
                played_at=now - timedelta(days=1), track_id=t.id,
            ))
        # Yesterday's playlist already includes *every* listened track.
        db.add(DailyPlaylist(
            user_id="u1",
            generated_at=now - timedelta(days=1),
            context="afternoon_weekday",
            items=[{"track_id": t.id} for t in tracks],
        ))
        db.commit()

    with factory() as db:
        result = generate_playlist(
            db, "u1", limit=10, now=now,
            enable_discovery=False, enable_audio_features=False,
        )
    # Rather than returning empty, the generator relaxes the window.
    assert len(result["items"]) == 2
