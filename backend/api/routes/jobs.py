from fastapi import APIRouter

from backend.scheduler.dag import DAILY_JOB_DAG
from backend.scheduler.runner import run_dry_daily_jobs

router = APIRouter()


@router.get("/dag")
def dag() -> dict[str, list[str]]:
    return {"daily_job_dag": DAILY_JOB_DAG}


@router.post("/dry-run")
def dry_run() -> dict[str, list[dict[str, str]]]:
    results = run_dry_daily_jobs()
    return {"results": [{"job_name": item.job_name, "status": item.status} for item in results]}
