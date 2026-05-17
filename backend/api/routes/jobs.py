from fastapi import APIRouter
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.db.models import Base, JobRun
from backend.db.session import build_session_factory
from backend.scheduler.dag import DAILY_JOB_DAG
from backend.scheduler.runner import run_daily_jobs, run_dry_daily_jobs

router = APIRouter()
_session_factory = build_session_factory(get_settings().database_url)


def _ensure_schema() -> None:
    bind = _session_factory.kw["bind"]
    Base.metadata.create_all(bind)


@router.get("/dag")
def dag() -> dict[str, list[str]]:
    return {"daily_job_dag": DAILY_JOB_DAG}


@router.post("/dry-run")
def dry_run() -> dict[str, list[dict[str, str]]]:
    results = run_dry_daily_jobs()
    return {"results": [{"job_name": item.job_name, "status": item.status} for item in results]}


@router.post("/run")
def run() -> dict[str, object]:
    _ensure_schema()
    with _session_factory() as db:  # type: Session
        run_row, results = run_daily_jobs(db=db)
    return {
        "run": {
            "id": run_row.id,
            "status": run_row.status,
            "total_jobs": run_row.total_jobs,
            "completed_jobs": run_row.completed_jobs,
            "failed_jobs": run_row.failed_jobs,
        },
        "results": [{"job_name": item.job_name, "status": item.status} for item in results],
    }


@router.get("/runs/latest")
def latest_run() -> dict[str, object]:
    _ensure_schema()
    with _session_factory() as db:  # type: Session
        latest = db.query(JobRun).order_by(JobRun.started_at.desc()).first()
    if latest is None:
        return {"run": None}
    return {
        "run": {
            "id": latest.id,
            "status": latest.status,
            "total_jobs": latest.total_jobs,
            "completed_jobs": latest.completed_jobs,
            "failed_jobs": latest.failed_jobs,
        }
    }
