from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, ARRAY, JSON
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
        UniqueConstraint("user_id", "provider", "provider_track_id", "played_at", name="uq_listen_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    provider_track_id: Mapped[str] = mapped_column(String(120))
    played_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"), index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    track: Mapped[Track] = relationship("Track")


class FeedbackEvent(Base):
    __tablename__ = "feedback_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    weight: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class Lane(Base):
    __tablename__ = "lanes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    lane_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(600), default="")
    contexts: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}", default=list)
    top_artists: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}", default=list)
    top_tags: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}", default=list)
    energy_profile: Mapped[dict] = mapped_column(JSON, server_default="{}", default=dict)
    languages: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}", default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_type: Mapped[str] = mapped_column(String(60), default="daily", index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_jobs: Mapped[int] = mapped_column(Integer)
    completed_jobs: Mapped[int] = mapped_column(Integer, default=0)
    failed_jobs: Mapped[int] = mapped_column(Integer, default=0)
    contexts: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}", default=list)
    top_artists: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}", default=list)
    top_tags: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}", default=list)
    energy_profile: Mapped[dict] = mapped_column(JSON, server_default="{}", default=dict)
    languages: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}", default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
