from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.adapters.mock import MockAdapter
from backend.db.models import Base, Listen, Track
from backend.ingestion import ingest_listening_history


def test_ingestion_and_dedup():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        result_1 = ingest_listening_history(db=db, adapter=MockAdapter(), user_id="u1")
        assert result_1 == {"ingested": 2, "deduped": 0, "total": 2}

        result_2 = ingest_listening_history(db=db, adapter=MockAdapter(), user_id="u1")
        assert result_2 == {"ingested": 0, "deduped": 2, "total": 2}

        tracks = db.scalars(select(Track)).all()
        listens = db.scalars(select(Listen)).all()

        assert len(tracks) == 2
        assert len(listens) == 2
