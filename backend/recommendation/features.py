"""Feature computation for the FR-7 scoring formula.

Inputs are pulled from local `Track` / `Listen` / `FeedbackEvent` tables.
Everything here is deterministic given the same DB snapshot + clock value
so tests can pin exact scores.

v1.6 changes:
- `_context_match_for_track` no longer floors to 0.5 when there are no
  same-time-of-day plays — a track played only at other times now scores
  0 on context, which is a real negative signal.
- `apply_diversity_penalty` switches from 1/n to a harder linear penalty
  so the third occurrence of an artist drops out entirely.
- New helpers: `compute_artist_affinity` (shared between listened and
  discovery candidates), `compute_rejection_penalties` (drives the
  feedback loop), `compute_user_centroid` (mean audio-features vector
  for Spotify similarity).
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models import FeedbackEvent, Listen, Track

# Subset of Spotify audio features we use for similarity. Stuck to the
# bounded-[0,1] features so cosine similarity is well-behaved without
# additional normalisation; loudness (dB) and tempo (BPM) are excluded.
AUDIO_FEATURE_KEYS = (
    "acousticness",
    "danceability",
    "energy",
    "instrumentalness",
    "liveness",
    "speechiness",
    "valence",
)


@dataclass(frozen=True)
class CandidateFeatures:
    track_id: int
    title: str
    artist: str
    play_count: int
    last_played_at: datetime | None
    first_played_at: datetime | None
    taste_match: float
    context_match: float
    freshness: float
    novelty: float
    diversity: float
    rejection_penalty: float
    audio_similarity: float = 0.5
    source: str = "history"  # "history" or "discovery"

    def trace(self) -> dict[str, float]:
        return {
            "taste_match": round(self.taste_match, 4),
            "context_match": round(self.context_match, 4),
            "freshness": round(self.freshness, 4),
            "novelty": round(self.novelty, 4),
            "diversity": round(self.diversity, 4),
            "rejection_penalty": round(self.rejection_penalty, 4),
            "audio_similarity": round(self.audio_similarity, 4),
        }


def _ensure_utc(dt: datetime) -> datetime:
    """Defensive UTC coercion. The `UtcDateTime` column type guarantees
    DB reads are already tz-aware UTC; this insulates callers that build
    CandidateFeatures from non-DB sources.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _time_of_day_label(hour: int) -> str:
    if 5 <= hour < 10:
        return "morning"
    if 10 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "late_night"


def current_context(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    day = "weekday" if now.weekday() < 5 else "weekend"
    return f"{_time_of_day_label(now.hour)}_{day}"


# ── Shared helpers ────────────────────────────────────────────────────


def compute_artist_affinity(
    db: Session, user_id: str, *, now: datetime | None = None, window_days: int = 90
) -> dict[str, float]:
    """Per-artist normalised play count, used as taste_match."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    rows = db.execute(
        select(Track.artist, func.count(Listen.id))
        .join(Listen, Listen.track_id == Track.id)
        .where(Listen.user_id == user_id)
        .where(Listen.played_at >= cutoff)
        .group_by(Track.artist)
    ).all()
    if not rows:
        return {}
    max_count = max(int(c) for _, c in rows) or 1
    return {artist: int(c) / max_count for artist, c in rows}


def compute_rejection_penalties(
    db: Session,
    user_id: str,
    *,
    now: datetime | None = None,
    decay_days: float = 14.0,
    window_days: int = 90,
) -> dict[int, float]:
    """Sum decayed negative feedback weights per track.

    Feedback weights are written as positive for "love"/"like" and negative
    for "skip"/"hate" — this function returns penalties keyed by track_id
    that the scorer subtracts. Each feedback event decays exponentially so
    a strong "hate" from two months ago doesn't lock a track out forever.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    rows = db.execute(
        select(FeedbackEvent.track_id, FeedbackEvent.weight, FeedbackEvent.created_at)
        .where(FeedbackEvent.user_id == user_id)
        .where(FeedbackEvent.weight < 0)
        .where(FeedbackEvent.created_at >= cutoff)
    ).all()
    penalties: dict[int, float] = {}
    for tid, weight, created_at in rows:
        created = _ensure_utc(created_at)
        days_old = max(0.0, (now - created).total_seconds() / 86400)
        decayed = abs(float(weight)) * math.exp(-days_old / decay_days)
        penalties[int(tid)] = penalties.get(int(tid), 0.0) + decayed
    return penalties


