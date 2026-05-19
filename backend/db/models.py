from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    artist: Mapped[str] = mapped_column(String(300))
    isrc: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Listen(Base):
    __tablename__ = "listens"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider", "provider_track_id", "played_at",
            name="uq_listen_event",
        ),
        # Composite index for time-window queries used by recommendation
        # candidate retrieval and incremental ingestion checkpoints.
        Index("ix_listen_user_played_at", "user_id", "played_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    provider_track_id: Mapped[str] = mapped_column(String(120))
    played_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"), index=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    track: Mapped[Track] = relationship("Track")


class FeedbackEvent(Base):
    __tablename__ = "feedback_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    weight: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


class Lane(Base):
    __tablename__ = "lanes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    lane_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(600), default="")
    # JSON columns work on both PostgreSQL and SQLite; the previous ARRAY(String)
    # form was Postgres-only and broke the in-memory test database.
    contexts: Mapped[list[str]] = mapped_column(JSON, default=list)
    top_artists: Mapped[list[str]] = mapped_column(JSON, default=list)
    top_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    energy_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    languages: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ── Orchestration / run state ─────────────────────────────────────────


class JobRun(Base):
    """Persistent record of a daily DAG execution.

    Status transitions: pending → running → (succeeded | partial | failed).
    `partial` means at least one stage succeeded and at least one failed.
    """

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_type: Mapped[str] = mapped_column(String(60), default="daily", index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_jobs: Mapped[int] = mapped_column(Integer)
    completed_jobs: Mapped[int] = mapped_column(Integer, default=0)
    failed_jobs: Mapped[int] = mapped_column(Integer, default=0)
    # Time window the run pulled data for (None = since last checkpoint).
    source_window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    stages: Mapped[list["JobStageRun"]] = relationship(
        "JobStageRun",
        back_populates="run",
        cascade="all, delete-orphan",
    )


class JobStageRun(Base):
    """Per-stage record within a JobRun.

    Captures status, duration, structured counts (e.g. ingested/deduped), and
    error text so failures are isolated and re-runnable per stage.
    """

    __tablename__ = "job_stage_runs"
    __table_args__ = (
        Index("ix_stage_run_id_stage", "run_id", "stage_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("job_runs.id"), index=True)
    stage_name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[JobRun] = relationship("JobRun", back_populates="stages")
