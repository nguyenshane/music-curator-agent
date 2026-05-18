"""Scheduler partial-failure path: one stage raising must not abort the run."""
from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.db.models import Base, JobRun, JobStageRun
from backend.scheduler.dag import DAILY_JOB_DAG
from backend.scheduler.runner import run_daily_jobs


def _fresh_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_partial_run_on_single_stage_failure():
    def ok(_db: Any, _run: Any, _u: str) -> dict[str, Any]:
        return {"did": "work"}

    def boom(_db: Any, _run: Any, _u: str) -> dict[str, Any]:
        raise RuntimeError("kaboom")

    # Make the third stage in the DAG fail; others succeed.
    failing_stage = DAILY_JOB_DAG[2]
    stage_fns = {name: (boom if name == failing_stage else ok) for name in DAILY_JOB_DAG}

    with _fresh_db() as db:
        run, results = run_daily_jobs(db=db, user_id="u1", stage_fns=stage_fns)

    assert run.status == "partial"
    assert run.failed_jobs == 1
    assert run.completed_jobs == len(DAILY_JOB_DAG) - 1

    failed = [r for r in results if r.status != "ok"]
    assert [r.job_name for r in failed] == [failing_stage]
    assert "kaboom" in (failed[0].error or "")

    # Stage rows are persisted with the right statuses.
    with Session(db.bind) as q:
        stage_rows = q.scalars(select(JobStageRun).where(JobStageRun.run_id == run.id)).all()
        statuses = {s.stage_name: s.status for s in stage_rows}
    assert statuses[failing_stage] == "failed"
    for name in DAILY_JOB_DAG:
        if name != failing_stage:
            assert statuses[name] == "succeeded", f"{name}: {statuses[name]}"


def test_all_stages_failed_marks_run_failed():
    def boom(_db: Any, _run: Any, _u: str) -> dict[str, Any]:
        raise RuntimeError("nope")

    stage_fns = {name: boom for name in DAILY_JOB_DAG}

    with _fresh_db() as db:
        run, _ = run_daily_jobs(db=db, user_id="u1", stage_fns=stage_fns)

    assert run.status == "failed"
    assert run.completed_jobs == 0
    assert run.failed_jobs == len(DAILY_JOB_DAG)
