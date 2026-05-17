from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.adapters.base import ListeningHistoryAdapter
from backend.db.models import Listen, Track


def canonical_key(*, isrc: str | None, title: str, artist: str, duration_ms: int | None) -> str:
    if isrc:
        return f"isrc:{isrc.lower()}"
    return f"meta:{title.strip().lower()}::{artist.strip().lower()}::{duration_ms or 0}"


def ingest_listening_history(
    *,
    db: Session,
    adapter: ListeningHistoryAdapter,
    user_id: str,
    since: datetime | None = None,
) -> dict[str, int]:
    events = adapter.fetch_listens(user_id=user_id, since=since)
    ingested = 0
    deduped = 0

    for event in events:
        key = canonical_key(
            isrc=event.track.isrc,
            title=event.track.title,
            artist=event.track.artist,
            duration_ms=event.track.duration_ms,
        )
        track = db.scalar(select(Track).where(Track.canonical_key == key))
        if track is None:
            track = Track(
                canonical_key=key,
                title=event.track.title,
                artist=event.track.artist,
                isrc=event.track.isrc,
                duration_ms=event.track.duration_ms,
            )
            db.add(track)
            db.flush()

        existing = db.scalar(
            select(Listen).where(
                Listen.user_id == user_id,
                Listen.provider == event.provider,
                Listen.provider_track_id == event.track.track_id,
                Listen.played_at == event.played_at,
            )
        )
        if existing is not None:
            deduped += 1
            continue

        db.add(
            Listen(
                user_id=user_id,
                provider=event.provider,
                provider_track_id=event.track.track_id,
                played_at=event.played_at,
                track_id=track.id,
            )
        )
        ingested += 1

    db.commit()
    return {"ingested": ingested, "deduped": deduped, "total": len(events)}
