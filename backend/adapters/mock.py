from __future__ import annotations

from datetime import datetime, timezone

from backend.adapters.base import ListeningHistoryAdapter
from backend.adapters.types import ExternalTrackRef, ListenEvent


class MockAdapter(ListeningHistoryAdapter):
    provider_name = "mock"

    def fetch_listens(self, *, user_id: str, since: datetime | None = None) -> list[ListenEvent]:
        seed = [
            ListenEvent(
                provider=self.provider_name,
                user_id=user_id,
                played_at=datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc),
                track=ExternalTrackRef(
                    provider=self.provider_name,
                    track_id="t1",
                    title="Sunrise Piano",
                    artist="Calm Unit",
                    isrc="ISRC000001",
                    duration_ms=180_000,
                ),
            ),
            ListenEvent(
                provider=self.provider_name,
                user_id=user_id,
                played_at=datetime(2026, 1, 1, 8, 4, tzinfo=timezone.utc),
                track=ExternalTrackRef(
                    provider=self.provider_name,
                    track_id="t2",
                    title="Focus Loop",
                    artist="Code Ensemble",
                    duration_ms=200_000,
                ),
            ),
        ]
        if since is None:
            return seed
        return [event for event in seed if event.played_at >= since]
