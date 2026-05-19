"""Daily playlist generator.

Orchestrates: candidate retrieval → feature computation → FR-7 scoring →
diversity penalty → top-N selection → persistence as a `DailyPlaylist` row.

The same function is called by the daily DAG (`recommendation_scoring`
stage) and by the `/playlists/today` route when `regenerate=true`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import DailyPlaylist
from backend.recommendation.features import (
    CandidateFeatures,
    apply_diversity_penalty,
    compute_features,
    current_context,
)
from backend.recommendation.scoring import score_track

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScoredCandidate:
    features: CandidateFeatures
    score: float


def _score_candidates(candidates: list[CandidateFeatures]) -> list[ScoredCandidate]:
    return [
        ScoredCandidate(
            features=c,
            score=score_track(
                taste_match=c.taste_match,
                context_match=c.context_match,
                freshness=c.freshness,
                novelty=c.novelty,
                diversity=c.diversity,
                rejection_penalty=c.rejection_penalty,
            ),
        )
        for c in candidates
    ]


def generate_playlist(
    db: Session,
    user_id: str,
    *,
    limit: int = 20,
    now: datetime | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Generate a fresh playlist and (optionally) persist it.

    Returns the dict that the API route serializes — same shape regardless
    of whether the call came from the DAG or an ad-hoc Hermes request.
    """
    now = now or datetime.now(timezone.utc)
    ctx = current_context(now)

    features = compute_features(db, user_id, now=now)
    if not features:
        result: dict[str, Any] = {
            "user_id": user_id,
            "generated_at": now.isoformat(),
            "context": ctx,
            "items": [],
            "notes": "no listening history yet; ingest some listens first",
        }
        if persist:
            _persist(db, result)
        return result

    # First-pass scoring with placeholder diversity, sort, then apply the
    # diversity penalty against the ordered list and re-score.
    first_pass = _score_candidates(features)
    first_pass.sort(key=lambda s: s.score, reverse=True)
    ordered_features = [s.features for s in first_pass]
    diversified = apply_diversity_penalty(ordered_features)
    final = _score_candidates(diversified)
    final.sort(key=lambda s: s.score, reverse=True)

    items = [
        {
            "track_id": s.features.track_id,
            "title": s.features.title,
            "artist": s.features.artist,
            "score": round(s.score, 4),
            "trace": s.features.trace(),
        }
        for s in final[:limit]
    ]
    result = {
        "user_id": user_id,
        "generated_at": now.isoformat(),
        "context": ctx,
        "items": items,
        "notes": None,
    }
    if persist:
        _persist(db, result)
    logger.info(
        "playlist.generated user=%s items=%d context=%s", user_id, len(items), ctx
    )
    return result


def _persist(db: Session, result: dict[str, Any]) -> DailyPlaylist:
    row = DailyPlaylist(
        user_id=result["user_id"],
        generated_at=datetime.fromisoformat(result["generated_at"]),
        context=result.get("context"),
        items=result.get("items", []),
        notes=result.get("notes"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def latest_playlist(db: Session, user_id: str) -> dict[str, Any] | None:
    """Most recently persisted playlist for a user, or None."""
    row = db.scalar(
        select(DailyPlaylist)
        .where(DailyPlaylist.user_id == user_id)
        .order_by(DailyPlaylist.generated_at.desc())
        .limit(1)
    )
    if row is None:
        return None
    # SQLite returns naive datetimes even on DateTime(timezone=True) columns;
    # re-tag as UTC so the ISO string carries an offset for downstream parsers.
    generated_at = row.generated_at
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    return {
        "user_id": row.user_id,
        "generated_at": generated_at.isoformat(),
        "context": row.context,
        "items": row.items,
        "notes": row.notes,
    }
