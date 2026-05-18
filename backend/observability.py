"""Structured logging for DAG stages.

Emits a single log line per event with deterministic key=value ordering so
local logs are greppable and a downstream JSON formatter can pick the same
fields off the LogRecord via `extra=`.

Canonical fields (per the Phase 2 observability plan):
    run_id, stage, provider, duration_ms, counts, status, error
"""
from __future__ import annotations

import logging
from typing import Any

CANONICAL_FIELDS = ("run_id", "stage", "provider", "status", "duration_ms", "counts", "error")


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    run_id: int | None = None,
    stage: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    duration_ms: int | None = None,
    counts: dict[str, Any] | None = None,
    error: str | None = None,
    **extra: Any,
) -> None:
    """Emit a structured DAG event.

    The message is prefixed with `event=<event>` followed by the populated
    canonical fields, then any extras. Fields with None values are omitted.
    """
    fields: dict[str, Any] = {
        "run_id": run_id,
        "stage": stage,
        "provider": provider,
        "status": status,
        "duration_ms": duration_ms,
        "counts": counts,
        "error": error,
        **extra,
    }
    parts = [f"event={event}"]
    for key in CANONICAL_FIELDS:
        value = fields.pop(key, None)
        if value is None:
            continue
        parts.append(f"{key}={value!r}")
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value!r}")

    logger.log(level, " ".join(parts), extra={k: v for k, v in fields.items() if v is not None})
