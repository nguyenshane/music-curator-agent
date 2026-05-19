"""Smoke + regression tests for the TIDAL auth helper routes.

The two bugs we are specifically guarding against:
  1. Stale client_id: settings were snapshotted at adapter construction
     and never refreshed, so an env change after import produced authorize
     URLs with the *old* client. The adapter now reads settings live and
     /auth/tidal/config exposes the value it would use.
  2. Lost PKCE state: a fresh adapter per request loses the code_verifier
     created at /authorize before /exchange runs. The route uses a
     module singleton via get_tidal_adapter().
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from backend.adapters import tidal as tidal_mod
from backend.api.main import app


@pytest.fixture(autouse=True)
def _tidal_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIDAL_CLIENT_ID", "client_AAA")
    monkeypatch.setenv("TIDAL_CLIENT_SECRET", "secret_AAA")
    # Reset the singleton so state from prior tests doesn't bleed in.
    tidal_mod._singleton = None
    yield
    tidal_mod._singleton = None


def test_config_returns_live_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    first = client.get("/auth/tidal/config").json()
    assert first["client_id"] == "client_AAA"
    assert first["client_id_configured"] is True
    assert first["client_secret_configured"] is True
    assert first["scopes"] == list(tidal_mod.TIDAL_SCOPES)

    # Simulate a runtime env change (the original bug source).
    monkeypatch.setenv("TIDAL_CLIENT_ID", "client_BBB")
    second = client.get("/auth/tidal/config").json()
    assert second["client_id"] == "client_BBB", "settings must be read live, not cached"


def test_authorize_then_status_uses_same_singleton() -> None:
    """PKCE state created at /authorize must be visible at /status (same singleton)."""
    client = TestClient(app)

    auth = client.post(
        "/auth/tidal/authorize",
        json={"user_id": "shane", "redirect_uri": "https://nguyenshane.com/tidal/"},
    ).json()
    parsed = urlparse(auth["authorize_url"])
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["client_AAA"]
    assert qs["redirect_uri"] == ["https://nguyenshane.com/tidal/"]
    assert qs["code_challenge_method"] == ["S256"]
    assert auth["state"] == qs["state"][0]

    status = client.get("/auth/tidal/status", params={"user_id": "shane"}).json()
    assert status["has_pkce_state"] is True
    assert status["has_tokens"] is False
    assert status["state"] == auth["state"]


def test_reset_clears_pkce_state() -> None:
    client = TestClient(app)
    client.post("/auth/tidal/authorize", json={"user_id": "shane"})
    cleared = client.post("/auth/tidal/reset", json={"user_id": "shane"}).json()
    assert cleared == {"ok": True, "user_id": "shane", "cleared": True}

    after = client.get("/auth/tidal/status", params={"user_id": "shane"}).json()
    assert after["has_pkce_state"] is False


def test_exchange_without_authorize_returns_400() -> None:
    client = TestClient(app)
    resp = client.post(
        "/auth/tidal/exchange",
        json={"user_id": "ghost", "code": "x", "state": "y"},
    )
    assert resp.status_code == 400
    assert "PKCE state" in resp.json()["detail"]
