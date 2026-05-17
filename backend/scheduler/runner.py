from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.db.models import JobRun
from backend.scheduler.dag import DAILY_JOB_DAG


@dataclass(frozen=True)
class JobResult:
    job_name: str
    status: str


def run_dry_daily_jobs() -> list[JobResult]:
    """Dry-run executor for Hermes smoke tests."""
    return [JobResult(job_name=name, status="dry_run_ok") for name in DAILY_JOB_DAG]


def run_daily_jobs(*, db: Session) -> tuple[JobRun, list[JobResult]]:
    """Execute daily jobs and persist a run-state record.

    Current implementation is deterministic and non-destructive, but persists
    run metadata so orchestration state can be inspected and retried safely.
    """
    run = JobRun(status="running", total_jobs=len(DAILY_JOB_DAG), completed_jobs=0, failed_jobs=0)
    db.add(run)
    db.commit()
    db.refresh(run)

    results: list[JobResult] = []
    for name in DAILY_JOB_DAG:
        # Phase-2 starter behavior: run all steps as successful placeholders.
        results.append(JobResult(job_name=name, status="ok"))

    run.completed_jobs = len(results)
    run.failed_jobs = sum(1 for item in results if item.status != "ok")
    run.status = "succeeded" if run.failed_jobs == 0 else "partial"
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run, results