def compute_user_centroid(
    db: Session, user_id: str, *, window_days: int = 90, now: datetime | None = None
) -> dict[str, float] | None:
    """Mean audio-features vector across the user's recent listened tracks.

    Returns None if no tracks with cached audio_features exist in the
    window — callers should fall back to neutral audio_similarity in that
    case so scoring still works.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    rows = db.execute(
        select(Track.audio_features)
        .join(Listen, Listen.track_id == Track.id)
        .where(Listen.user_id == user_id)
        .where(Listen.played_at >= cutoff)
        .where(Track.audio_features.is_not(None))
    ).all()
    sums: dict[str, float] = {k: 0.0 for k in AUDIO_FEATURE_KEYS}
    n = 0
    for (af,) in rows:
        if not af or af.get("_unavailable"):
            continue
        for k in AUDIO_FEATURE_KEYS:
            v = af.get(k)
            if isinstance(v, (int, float)):
                sums[k] += float(v)
        n += 1
    if n == 0:
        return None
    return {k: sums[k] / n for k in AUDIO_FEATURE_KEYS}


def audio_similarity(centroid: dict[str, float] | None, features: dict | None) -> float:
    """Cosine similarity between two audio-features vectors, clipped to [0,1].

    Returns 0.5 (neutral) when either side is missing so unscored tracks
    aren't disadvantaged.
    """
    if not centroid or not features or features.get("_unavailable"):
        return 0.5
    v1 = [centroid[k] for k in AUDIO_FEATURE_KEYS]
    v2 = [float(features.get(k, 0.0)) for k in AUDIO_FEATURE_KEYS]
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    if n1 == 0 or n2 == 0:
        return 0.5
    sim = dot / (n1 * n2)
    return max(0.0, min(1.0, sim))


# ── Listened-track features ──────────────────────────────────────────


def compute_features(
    db: Session,
    user_id: str,
    *,
    now: datetime | None = None,
    rejection_penalty_by_track: dict[int, float] | None = None,
    artist_affinity: dict[str, float] | None = None,
    user_centroid: dict[str, float] | None = None,
    history_window_days: int = 90,
) -> list[CandidateFeatures]:
    """Per-track features for every track the user has heard in the window."""
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(days=history_window_days)
    rejection_penalty_by_track = rejection_penalty_by_track or {}
    if artist_affinity is None:
        artist_affinity = compute_artist_affinity(
            db, user_id, now=now, window_days=history_window_days
        )

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

    max_play_count = max((a.play_count for a in aggregates.values()), default=1)
    ctx = current_context(now)
    time_label = ctx.split("_", 1)[0]

    features: list[CandidateFeatures] = []
    for tid, agg in aggregates.items():
        track = tracks.get(tid)
        if track is None:
            continue

        taste_match = artist_affinity.get(track.artist, 0.0)
        context_match = _context_match_for_track(db, user_id, tid, time_label)
        last_played_at = _ensure_utc(agg.last_played_at)
        first_played_at = _ensure_utc(agg.first_played_at)
        days_since_last = (now - last_played_at).total_seconds() / 86400
        freshness = min(1.0, days_since_last / 14.0)
        novelty = 1.0 - (agg.play_count / max_play_count)
        diversity = 0.5  # refined post-order by apply_diversity_penalty
        a_sim = audio_similarity(user_centroid, track.audio_features)

        features.append(
            CandidateFeatures(
                track_id=tid,
                title=track.title,
                artist=track.artist,
                play_count=agg.play_count,
                first_played_at=first_played_at,
                last_played_at=last_played_at,
                taste_match=round(taste_match, 4),
                context_match=round(context_match, 4),
                freshness=round(freshness, 4),
                novelty=round(novelty, 4),
                diversity=diversity,
                rejection_penalty=rejection_penalty_by_track.get(tid, 0.0),
                audio_similarity=round(a_sim, 4),
                source="history",
            )
        )
    return features


def _context_match_for_track(
    db: Session, user_id: str, track_id: int, time_label: str
) -> float:
    """Fraction of this track's plays at the current time-of-day.

    Returns 0.0 (not 0.5) if the user has heard this track but never at
    the current time-of-day — that's a real negative signal, not "no
    information". 0.5 is reserved for genuinely unknown context (used by
    discovery candidates that have no plays at all).
    """
    plays = db.execute(
        select(Listen.played_at)
        .where(Listen.user_id == user_id)
        .where(Listen.track_id == track_id)
    ).scalars().all()
    if not plays:
        return 0.5  # no plays = no signal = neutral
    matches = sum(1 for p in plays if _time_of_day_label(p.hour) == time_label)
    return matches / len(plays)


# ── Discovery features (ghost tracks with no Listen rows) ────────────


def compute_discovery_features(
    db: Session,
    track_ids: Iterable[int],
    *,
    artist_affinity: dict[str, float],
    user_centroid: dict[str, float] | None = None,
    rejection_penalty_by_track: dict[int, float] | None = None,
) -> list[CandidateFeatures]:
    """Features for tracks the user has *not* heard.

    taste_match comes from artist affinity (a similar-artist track scores
    high on taste); context_match is neutral (we have no time-of-day
    evidence); freshness and novelty are maximal.
    """
    rejection_penalty_by_track = rejection_penalty_by_track or {}
    track_ids = list(track_ids)
    if not track_ids:
        return []
    rows = db.execute(select(Track).where(Track.id.in_(track_ids))).scalars().all()
    out: list[CandidateFeatures] = []
    for t in rows:
        out.append(
            CandidateFeatures(
                track_id=t.id,
                title=t.title,
                artist=t.artist,
                play_count=0,
                last_played_at=None,
                first_played_at=None,
                taste_match=round(artist_affinity.get(t.artist, 0.0), 4),
                context_match=0.5,
                freshness=1.0,
                novelty=1.0,
                diversity=0.5,
                rejection_penalty=rejection_penalty_by_track.get(t.id, 0.0),
                audio_similarity=round(audio_similarity(user_centroid, t.audio_features), 4),
                source="discovery",
            )
        )
    return out


# ── Diversity penalty (post-order) ───────────────────────────────────


def apply_diversity_penalty(
    candidates: Iterable[CandidateFeatures],
) -> list[CandidateFeatures]:
    """Penalise repeat artists in the ordered list.

    Linear ramp: 1st occurrence = 1.0, 2nd = 0.25, 3rd+ = 0.0. Sharper
    than the previous 1/n curve so a 4-track Dominic Fike streak isn't
    possible without a savage net-score hit.
    """
    seen: Counter[str] = Counter()
    out: list[CandidateFeatures] = []
    for c in candidates:
        seen[c.artist] += 1
        n = seen[c.artist]
        diversity = max(0.0, 1.0 - 0.75 * (n - 1))
        out.append(replace(c, diversity=round(diversity, 4)))
    return out
