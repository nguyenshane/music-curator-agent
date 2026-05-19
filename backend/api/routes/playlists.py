"""Daily playlist endpoints.

GET  /playlists/today    Returns the most recently persisted playlist for
                         a user. If `regenerate=true`, re-runs the
                         generator inline before returning.

POST /playlists/today    Forces a regeneration. Equivalent to
                         `?regenerate=true` GET, but separate so caches
                         and CDNs can treat regeneration as a write.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.db.models import Base
from backend.db.session import build_session_factory
from backend.recommendation.playlist import generate_playlist, latest_playlist

router = APIRouter(prefix="/playlists", tags=["playlists"])

_session_factory = build_session_factory(get_settings().database_url)


def _ensure_schema() -> None:
    bind = _session_factory.kw["bind"]
    Base.metadata.create_all(bind)


class RegenerateBody(BaseModel):
    user_id: str
    limit: int = Field(20, ge=1, le=100)


@router.get("/today")
def today(
    user_id: str = Query(..., description="Internal user identifier."),
    limit: int = Query(20, ge=1, le=100),
    regenerate: bool = Query(False, description="Force a fresh generation before returning."),
) -> dict[str, Any]:
    _ensure_schema()
    with _session_factory() as db:  # type: Session
        if regenerate:
            return generate_playlist(db, user_id, limit=limit)
        existing = latest_playlist(db, user_id)
        if existing is not None:
            return existing
        # No persisted playlist yet — fall back to fresh generation so
        # Hermes never has to call twice on a cold cache.
        return generate_playlist(db, user_id, limit=limit)


@router.post("/today")
def regenerate(body: RegenerateBody) -> dict[str, Any]:
    _ensure_schema()
    with _session_factory() as db:  # type: Session
        return generate_playlist(db, body.user_id, limit=body.limit)
