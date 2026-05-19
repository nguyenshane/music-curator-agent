"""Daily DAG executor.

Runs each stage from `DAILY_JOB_DAG` with per-stage failure isolation and
persisted state. A single stage failure does not abort the run — subsequent
stages still attempt to execute, and the JobRun is marked `partial` if any
stage failed.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from backend.adapters.registry import ProviderRegistry
from backend.db.models import JobRun, JobStageRun
from backend.ingestion import ingest_listening_history
from backend.observability import log_event
from backend.scheduler.dag import DAILY_JOB_DAG

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobResult:
    job_name: str
    status: str
    counts: dict[str, Any] | None = None
    error: str | None = None


# Type alias: stage callables receive (db, run, user_id) and return a counts dict.
StageFn = Callable[[Session, JobRun, str], dict[str, Any]]


# ── Stage implementations ────────────────────────────────────────────


def _stage_ingestion_sync(db: Session, run: JobRun, user_id: str) -> dict[str, Any]:
    """Fetch + ingest from every enabled provider with per-provider isolation."""
    registry = ProviderRegistry()
    totals = {"providers": 0, "ingested": 0, "deduped": 0, "fetch_errors": 0}

    for provider_name in registry.enabled_providers:
        adapter = registry.get_adapter(provider_name)
        if adapter is None:
            continue
        totals["providers"] += 1
        t0 = time.monotonic()
        try:
            stats = ingest_listening_history(
                db=db,
                adapter=adapter,
                user_id=user_id,
                since=run.source_window_start,
            )
            totals["ingested"] += stats["ingested"]
            totals["deduped"] += stats["deduped"]
            log_event(
                logger,
                "ingestion.provider.ok",
                run_id=run.id,
                stage="ingestion_sync",
                provider=provider_name,
                duration_ms=int((time.monotonic() - t0) * 1000),
                counts=stats,
            )
        except Exception as e:  # noqa: BLE001 — per-provider isolation
            totals["fetch_errors"] += 1
            log_event(
                logger,
                "ingestion.provider.error",
                level=logging.ERROR,
                run_id=run.id,
                stage="ingestion_sync",
                provider=provider_name,
                duration_ms=int((time.monotonic() - t0) * 1000),
                status="failed",
                error=str(e),
            )
    return totals


def _stage_session_build(db: Session, run: JobRun, user_id: str) -> dict[str, Any]:
    from backend.sessions import build_sessions

    sessions = build_sessions(db, user_id, since=run.source_window_start)
    return {"sessions": len(sessions)}


def _stage_lane_update(db: Session, run: JobRun, user_id: str) -> dict[str, Any]:
    from backend.lane_extraction import extract_lanes, save_lanes

    lanes = extract_lanes(db, user_id, since=run.source_window_start)
    save_lanes(db, user_id, lanes)
    return {"lanes": len(lanes)}


def _stage_recommendation_scoring(db: Session, run: JobRun, user_id: str) -> dict[str, Any]:
    """Generate and persist today's playlist from ingested listens."""
    from backend.recommendation.playlist import generate_playlist

    result = generate_playlist(db, user_id, limit=20)
    return {
        "items": len(result["items"]),
        "context": result["context"],
    }


def _stage_noop(_name: str) -> StageFn:
    """Placeholder stage that succeeds with no work — for DAG entries that
    aren't implemented yet (metadata_enrichment, candidate_discovery, etc.).
    Keeping them in the DAG preserves slot ordering for future work.
    """
    def _run(_db: Session, _run: JobRun, _user_id: str) -> dict[str, Any]:
        return {"note": "not_implemented"}
    return _run


STAGE_FNS: dict[str, StageFn] = {
    "ingestion_sync": _stage_ingestion_sync,
    "metadata_enrichment": _stage_noop("metadata_enrichment"),
    "session_build": _stage_session_build,
    "lane_update": _stage_lane_update,
    "candidate_discovery": _stage_noop("candidate_discovery"),
    "recommendation_scoring": _stage_recommendation_scoring,
    "playlist_publish": _stage_noop("playlist_publish"),
    "feedback_scan": _stage_noop("feedback_scan"),
}


# ── Public API ───────────────────────────────────────────────────────


def run_dry_daily_jobs() -> list[JobResult]:
    """Dry-run executor for Hermes smoke tests (no DB writes)."""
    return [JobResult(job_name=name, status="dry_run_ok") for name in DAILY_JOB_DAG]


def run_daily_jobs(
    *,
    db: Session,
    user_id: str = "shane",
    stage_fns: dict[str, StageFn] | None = None,
) -> tuple[JobRun, list[JobResult]]:
    """Execute the daily DAG with per-stage failure isolation.

    `stage_fns` override is for tests to inject stage failures without
    actually running real ingestion.
    """
    fns = stage_fns if stage_fns is not None else STAGE_FNS

    run = JobRun(
        status="running",
        total_jobs=len(DAILY_JOB_DAG),
        completed_jobs=0,
        failed_jobs=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    log_event(logger, "run.start", run_id=run.id, status="running")

    results: list[JobResult] = []
    for stage_name in DAILY_JOB_DAG:
        stage = JobStageRun(
            run_id=run.id,
            stage_name=stage_name,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        db.add(stage)
        db.commit()
        db.refresh(stage)

        t0 = time.monotonic()
        try:
            stage_fn = fns.get(stage_name, _stage_noop(stage_name))
            counts = stage_fn(db, run, user_id)
            stage.status = "succeeded"
            stage.counts = counts or {}
            results.append(JobResult(job_name=stage_name, status="ok", counts=counts))
            log_event(
                logger,
                "stage.ok",
                run_id=run.id,
                stage=stage_name,
                status="succeeded",
                duration_ms=int((time.monotonic() - t0) * 1000),
                counts=counts,
            )
        except Exception as e:  # noqa: BLE001 — per-stage isolation
            db.rollback()
            stage = db.get(JobStageRun, stage.id) or stage  # rehydrate after rollback
            stage.status = "failed"
            stage.error = str(e)
            results.append(JobResult(job_name=stage_name, status="failed", error=str(e)))
            log_event(
                logger,
                "stage.error",
                level=logging.ERROR,
                run_id=run.id,
                stage=stage_name,
                status="failed",
                duration_ms=int((time.monotonic() - t0) * 1000),
                error=str(e),
            )
        finally:
            stage.finished_at = datetime.now(timezone.utc)
            stage.duration_ms = int((time.monotonic() - t0) * 1000)
            db.commit()

    run.completed_jobs = sum(1 for r in results if r.status == "ok")
    run.failed_jobs = sum(1 for r in results if r.status != "ok")
    if run.failed_jobs == 0:
        run.status = "succeeded"
    elif run.completed_jobs == 0:
        run.status = "failed"
    else:
        run.status = "partial"
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)

    log_event(
        logger,
        "run.finish",
        run_id=run.id,
        status=run.status,
        counts={"completed": run.completed_jobs, "failed": run.failed_jobs},
    )
    return run, results
