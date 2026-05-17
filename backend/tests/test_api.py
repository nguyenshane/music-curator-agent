from fastapi.testclient import TestClient

from backend.api.main import app


def test_health_and_dry_run_endpoints():
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    dag = client.get("/jobs/dag")
    assert dag.status_code == 200
    assert len(dag.json()["daily_job_dag"]) == 8

    dry_run = client.post("/jobs/dry-run")
    assert dry_run.status_code == 200
    results = dry_run.json()["results"]
    assert len(results) == 8
    assert all(item["status"] == "dry_run_ok" for item in results)
