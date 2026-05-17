"""Session builder — groups listens into listening sessions.

Rule: gap > 30 minutes between consecutive listens = new session.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Listen, Track
from backend.config import get_settings

logger = logging.getLogger(__name__)

SESSION_GAP_MINUTES = 30


class ListeningSession:
    """A user's listening session."""

    def __init__(
        self,
        session_id: int,
        user_id: str,
        start_time: datetime,
        end_time: datetime,
        track_count: int,
        providers: list[str],
        top_artists: list[str],
        context: str,
    ):
        self.session_id = session_id
        self.user_id = user_id
        self.start_time = start_time
        self.end_time = end_time
        self.duration_minutes = max(1, (end_time - start_time).total_seconds() / 60)
        self.track_count = track_count
        self.providers = providers
        self.top_artists = top_artists
        self.context = context

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "duration_minutes": self.duration_minutes,
            "track_count": self.track_count,
            "providers": self.providers,
            "top_artists": self.top_artists,
            "context": self.context,
        }


def _detect_context(session: ListeningSession) -> str:
    """Detect the context (time of day, day type) for a session."""
    start = session.start_time
    hour = start.hour

    # Time of day
    if 5 <= hour < 10:
        time_label = "morning"
    elif 10 <= hour < 13:
        time_label = "midday"
    elif 13 <= hour < 17:
        time_label = "afternoon"
    elif 17 <= hour < 21:
        time_label = "evening"
    else:
        time_label = "late night"

    # Day type
    if start.weekday() < 5:  # Monday=0, Friday=4
        day_type = "weekday"
    else:
        day_type = "weekend"

    return f"{time_label}_{day_type}"


def _get_top_artists_from_user(db: Session, user_id: str, limit: int = 5) -> list[str]:
    """Get top artists from a user's listens."""
    result = db.execute(
        select(Listen.track_id, Track.artist)
        .join(Track, Listen.track_id == Track.id)
        .where(Listen.user_id == user_id)
        .limit(limit * 10)
    )
    artist_counts: dict[str, int] = {}
    for track_id, artist in result:
        if artist:
            artist_counts[artist] = artist_counts.get(artist, 0) + 1
    sorted_artists = sorted(artist_counts.items(), key=lambda x: x[1], reverse=True)
    return [artist for artist, _ in sorted_artists[:limit]]


def build_sessions(
    db: Session,
    user_id: str,
    since: datetime | None = None,
) -> list[ListeningSession]:
    """Build listening sessions from the user's listen history.

    Groups consecutive listens by 30-minute gap rule.
    """
    query = select(Listen).where(Listen.user_id == user_id)
    if since:
        query = query.where(Listen.played_at >= since)
    query = query.order_by(Listen.played_at)

    result = db.execute(query)
    listens = result.scalars().all()

    if not listens:
        logger.info(f"Session builder: no listens for user={user_id}")
        return []

    # Group into sessions
    sessions: list[ListeningSession] = []
    current_tracks: list[Any] = []
    session_start = listens[0].played_at

    for listen in listens:
        if current_tracks and (listen.played_at - current_tracks[-1].played_at).total_seconds() > SESSION_GAP_MINUTES * 60:
            # Close current session
            session = _finalize_session(db, user_id, session_start, current_tracks)
            sessions.append(session)
            current_tracks = []
            session_start = listen.played_at
        current_tracks.append(listen)

    # Finalize last session
    if current_tracks:
        session = _finalize_session(db, user_id, session_start, current_tracks)
        sessions.append(session)

    logger.info(f"Session builder: created {len(sessions)} sessions for user={user_id}")
    return sessions


def _finalize_session(
    db: Session,
    user_id: str,
    start_time: datetime,
    tracks: list[Any],
) -> ListeningSession:
    """Create a ListeningSession from a group of tracks."""
    end_time = tracks[-1].played_at
    providers = list(set(t.provider for t in tracks))
    top_artists = _get_top_artists_from_user(db, user_id, limit=5)
    context = _detect_context(
        ListeningSession(0, user_id, start_time, end_time, len(tracks), providers, top_artists, "")
    )
    return ListeningSession(
        session_id=0,  # Will be assigned when persisted
        user_id=user_id,
        start_time=start_time,
        end_time=end_time,
        track_count=len(tracks),
        providers=providers,
        top_artists=top_artists,
        context=context,
    )
