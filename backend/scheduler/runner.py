from __future__ import annotations

from dataclasses import dataclass

from backend.scheduler.dag import DAILY_JOB_DAG


@dataclass(frozen=True)
class JobResult:
    job_name: str
    status: str


def run_dry_daily_jobs() -> list[JobResult]:
    """Dry-run executor for Hermes smoke tests."""
    return [JobResult(job_name=name, status="dry_run_ok") for name in DAILY_JOB_DAG]
