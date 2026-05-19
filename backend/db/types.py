"""Custom SQLAlchemy types.

`UtcDateTime` guarantees datetimes are stored *and read back* as tz-aware
UTC. SQLite silently drops timezone info even on `DateTime(timezone=True)`
columns, so values read back through the ORM are naive and break any
arithmetic against `datetime.now(timezone.utc)`. This decorator re-tags
naive results as UTC at the read boundary so callers never have to
remember.

Use it everywhere we previously wrote `DateTime(timezone=True)`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """A `DateTime(timezone=True)` that round-trips tz-aware UTC reliably.

    - On bind: accepts naive (assumed UTC) or tz-aware datetimes; normalizes
      to UTC before handing to the dialect.
    - On result: tags naive values returned by the driver as UTC; leaves
      already-aware values unchanged but converts them to UTC for
      consistency.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, datetime):
            return value
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, datetime):
            return value
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
