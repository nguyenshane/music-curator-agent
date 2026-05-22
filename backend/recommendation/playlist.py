"""Daily playlist generator (v1.6).

Pipeline:
  1. compute artist affinity + rejection penalties + user audio centroid
  2. (best-effort) discover new ghost tracks via Last.fm
  3. compute history features + discovery features
  4. apply skip-window: drop tracks that appeared in the last N daily
     playlists, so today's set doesn't dominate every day's set
  5. score, apply diversity penalty post-order, re-score, sort, top-N
  6. (best-effort) backfill Spotify audio features for the top-N and
     re-score with the audio_similarity term populated
  7. persist as a DailyPlaylist row

Each step is best-effort: discovery / audio-features failures degrade
gracefully to "just rank what we have."
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import DailyPlaylist, Listen, Track
from backend.recommendation.features import (
    AUDIO_FEATURE_KEYS,
    CandidateFeatures,
    apply_diversity_penalty,
    audio_similarity as feature_audio_similarity,
    compute_artist_affinity,
    compute_discovery_features,
    compute_features,
    compute_rejection_penalties,
    compute_user_centroid,
    current_context,
)
from backend.recommendation.scoring import score_track

logger = logging.getLogger(__name__)

SKIP_WINDOW_DAYS = 7


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
                audio_similarity=c.audio_similarity,
            ),
        )
        for c in candidates
    ]


def _recent_playlist_track_ids(
    db: Session, user_id: str, *, now: datetime, days: int = SKIP_WINDOW_DAYS
) -> set[int]:
    cutoff = now - timedelta(days=days)
    rows = db.execute(
        select(DailyPlaylist.items)
        .where(DailyPlaylist.user_id == user_id)
        .where(DailyPlaylist.generated_at >= cutoff)
    ).all()
    seen: set[int] = set()
    for (items,) in rows:
        for item in items or []:
            tid = item.get("track_id")
            if isinstance(tid, int):
                seen.add(tid)
    return seen


def _try_discovery(
    db: Session, user_id: str, *, now: datetime
) -> list[int]:
    """Best-effort Last.fm discovery. Never raises into the caller."""
    try:
        from backend.recommendation.discovery import discover_via_lastfm

        return discover_via_lastfm(db, user_id, now=now)
    except Exception as e:  # noqa: BLE001
        logger.warning("Discovery failed; continuing without new candidates: %s", e)
        return []


def _try_backfill_audio_features(
    db: Session,
    candidates: list[CandidateFeatures],
    *,
    user_centroid: dict[str, float] | None,
) -> tuple[list[CandidateFeatures], dict[str, float] | None]:
    """Backfill audio features for top candidates via Spotify, then
    recompute their audio_similarity. Returns (updated_candidates, centroid).

    Only fetches for tracks that (a) have a Spotify provider_track_id in
    Listen, (b) don't already have cached audio_features. Failures are
    logged and silently skipped.
    """
    try:
        from backend.adapters.spotify import SpotifyAdapter
        from backend.adapters.spotify.audio_features import fetch_audio_features
    except Exception as e:  # noqa: BLE001
        logger.info("Spotify adapter unavailable; skipping audio-features backfill: %s", e)
        return candidates, user_centroid

    # Build track_id → spotify_track_id mapping for candidates needing data.
    needing: dict[int, str] = {}
    track_rows = db.execute(
        select(Track).where(Track.id.in_([c.track_id for c in candidates]))
    ).scalars().all()
    track_by_id = {t.id: t for t in track_rows}
    for c in candidates:
        t = track_by_id.get(c.track_id)
        if t is None or t.audio_features is not None:
            continue
        listen = db.scalar(
            select(Listen.provider_track_id)
            .where(Listen.track_id == c.track_id)
            .where(Listen.provider == "spotify")
            .limit(1)
        )
        if listen:
            needing[c.track_id] = listen

    if not needing:
        return candidates, user_centroid

    try:
        spotify = SpotifyAdapter()
        token = spotify._get_client_token()
        fetched = fetch_audio_features(list(needing.values()), token)
    except Exception as e:  # noqa: BLE001
        logger.warning("Audio-features backfill failed: %s", e)
        return candidates, user_centroid

    # Cache results on the Track rows.
    spotify_to_internal = {sp: internal for internal, sp in needing.items()}
    for sp_id, internal_id in spotify_to_internal.items():
        track = track_by_id.get(internal_id)
        if track is None:
            continue
        features = fetched.get(sp_id)
        track.audio_features = features or {"_unavailable": True}
    db.commit()

    # Recompute centroid (now has more data) and per-candidate similarity.
    updated_centroid = user_centroid
    if updated_centroid is None and fetched:
        # Approximate centroid from what we just fetched if we had nothing.
        sums: dict[str, float] = {k: 0.0 for k in AUDIO_FEATURE_KEYS}
        n = 0
        for af in fetched.values():
            for k in AUDIO_FEATURE_KEYS:
                v = af.get(k)
                if isinstance(v, (int, float)):
                    sums[k] += float(v)
            n += 1
        if n:
            updated_centroid = {k: sums[k] / n for k in AUDIO_FEATURE_KEYS}

    updated: list[CandidateFeatures] = []
    for c in candidates:
        t = track_by_id.get(c.track_id)
        af = t.audio_features if t is not None else None
        sim = feature_audio_similarity(updated_centroid, af)
        if abs(sim - c.audio_similarity) < 1e-6:
            updated.append(c)
        else:
            updated.append(
                CandidateFeatures(
                    track_id=c.track_id, title=c.title, artist=c.artist,
                    play_count=c.play_count,
                    last_played_at=c.last_played_at, first_played_at=c.first_played_at,
                    taste_match=c.taste_match, context_match=c.context_match,
                    freshness=c.freshness, novelty=c.novelty, diversity=c.diversity,
                    rejection_penalty=c.rejection_penalty,
                    audio_similarity=round(sim, 4),
                    source=c.source,
                )
            )
    return updated, updated_centroid


def generate_playlist(
    db: Session,
    user_id: str,
    *,
    limit: int = 20,
    now: datetime | None = None,
    persist: bool = True,
    enable_discovery: bool = True,
    enable_audio_features: bool = True,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    ctx = current_context(now)

    artist_affinity = compute_artist_affinity(db, user_id, now=now)
    rejection_penalty_by_track = compute_rejection_penalties(db, user_id, now=now)
    user_centroid = compute_user_centroid(db, user_id, now=now)

    discovery_ids: list[int] = []
    if enable_discovery:
        discovery_ids = _try_discovery(db, user_id, now=now)

    history = compute_features(
        db, user_id, now=now,
        rejection_penalty_by_track=rejection_penalty_by_track,
        artist_affinity=artist_affinity,
        user_centroid=user_centroid,
    )
    discovery = compute_discovery_features(
        db, discovery_ids,
        artist_affinity=artist_affinity,
        user_centroid=user_centroid,
        rejection_penalty_by_track=rejection_penalty_by_track,
    )

    candidates = history + discovery
    if not candidates:
        result: dict[str, Any] = {
            "user_id": user_id, "generated_at": now.isoformat(),
            "context": ctx, "items": [],
            "notes": "no listening history yet; ingest some listens first",
        }
        if persist:
            _persist(db, result)
        return result

    # Skip-window: drop anything that appeared in a recent persisted playlist.
    recently_picked = _recent_playlist_track_ids(db, user_id, now=now)
    if recently_picked:
        candidates = [c for c in candidates if c.track_id not in recently_picked]
        if not candidates:
            # Everything we'd recommend is in the skip-window. Better to
            # surface yesterday's set than to fail.
            candidates = history + discovery
            logger.info(
                "Skip-window emptied the candidate set for user=%s; relaxing it.",
                user_id,
            )

    # First-pass score (placeholder diversity), order, then diversity-penalise
    # against the ordered list and re-score.
    first = _score_candidates(candidates)
    first.sort(key=lambda s: s.score, reverse=True)
    diversified = apply_diversity_penalty([s.features for s in first])

    # Best-effort audio backfill for the top slice (no point fetching for
    # tracks that won't survive the cut). 2 * limit is a small over-fetch.
    if enable_audio_features:
        top_slice, _ = _try_backfill_audio_features(
            db, diversified[: limit * 2], user_centroid=user_centroid
        )
        diversified = top_slice + diversified[limit * 2 :]

    final = _score_candidates(diversified)
    final.sort(key=lambda s: s.score, reverse=True)

    items = [
        {
            "track_id": s.features.track_id,
            "title": s.features.title,
            "artist": s.features.artist,
            "source": s.features.source,
            "score": round(s.score, 4),
            "trace": s.features.trace(),
        }
        for s in final[:limit]
    ]
    result = {
        "user_id": user_id, "generated_at": now.isoformat(),
        "context": ctx, "items": items, "notes": None,
    }
    if persist:
        _persist(db, result)
    logger.info(
        "playlist.generated user=%s items=%d (history=%d discovery=%d) context=%s",
        user_id, len(items),
        sum(1 for s in final[:limit] if s.features.source == "history"),
        sum(1 for s in final[:limit] if s.features.source == "discovery"),
        ctx,
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
    row = db.scalar(
        select(DailyPlaylist)
        .where(DailyPlaylist.user_id == user_id)
        .order_by(DailyPlaylist.generated_at.desc())
        .limit(1)
    )
    if row is None:
        return None
    return {
        "user_id": row.user_id,
        "generated_at": row.generated_at.isoformat(),
        "context": row.context,
        "items": row.items,
        "notes": row.notes,
    }
