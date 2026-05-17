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


def test_run_and_latest_run_endpoints():
    client = TestClient(app)

    run_response = client.post("/jobs/run")
    assert run_response.status_code == 200
    payload = run_response.json()
    assert payload["run"]["status"] == "succeeded"
    assert payload["run"]["total_jobs"] == 8
    assert payload["run"]["completed_jobs"] == 8
    assert payload["run"]["failed_jobs"] == 0
    assert all(item["status"] == "ok" for item in payload["results"])

    latest = client.get("/jobs/runs/latest")
    assert latest.status_code == 200
    latest_payload = latest.json()
    assert latest_payload["run"]["id"] == payload["run"]["id"]
    assert latest_payload["run"]["status"] == "succeeded"
