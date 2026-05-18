"""Pipeline entry point.

Consolidated into `backend.scheduler.runner.run_daily_jobs` which owns DAG
execution, per-stage state, and structured logging. Re-exported here so
`from backend.pipeline import run_daily_pipeline` keeps working as a thin
wrapper.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.db.models import JobRun
from backend.scheduler.runner import JobResult, run_daily_jobs

__all__ = ["run_daily_pipeline", "JobResult", "JobRun"]


def run_daily_pipeline(*, db: Session, user_id: str = "shane") -> tuple[JobRun, list[JobResult]]:
    """Run the full daily curator pipeline. See `run_daily_jobs`."""
    return run_daily_jobs(db=db, user_id=user_id)
