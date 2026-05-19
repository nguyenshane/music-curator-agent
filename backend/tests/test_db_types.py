"""UtcDateTime round-trip guarantees.

The whole point of the decorator is that values read back from SQLite are
tz-aware UTC, so arithmetic against `datetime.now(timezone.utc)` never
raises TypeError. These tests pin that behavior across the bind/result
boundary on both naive and tz-aware inputs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.db.models import Base, Listen, Track


def _fresh_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _insert_listen(db: Session, played_at: datetime) -> Listen:
    track = Track(canonical_key=f"k:{played_at.isoformat()}", title="t", artist="a")
    db.add(track)
    db.flush()
    listen = Listen(
        user_id="u1",
        provider="mock",
        provider_track_id="p1",
        played_at=played_at,
        track_id=track.id,
    )
    db.add(listen)
    db.commit()
    db.refresh(listen)
    return listen


def test_tz_aware_input_round_trips_as_utc():
    aware = datetime(2026, 5, 19, 14, 0, tzinfo=timezone.utc)
    with _fresh_db() as db:
        listen = _insert_listen(db, aware)
        # Force a fresh read from the DB, bypassing any identity-map cache.
        db.expire(listen)
        fetched = db.scalar(select(Listen).where(Listen.id == listen.id))
        assert fetched is not None
        assert fetched.played_at == aware
        assert fetched.played_at.tzinfo is not None


def test_naive_input_is_treated_as_utc_on_write_and_read():
    naive = datetime(2026, 5, 19, 14, 0)
    with _fresh_db() as db:
        listen = _insert_listen(db, naive)
        db.expire(listen)
        fetched = db.scalar(select(Listen).where(Listen.id == listen.id))
        assert fetched is not None
        assert fetched.played_at.tzinfo is not None
        assert fetched.played_at == naive.replace(tzinfo=timezone.utc)


def test_subtraction_against_now_never_raises():
    """The original bug: subtracting a sqlite-read datetime from now()."""
    with _fresh_db() as db:
        listen = _insert_listen(db, datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc))
        db.expire(listen)
        fetched = db.scalar(select(Listen).where(Listen.id == listen.id))
        # Would have raised TypeError before the decorator was in place.
        delta = datetime.now(timezone.utc) - fetched.played_at
        assert isinstance(delta, timedelta)


def test_non_utc_input_is_converted_to_utc():
    """A datetime in a non-UTC zone should be stored as the equivalent UTC."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:  # pragma: no cover — Python 3.9+ has zoneinfo
        return

    pdt = datetime(2026, 5, 19, 7, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    with _fresh_db() as db:
        listen = _insert_listen(db, pdt)
        db.expire(listen)
        fetched = db.scalar(select(Listen).where(Listen.id == listen.id))
        assert fetched is not None
        assert fetched.played_at == pdt.astimezone(timezone.utc)
        assert fetched.played_at.tzinfo == timezone.utc
