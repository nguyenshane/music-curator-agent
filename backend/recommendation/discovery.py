"""External candidate discovery via Last.fm `track.getSimilar`.

The catalog of listened tracks is closed by definition — without an
external source the recommender can only re-rank what the user has
already heard. This module seeds discovery off the user's top tracks in
the last 30 days, fetches similar tracks from Last.fm, and inserts ghost
`Track` rows (no associated `Listen`) so the playlist generator can score
them alongside listened candidates.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.adapters.lastfm import LASTFM_API_BASE
from backend.config import get_settings
from backend.db.models import Listen, Track
from backend.ingestion import canonical_key

logger = logging.getLogger(__name__)

DISCOVERY_SEED_TRACKS = 3
DISCOVERY_SIMILAR_PER_SEED = 15
DISCOVERY_MAX_NEW = 50


def _top_seed_tracks(
    db: Session, user_id: str, *, now: datetime, window_days: int = 30, limit: int = DISCOVERY_SEED_TRACKS
) -> list[Track]:
    """Top played tracks for the user in the window, ordered by play count."""
    cutoff = now - timedelta(days=window_days)
    rows = db.execute(
        select(Track)
        .join(Listen, Listen.track_id == Track.id)
        .where(Listen.user_id == user_id)
        .where(Listen.played_at >= cutoff)
        .group_by(Track.id)
        .order_by(func.count(Listen.id).desc())
        .limit(limit)
    ).scalars().all()
    return list(rows)


def _lastfm_get_similar(
    client: httpx.Client, artist: str, track: str, api_key: str, *, limit: int
) -> list[dict[str, Any]]:
    """Call `track.getSimilar` and return its `similartracks.track` list.

    Returns [] on transport / 4xx errors (logged) so a flaky external
    API doesn't kill the whole stage.
    """
    try:
        response = client.get(
            LASTFM_API_BASE,
            params={
                "method": "track.getSimilar",
                "artist": artist,
                "track": track,
                "api_key": api_key,
                "format": "json",
                "limit": limit,
            },
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("Last.fm getSimilar failed for %r/%r: %s", artist, track, e)
        return []
    payload = response.json() or {}
    return ((payload.get("similartracks") or {}).get("track")) or []


def discover_via_lastfm(
    db: Session,
    user_id: str,
    *,
    now: datetime | None = None,
    max_new: int = DISCOVERY_MAX_NEW,
    client: httpx.Client | None = None,
) -> list[int]:
    """Pull similar tracks from Last.fm and insert ghost Track rows.

    Returns the list of track_ids for newly inserted ghost tracks. Idempotent:
    on a second call with the same seed inputs, tracks already present in the
    catalog are skipped (returns only the new ones added this call).
    """
    settings = get_settings()
    api_key = settings.lastfm_api_key
    if not api_key:
        logger.info("Discovery: LASTFM_API_KEY not set; skipping")
        return []

    now = now or datetime.now(timezone.utc)
    seeds = _top_seed_tracks(db, user_id, now=now)
    if not seeds:
        logger.info("Discovery: no seed tracks for user=%s", user_id)
        return []

    owns_client = client is None
    if client is None:
        client = httpx.Client()
    try:
        new_track_ids: list[int] = []
        for seed in seeds:
            if len(new_track_ids) >= max_new:
                break
            similar = _lastfm_get_similar(
                client, seed.artist, seed.title, api_key, limit=DISCOVERY_SIMILAR_PER_SEED
            )
            for item in similar:
                if len(new_track_ids) >= max_new:
                    break
                title = (item.get("name") or "").strip()
                artist_block = item.get("artist") or {}
                artist = (artist_block.get("name") if isinstance(artist_block, dict) else str(artist_block)) or ""
                artist = artist.strip()
                if not title or not artist:
                    continue
                key = canonical_key(isrc=None, title=title, artist=artist, duration_ms=None)
                existing = db.scalar(select(Track).where(Track.canonical_key == key))
                if existing is not None:
                    continue
                ghost = Track(canonical_key=key, title=title, artist=artist)
                db.add(ghost)
                db.flush()
                new_track_ids.append(ghost.id)
        db.commit()
    finally:
        if owns_client:
            client.close()

    logger.info("Discovery: inserted %d new ghost tracks for user=%s", len(new_track_ids), user_id)
    return new_track_ids


def candidate_track_ids_for_user(
    db: Session, user_id: str, *, now: datetime | None = None, window_days: int = 90
) -> set[int]:
    """All Track ids the user has any Listen for in the window — used to
    distinguish history from discovery when both are in scope.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    ids = db.execute(
        select(Listen.track_id)
        .where(Listen.user_id == user_id)
        .where(Listen.played_at >= cutoff)
        .group_by(Listen.track_id)
    ).scalars().all()
    return {int(i) for i in ids}


def ghost_track_ids(db: Session, *, exclude: Iterable[int]) -> list[int]:
    """Track ids with no listens at all — eligible discovery candidates."""
    exclude_ids = set(int(i) for i in exclude)
    has_listens = db.execute(select(Listen.track_id).group_by(Listen.track_id)).scalars().all()
    listened = set(int(i) for i in has_listens)
    all_tracks = db.execute(select(Track.id)).scalars().all()
    return [int(i) for i in all_tracks if int(i) not in listened and int(i) not in exclude_ids]
