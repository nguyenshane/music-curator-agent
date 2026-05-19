from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.adapters.mock import MockAdapter
from backend.db.models import Base, Listen, Track
from backend.ingestion import ingest_listening_history


def _fresh_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_ingestion_and_dedup():
    with _fresh_db() as db:
        result_1 = ingest_listening_history(db=db, adapter=MockAdapter(), user_id="u1")
        assert result_1 == {"ingested": 2, "deduped": 0, "total": 2}

        result_2 = ingest_listening_history(db=db, adapter=MockAdapter(), user_id="u1")
        assert result_2 == {"ingested": 0, "deduped": 2, "total": 2}

        tracks = db.scalars(select(Track)).all()
        listens = db.scalars(select(Listen)).all()

        assert len(tracks) == 2
        assert len(listens) == 2


def test_idempotent_with_since_watermark():
    """Second run with `since` past the first listen ingests only the newer one
    and avoids duplicates from overlap."""
    with _fresh_db() as db:
        first = ingest_listening_history(db=db, adapter=MockAdapter(), user_id="u1")
        assert first["ingested"] == 2

        # Simulate next daily run: pull only listens at or after the second listen.
        watermark = datetime(2026, 1, 1, 8, 4, tzinfo=timezone.utc)
        second = ingest_listening_history(
            db=db, adapter=MockAdapter(), user_id="u1", since=watermark
        )
        # Only the listen at 08:04 is in scope; it already exists → 0 ingested, 1 deduped.
        assert second == {"ingested": 0, "deduped": 1, "total": 1}

        listens = db.scalars(select(Listen)).all()
        assert len(listens) == 2  # unchanged
