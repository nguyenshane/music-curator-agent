"""Feature computation for the FR-7 scoring formula.

Inputs are pulled from the local `Track` / `Listen` tables. Everything here
is deterministic given the same DB snapshot + clock value so tests can pin
exact scores.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models import Listen, Track


@dataclass(frozen=True)
class CandidateFeatures:
    track_id: int
    title: str
    artist: str
    play_count: int
    last_played_at: datetime
    first_played_at: datetime
    taste_match: float
    context_match: float
    freshness: float
    novelty: float
    diversity: float
    rejection_penalty: float

    def trace(self) -> dict[str, float]:
        return {
            "taste_match": round(self.taste_match, 4),
            "context_match": round(self.context_match, 4),
            "freshness": round(self.freshness, 4),
            "novelty": round(self.novelty, 4),
            "diversity": round(self.diversity, 4),
            "rejection_penalty": round(self.rejection_penalty, 4),
        }


def _time_of_day_label(hour: int) -> str:
    if 5 <= hour < 10:
        return "morning"
    if 10 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "late_night"


def current_context(now: datetime | None = None) -> str:
    """Compose the time-of-day + weekday/weekend context used by lanes."""
    now = now or datetime.now(timezone.utc)
    day = "weekday" if now.weekday() < 5 else "weekend"
    return f"{_time_of_day_label(now.hour)}_{day}"


def compute_features(
    db: Session,
    user_id: str,
    *,
    now: datetime | None = None,
    rejection_penalty_by_track: dict[int, float] | None = None,
    history_window_days: int = 90,
) -> list[CandidateFeatures]:
    """Compute per-track features for every track the user has heard in
    the last `history_window_days`.

    The scorer applies the FR-7 formula on top of these features.
    """
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(days=history_window_days)
    rejection_penalty_by_track = rejection_penalty_by_track or {}

    # Pull listen aggregates per track inside the window.
    rows = db.execute(
        select(
            Listen.track_id,
            func.count(Listen.id).label("play_count"),
            func.min(Listen.played_at).label("first_played_at"),
            func.max(Listen.played_at).label("last_played_at"),
        )
        .where(Listen.user_id == user_id)
        .where(Listen.played_at >= window_start)
        .group_by(Listen.track_id)
    ).all()
    if not rows:
        return []

    aggregates = {r.track_id: r for r in rows}
    track_rows = db.execute(
        select(Track).where(Track.id.in_(aggregates.keys()))
    ).scalars().all()
    tracks = {t.id: t for t in track_rows}

    # Pre-compute per-artist play totals for taste_match normalization.
    artist_counts: Counter[str] = Counter()
    for tid, agg in aggregates.items():
        t = tracks.get(tid)
        if t is not None:
            artist_counts[t.artist] += agg.play_count
    max_artist_count = max(artist_counts.values(), default=1)
    max_play_count = max((a.play_count for a in aggregates.values()), default=1)

    ctx = current_context(now)
    time_label = ctx.split("_", 1)[0]

    features: list[CandidateFeatures] = []
    for tid, agg in aggregates.items():
        track = tracks.get(tid)
        if track is None:
            continue

        taste_match = artist_counts[track.artist] / max_artist_count

        # Context match: if the track was played at the current time-of-day
        # at least once, weight it; otherwise neutral.
        context_match = _context_match_for_track(db, user_id, tid, time_label)

        # Freshness: high when not heard recently (good for re-surfacing).
        days_since_last = (now - agg.last_played_at).total_seconds() / 86400
        freshness = min(1.0, days_since_last / 14.0)

        # Novelty: inverse of normalized play count (rare = novel).
        novelty = 1.0 - (agg.play_count / max_play_count)

        # Diversity placeholder — refined post-selection in the orchestrator
        # because it depends on what's already in the playlist.
        diversity = 0.5

        features.append(
            CandidateFeatures(
                track_id=tid,
                title=track.title,
                artist=track.artist,
                play_count=agg.play_count,
                first_played_at=agg.first_played_at,
                last_played_at=agg.last_played_at,
                taste_match=round(taste_match, 4),
                context_match=round(context_match, 4),
                freshness=round(freshness, 4),
                novelty=round(novelty, 4),
                diversity=diversity,
                rejection_penalty=rejection_penalty_by_track.get(tid, 0.0),
            )
        )
    return features


def _context_match_for_track(
    db: Session, user_id: str, track_id: int, time_label: str
) -> float:
    """Fraction of this track's plays that occurred at the current time-of-day."""
    plays = db.execute(
        select(Listen.played_at)
        .where(Listen.user_id == user_id)
        .where(Listen.track_id == track_id)
    ).scalars().all()
    if not plays:
        return 0.5
    matches = sum(1 for p in plays if _time_of_day_label(p.hour) == time_label)
    return matches / len(plays)


def apply_diversity_penalty(
    candidates: Iterable[CandidateFeatures],
) -> list[CandidateFeatures]:
    """Replace each candidate's diversity placeholder with a value that
    penalizes the Nth occurrence of an artist in the *ordered* list.
    The caller orders by a preliminary score before passing in.
    """
    seen: Counter[str] = Counter()
    out: list[CandidateFeatures] = []
    for c in candidates:
        seen[c.artist] += 1
        diversity = round(1.0 / seen[c.artist], 4)
        out.append(
            CandidateFeatures(
                track_id=c.track_id,
                title=c.title,
                artist=c.artist,
                play_count=c.play_count,
                first_played_at=c.first_played_at,
                last_played_at=c.last_played_at,
                taste_match=c.taste_match,
                context_match=c.context_match,
                freshness=c.freshness,
                novelty=c.novelty,
                diversity=diversity,
                rejection_penalty=c.rejection_penalty,
            )
        )
    return out
