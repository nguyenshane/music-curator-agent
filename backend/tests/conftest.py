"""Shared test fixtures — patches session factories to use in-memory SQLite."""
from __future__ import annotations

import pytest

TEST_DB_URL = "sqlite+pysqlite:///:memory:"


@pytest.fixture(autouse=True)
def _patch_in_memory_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace cloud DB with in-memory SQLite for all tests."""
    from backend.db.models import Base
    from backend.db.session import build_session_factory

    factory = build_session_factory(TEST_DB_URL)
    Base.metadata.create_all(factory.kw["bind"])

    # Patch the session factories used by the route modules.
    import backend.api.routes.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "_session_factory", factory, raising=False)

    import backend.api.routes.playlists as playlists_mod
    monkeypatch.setattr(playlists_mod, "_session_factory", factory, raising=False)
