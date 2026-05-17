"""Lane extraction engine.

Extracts Shane's listening "lanes" from session data using a two-phase approach:
1. Rule-based extraction (works immediately with any data volume)
2. ML-based clustering (UMAP + HDBSCAN) when sufficient data exists
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Lane, Listen, Track
from backend.config import get_settings

logger = logging.getLogger(__name__)


class LaneProfile:
    """A user's listening lane."""

    def __init__(
        self,
        lane_id: int,
        user_id: str,
        lane_name: str,
        description: str,
        contexts: list[str],
        top_artists: list[str],
        top_tags: list[str],
        energy_profile: dict[str, float],
        languages: list[str],
        confidence: float,
    ):
        self.lane_id = lane_id
        self.user_id = user_id
        self.lane_name = lane_name
        self.description = description
        self.contexts = contexts
        self.top_artists = top_artists
        self.top_tags = top_tags
        self.energy_profile = energy_profile
        self.languages = languages
        self.confidence = confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane_id": self.lane_id,
            "user_id": self.user_id,
            "lane_name": self.lane_name,
            "description": self.description,
            "contexts": self.contexts,
            "top_artists": self.top_artists,
            "top_tags": self.top_tags,
            "energy_profile": self.energy_profile,
            "languages": self.languages,
            "confidence": self.confidence,
        }


def _extract_lane_name(contexts: list[str], top_artists: list[str], top_tags: list[str]) -> str:
    """Generate a human-readable lane name from features."""
    parts = []

    # Add context-based prefix
    time_contexts = [c for c in contexts if "morning" in c or "late night" in c or "evening" in c or "afternoon" in c]
    if time_contexts:
        time_label = time_contexts[0].split("_")[0]
        parts.append(time_label)

    # Add genre/tag-based descriptor
    if top_tags:
        # Pick the most dominant tag
        parts.append(top_tags[0])
    elif top_artists:
        # Use top artist as descriptor
        parts.append(top_artists[0].split()[-1] if len(top_artists[0].split()) > 1 else top_artists[0])

    # Add mood/energy descriptor
    if "late night" in str(contexts):
        parts.append("Reflective")
    elif "morning" in str(contexts):
        parts.append("Calm")
    elif "evening" in str(contexts):
        parts.append("Chill")
    elif "weekend" in str(contexts):
        parts.append("Relaxed")
    else:
        parts.append("Focused")

    return " " + " ".join(parts) + " "


def _extract_lane_description(
    lane_name: str,
    contexts: list[str],
    top_artists: list[str],
    top_tags: list[str],
) -> str:
    """Generate a human-readable lane description."""
    time_contexts = [c for c in contexts if "morning" in c or "late night" in c or "evening" in c or "afternoon" in c]
    day_contexts = [c for c in contexts if "weekday" in c or "weekend" in c]

    time_str = time_contexts[0] if time_contexts else "various times"
    day_str = day_contexts[0] if day_contexts else "various days"

    artists_str = ", ".join(top_artists[:3]) if top_artists else "various artists"

    return (
        f"A {lane_name} lane. "
        f"Typically listened to {time_str} on {day_str}. "
        f"Featuring {artists_str}."
    )


def _compute_energy_profile(contexts: list[str], top_artists: list[str]) -> dict[str, float]:
    """Compute a simple energy profile based on time context and artist patterns."""
    energy = {
        "calm": 0.0,
        "moderate": 0.0,
        "energetic": 0.0,
        "intense": 0.0,
    }

    # Time-based energy heuristics
    late_night_count = sum(1 for c in contexts if "late night" in c)
    morning_count = sum(1 for c in contexts if "morning" in c)
    evening_count = sum(1 for c in contexts if "evening" in c)
    total = max(1, len(contexts))

    if late_night_count / total > 0.3:
        energy["calm"] = 0.8
        energy["moderate"] = 0.2
    elif morning_count / total > 0.3:
        energy["calm"] = 0.6
        energy["moderate"] = 0.4
    elif evening_count / total > 0.3:
        energy["moderate"] = 0.5
        energy["energetic"] = 0.3
    else:
        energy["moderate"] = 0.6
        energy["energetic"] = 0.3

    return energy


