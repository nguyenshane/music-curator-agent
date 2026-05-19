from fastapi.testclient import TestClient
import sys
import tempfile
import pytest


# Module-level temp DB so both tests in this file share the same database.
_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_test_db_url = f"sqlite:///{_db.name}"


def _patched_app(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Create an app instance patched with a shared in-memory SQLite session factory."""
    from backend.db.models import Base
    from backend.db.session import build_session_factory
    from backend.config import Settings

    # Create test settings with the shared temp DB.
    test_settings = Settings(
        app_env="test",
        log_level="DEBUG",
        database_url=_test_db_url,
        spotify_client_id=None,
        spotify_client_secret=None,
        lastfm_api_key=None,
        lastfm_user=None,
        tidal_client_id=None,
        tidal_client_secret=None,
        ytmusic_oauth_token=None,
        musicbrainz_user_agent=None,
    )

    # Patch get_settings in backend.config (where it's defined).
    import backend.config as config_mod
    monkeypatch.setattr(config_mod, "get_settings", lambda: test_settings)

    # Clear cached backend.api modules so they re-import with our patched settings.
    for mod in list(sys.modules):
        if mod.startswith("backend.api."):
            del sys.modules[mod]

    # Now import app — it will import jobs, which calls get_settings() 
    # and creates _session_factory with our temp DB.
    from backend.api.main import app
    client = TestClient(app)

    # Create tables.
    factory = build_session_factory(_test_db_url)
    Base.metadata.create_all(factory.kw["bind"])

    return client


def test_health_and_dry_run_endpoints(monkeypatch: pytest.MonkeyPatch):
    client = _patched_app(monkeypatch)

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


def test_run_and_latest_run_endpoints(monkeypatch: pytest.MonkeyPatch):
    client = _patched_app(monkeypatch)

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
