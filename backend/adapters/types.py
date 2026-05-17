from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ExternalTrackRef:
    provider: str
    track_id: str
    title: str
    artist: str
    album: str | None = None
    isrc: str | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class ListenEvent:
    provider: str
    played_at: datetime
    track: ExternalTrackRef
    user_id: str
