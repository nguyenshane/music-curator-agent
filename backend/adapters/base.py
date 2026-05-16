from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from backend.adapters.types import ListenEvent


class ListeningHistoryAdapter(ABC):
    provider_name: str

    @abstractmethod
    def fetch_listens(self, *, user_id: str, since: datetime | None = None) -> list[ListenEvent]:
        raise NotImplementedError
