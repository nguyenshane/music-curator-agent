# Scaffold Plan

Initial scaffold added for:
- backend FastAPI service
- recommendation and feedback scoring primitives
- scheduler DAG definition
- database model base
- Hermes skill and MCP server placeholders

Next steps:
1. Add provider adapter interfaces and typed DTOs.
2. Add SQLAlchemy models for tracks/listens/sessions/lanes.
3. Add ingestion pipeline services and tests.
4. Add APScheduler/Celery job runner wiring.
