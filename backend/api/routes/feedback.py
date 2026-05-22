"""Track-level user feedback.

POST /feedback           Record a single feedback signal.
GET  /feedback/recent    List recent feedback events for a user.

Signal → weight mapping (positive = boost, negative = penalty):

    love  : +2.0   (strong boost — keep surfacing)
    like  : +1.0   (mild boost)
    skip  : -1.0   (mild penalty — saw it, didn't engage)
    hate  : -2.5   (strong penalty — never surface)

Negative weights flow into `rejection_penalty` in the FR-7 scorer via
`compute_rejection_penalties` with exponential time decay.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.db.models import Base, FeedbackEvent, Track
from backend.db.session import build_session_factory

router = APIRouter(prefix="/feedback", tags=["feedback"])

_session_factory = build_session_factory(get_settings().database_url)

SIGNAL_WEIGHTS: dict[str, float] = {
    "love": 2.0,
    "like": 1.0,
    "skip": -1.0,
    "hate": -2.5,
}

Signal = Literal["love", "like", "skip", "hate"]


def _ensure_schema() -> None:
    bind = _session_factory.kw["bind"]
    Base.metadata.create_all(bind)


class FeedbackBody(BaseModel):
    model_config = {"extra": "forbid"}

    user_id: str
    track_id: int = Field(..., description="Internal Track.id, not a provider id.")
    signal: Signal = Field(..., description="love | like | skip | hate")


@router.post("")
def record(body: FeedbackBody) -> dict[str, Any]:
    _ensure_schema()
    weight = SIGNAL_WEIGHTS[body.signal]
    with _session_factory() as db:  # type: Session
        track = db.scalar(select(Track).where(Track.id == body.track_id))
        if track is None:
            raise HTTPException(status_code=404, detail=f"track_id {body.track_id} not found")
        event = FeedbackEvent(
            user_id=body.user_id,
            track_id=body.track_id,
            event_type=body.signal,
            weight=weight,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
    return {
        "ok": True,
        "id": event.id,
        "user_id": event.user_id,
        "track_id": event.track_id,
        "signal": body.signal,
        "weight": weight,
    }


@router.get("/recent")
def recent(
    user_id: str = Query(...),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    _ensure_schema()
    with _session_factory() as db:  # type: Session
        rows = db.execute(
            select(FeedbackEvent)
            .where(FeedbackEvent.user_id == user_id)
            .order_by(FeedbackEvent.created_at.desc())
            .limit(limit)
        ).scalars().all()
    return {
        "user_id": user_id,
        "events": [
            {
                "id": r.id,
                "track_id": r.track_id,
                "signal": r.event_type,
                "weight": r.weight,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }
