"""Feedback endpoint + rejection-penalty wiring."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import Base, FeedbackEvent, Listen, Track
from backend.recommendation.features import compute_rejection_penalties


def _seeded_db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    with factory() as db:
        track = Track(canonical_key="isrc:Z", title="Z", artist="Hateful Band")
        db.add(track)
        db.commit()
        db.refresh(track)
        tid = track.id
    return factory, tid


def test_rejection_penalty_decays_with_age():
    factory, tid = _seeded_db()
    now = datetime(2026, 5, 19, 14, 0, tzinfo=timezone.utc)
    with factory() as db:
        # Two negative events: one fresh, one old. Decay τ=14 days.
        db.add(FeedbackEvent(
            user_id="u1", track_id=tid, event_type="hate", weight=-2.5,
            created_at=now,
        ))
        db.add(FeedbackEvent(
            user_id="u1", track_id=tid, event_type="hate", weight=-2.5,
            created_at=now - timedelta(days=60),
        ))
        db.commit()

        penalties = compute_rejection_penalties(db, "u1", now=now)
    assert tid in penalties
    # Fresh hate contributes ~2.5; 60-day-old hate decays heavily but is non-zero.
    assert penalties[tid] > 2.5
    assert penalties[tid] < 2.5 + 1.0  # 60-day decay should be well under 1


def test_positive_feedback_not_in_rejection_penalties():
    factory, tid = _seeded_db()
    with factory() as db:
        db.add(FeedbackEvent(user_id="u1", track_id=tid, event_type="love", weight=2.0))
        db.commit()
        penalties = compute_rejection_penalties(db, "u1")
    assert penalties == {}, "positive feedback should not produce a penalty"


def test_feedback_post_route(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from backend.api.main import app
    from backend.api.routes import feedback as feedback_route

    factory, tid = _seeded_db()
    monkeypatch.setattr(feedback_route, "_session_factory", factory)

    client = TestClient(app)
    resp = client.post(
        "/feedback",
        json={"user_id": "u1", "track_id": tid, "signal": "hate"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["weight"] == -2.5

    listed = client.get("/feedback/recent", params={"user_id": "u1"}).json()
    assert len(listed["events"]) == 1
    assert listed["events"][0]["signal"] == "hate"


def test_feedback_rejects_extra_fields(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient
    from backend.api.main import app
    from backend.api.routes import feedback as feedback_route
    factory, tid = _seeded_db()
    monkeypatch.setattr(feedback_route, "_session_factory", factory)
    client = TestClient(app)
    resp = client.post(
        "/feedback",
        json={"user_id": "u1", "track_id": tid, "signal": "hate", "weight": 999.0},
    )
    assert resp.status_code == 422


def test_feedback_unknown_track_404(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient
    from backend.api.main import app
    from backend.api.routes import feedback as feedback_route
    factory, _tid = _seeded_db()
    monkeypatch.setattr(feedback_route, "_session_factory", factory)
    client = TestClient(app)
    resp = client.post("/feedback", json={"user_id": "u1", "track_id": 9999, "signal": "skip"})
    assert resp.status_code == 404
