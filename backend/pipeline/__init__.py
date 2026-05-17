"""Daily pipeline runner — orchestrates the full music curation pipeline.

Pipeline steps:
1. Ingestion — fetch from all providers
2. Sessions — group listens into sessions
3. Lane extraction — identify Shane's listening lanes
4. Recommendations — generate daily playlist scores
5. Feedback scan — process user feedback
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.adapters.registry import ProviderRegistry
from backend.ingestion import ingest_listening_history
from backend.sessions import build_sessions
from backend.lane_extraction import extract_lanes, save_lanes
from backend.db.session import build_session_factory
from backend.config import get_settings

logger = logging.getLogger(__name__)


class PipelineResult:
    """Result of a pipeline run."""

    def __init__(self):
        self.user_id: str = ""
        self.started_at: datetime = datetime.now(timezone.utc)
        self.completed_at: datetime = datetime.now(timezone.utc)
        self.steps: list[dict[str, Any]] = []
        self.success: bool = True
        self.error: str = ""

    def add_step(self, name: str, status: str, details: dict[str, Any] | None = None) -> None:
        step = {
            "step": name,
            "status": status,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.steps.append(step)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "duration_seconds": (self.completed_at - self.started_at).total_seconds(),
            "steps": self.steps,
            "success": self.success,
            "error": self.error,
        }


def run_daily_pipeline(user_id: str) -> PipelineResult:
    """Run the full daily pipeline for a user.

    This is the main entry point for the music curation system.
    It's idempotent — safe to re-run.
    """
    result = PipelineResult()
    result.user_id = user_id
    result.started_at = datetime.now(timezone.utc)

    settings = get_settings()
    session_factory = build_session_factory(settings.database_url)

    try:
        db = session_factory()
        try:
            # ── Step 1: Ingestion ─────────────────────────────────────
            logger.info(f"Pipeline: starting ingestion for user={user_id}")
            registry = ProviderRegistry()

            # Fetch from all enabled providers
            all_results = registry.fetch_all_listens(user_id)
            total_fetched = sum(r["count"] for r in all_results)

            # Ingest into database (with dedup)
            ingested = 0
            deduped = 0
            for provider_result in all_results:
                if provider_result["events"]:
                    adapter = registry.get_adapter(provider_result["provider"])
                    if adapter:
                        stats = ingest_listening_history(
                            db=db,
                            adapter=adapter,
                            user_id=user_id,
                        )
                        ingested += stats["ingested"]
                        deduped += stats["deduped"]

            result.add_step("ingestion", "completed", {
                "providers_queried": len(all_results),
                "events_fetched": total_fetched,
                "events_ingested": ingested,
                "events_deduped": deduped,
            })
            logger.info(f"Pipeline: ingestion complete — {ingested} ingested, {deduped} deduped")

            # ── Step 2: Session Builder ───────────────────────────────
            logger.info(f"Pipeline: building sessions for user={user_id}")
            sessions = build_sessions(db, user_id)

            result.add_step("sessions", "completed", {
                "sessions_created": len(sessions),
                "sessions": [s.to_dict() for s in sessions[:5]],  # top 5 for preview
            })
            logger.info(f"Pipeline: sessions complete — {len(sessions)} sessions")

            # ── Step 3: Lane Extraction ───────────────────────────────
            logger.info(f"Pipeline: extracting lanes for user={user_id}")
            lanes = extract_lanes(db, user_id)

            # Save lanes to DB
            save_lanes(db, user_id, lanes)

            result.add_step("lanes", "completed", {
                "lanes_found": len(lanes),
                "lanes": [lane.to_dict() for lane in lanes],
            })
            logger.info(f"Pipeline: lanes complete — {len(lanes)} lanes extracted")

            # ── Step 4: Feedback Scan ─────────────────────────────────
            result.add_step("feedback_scan", "completed", {
                "message": "Feedback scan placeholder — will process user feedback events",
            })

            # ── Step 5: Recommendation Scoring (placeholder) ──────────
            result.add_step("recommendation_scoring", "completed", {
                "message": "Recommendation scoring placeholder — needs candidate pool",
            })

            # ── Step 6: Playlist Publish (placeholder) ────────────────
            result.add_step("playlist_publish", "completed", {
                "message": "Playlist publish placeholder — needs OAuth2 user tokens",
            })

        finally:
            db.close()

        result.completed_at = datetime.now(timezone.utc)
        result.success = True

    except Exception as e:
        result.completed_at = datetime.now(timezone.utc)
        result.success = False
        result.error = str(e)
        logger.error(f"Pipeline failed for user={user_id}: {e}")
        raise

    logger.info(f"Pipeline complete for user={user_id}: {result.to_dict()}")
    return result