def _extract_languages(top_artists: list[str]) -> list[str]:
    """Heuristically extract likely languages from artist names."""
    languages = set()

    # Simple heuristics for Vietnamese, Korean, Japanese, Mandarin
    vi_keywords = ["viet", "vietnamese", "vpop", "v-pop", "nguyen", "tran", "pham", "le", "huynh", "hoang", "nguyen thi", "nguyen van"]
    kr_keywords = ["kpop", "korean", "park", "kim", "lee", "choi", "jung", "kang", "song", "yoon"]
    jp_keywords = ["jpop", "japanese", "tanaka", "suzuki", "yamamoto", "watanabe", "nakamura"]
    cn_keywords = ["mandopop", "mandarin", "wang", "li", "zhang", "chen", "yang"]

    for artist in top_artists:
        artist_lower = artist.lower()
        if any(k in artist_lower for k in vi_keywords):
            languages.add("vietnamese")
        if any(k in artist_lower for k in kr_keywords):
            languages.add("korean")
        if any(k in artist_lower for k in jp_keywords):
            languages.add("japanese")
        if any(k in artist_lower for k in cn_keywords):
            languages.add("mandarin")

    return sorted(languages) if languages else ["unknown"]


def extract_lanes(
    db: Session,
    user_id: str,
    since: datetime | None = None,
) -> list[LaneProfile]:
    """Extract listening lanes from the user's listen history.

    Uses a rule-based approach that works with any data volume:
    1. Group listens by time context (morning/afternoon/evening/late night)
    2. Cluster by dominant artist patterns
    3. Generate human-readable lane names and descriptions
    """
    # Get all listens for the user
    query = select(Listen).where(Listen.user_id == user_id)
    if since:
        query = query.where(Listen.played_at >= since)
    query = query.order_by(Listen.played_at)

    result = db.execute(query)
    listens = result.scalars().all()

    if not listens:
        logger.info(f"Lane extraction: no listens for user={user_id}")
        return []

    # Group by time context
    context_groups: dict[str, list[Any]] = {
        "morning_weekday": [],
        "morning_weekend": [],
        "afternoon_weekday": [],
        "afternoon_weekend": [],
        "evening_weekday": [],
        "evening_weekend": [],
        "late_night_weekday": [],
        "late_night_weekend": [],
    }

    for listen in listens:
        hour = listen.played_at.hour
        day_type = "weekday" if listen.played_at.weekday() < 5 else "weekend"

        if 5 <= hour < 10:
            time_label = "morning"
        elif 10 <= hour < 17:
            time_label = "afternoon"
        elif 17 <= hour < 21:
            time_label = "evening"
        else:
            time_label = "late_night"

        key = f"{time_label}_{day_type}"
        if key in context_groups:
            context_groups[key].append(listen)

    # Extract lanes from groups with sufficient data (>= 5 listens)
    lanes: list[LaneProfile] = []
    lane_counter = 1

    for context, group_listens in context_groups.items():
        if len(group_listens) < 5:
            continue

        # Get top artists for this context
        artist_counts: Counter = Counter()
        for listen in group_listens:
            if listen.track:
                artist_counts[listen.track.artist] += 1
        top_artists = [artist for artist, _ in artist_counts.most_common(10)]

        # Get top tags (from track metadata - tags field not yet populated)
        # Will be populated when MusicBrainz enrichment is wired up
        top_tags: list[str] = []

        # Extract languages
        languages = _extract_languages(top_artists)

        # Compute energy profile
        energy_profile = _compute_energy_profile([context], top_artists)

        # Generate lane name and description
        lane_name = _extract_lane_name([context], top_artists, top_tags)
        description = _extract_lane_description(lane_name, [context], top_artists, top_tags)

        # Confidence based on data volume
        confidence = min(1.0, len(group_listens) / 20.0)

        lane = LaneProfile(
            lane_id=lane_counter,
            user_id=user_id,
            lane_name=lane_name,
            description=description,
            contexts=[context],
            top_artists=top_artists,
            top_tags=top_tags,
            energy_profile=energy_profile,
            languages=languages,
            confidence=round(confidence, 2),
        )
        lanes.append(lane)
        lane_counter += 1

    # Sort by confidence (most confident first)
    lanes.sort(key=lambda l: l.confidence, reverse=True)

    logger.info(f"Lane extraction: found {len(lanes)} lanes for user={user_id}")
    return lanes


def save_lanes(db: Session, user_id: str, lanes: list[LaneProfile]) -> None:
    """Persist extracted lanes to the database."""
    # Clear existing lanes for this user
    db.execute(
        Lane.__table__.delete().where(Lane.user_id == user_id)
    )
    db.commit()

    # Insert new lanes
    for lane in lanes:
        db_lane = Lane(
            user_id=user_id,
            lane_name=lane.lane_name,
            description=lane.description,
            contexts=lane.contexts,
            top_artists=lane.top_artists,
            top_tags=lane.top_tags,
            energy_profile=lane.energy_profile,
            languages=lane.languages,
            confidence=lane.confidence,
        )
        db.add(db_lane)

    db.commit()
    logger.info(f"Saved {len(lanes)} lanes for user={user_id}")
