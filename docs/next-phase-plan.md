# Next Phase Plan (Phase 2: Real Data + Daily Operations)

This phase converts the current scaffold/dry-run baseline into a production-like local system that ingests real listening history, computes lanes, and publishes daily recommendations with measurable reliability.

## Phase Objective

Ship an end-to-end **daily curator loop** with:
- Real provider ingestion (at least one live provider enabled)
- Idempotent orchestration with persisted run states
- Context/lane-aware recommendation candidates
- Basic observability and operator runbooks

## Exit Criteria

Phase 2 is complete when all of the following are true:
1. At least one provider adapter (Spotify or Last.fm) performs successful incremental ingestion in local/staging.
2. Daily DAG runs on schedule and can be safely re-run without duplicate writes.
3. Recommendation endpoint can return context-aware candidates sourced from ingested data (not only static examples).
4. Core operational metrics and structured logs are emitted for every DAG stage.
5. CI tests cover ingestion idempotency, scoring regression, and API contract smoke tests.

---

## Workstreams

## 1) Provider Integrations (P0)

**Goal:** Move from mock-only ingestion to at least one live source.

### Tasks
- Implement Last.fm adapter fetch + pagination + retry strategy.
- Implement Spotify recent plays adapter (token flow + refresh handling).
- Normalize provider DTOs into canonical `TrackListenEvent` objects.
- Add provider capability flags and per-provider health summary endpoint.

### Deliverables
- Live adapter module(s) under `backend/adapters/*`.
- Deterministic normalization tests with recorded fixtures.
- Provider ingestion smoke command/runbook.

### Risks / Mitigations
- **Risk:** API limits / transient failures.
  - **Mitigation:** bounded retries + exponential backoff + per-source checkpointing.

---

## 2) Data Model Hardening (P0)

**Goal:** Ensure database schema cleanly supports incremental sync and lineage.

### Tasks
- Add/verify unique constraints for listens dedup keys.
- Persist ingestion run metadata (source window, counts, status, errors).
- Add indexes for time-window queries used by recommendations.
- Add migration(s) for missing production fields in `Track` and `Listen`.

### Deliverables
- Migration scripts and model updates.
- Idempotency validation tests.

---

## 3) Orchestration + Scheduling (P0)

**Goal:** Turn dry-run DAG into real executable jobs with safe state transitions.

### Tasks
- Add persisted run-state machine (`pending/running/succeeded/failed/partial`).
- Wire scheduler runner to execute real stage functions.
- Add failure isolation so one source failure doesn’t corrupt whole run.
- Add re-run command for failed window with idempotent semantics.

### Deliverables
- Scheduler integration tests for success/failure/retry paths.
- Operator docs for retrying failed runs.

---

## 4) Recommendation Pipeline v1.5 (P1)

**Goal:** Use ingested history to build candidate sets and lane/context weighting.

### Tasks
- Add sessionization primitive for recent listening windows.
- Build lane affinity scoring inputs from feedback + recent sessions.
- Implement candidate retrieval from local catalog/listens history.
- Keep deterministic scoring but feed with real candidate feature values.

### Deliverables
- Expanded scoring tests with fixture-based expected outputs.
- `/recommendations/score` or successor endpoint returning trace fields for explainability.

---

## 5) Observability + Reliability Guardrails (P1)

**Goal:** Make jobs diagnosable and verifiable.

### Tasks
- Emit structured logs with `run_id`, `stage`, `provider`, `duration_ms`, `counts`.
- Add baseline metrics for ingestion and scoring latency/error rates.
- Add alerts/threshold checks in test harness for obvious regressions.
- Add data quality checks (duplicate listen rate, null key-field rate).

### Deliverables
- Logging + metrics instrumentation.
- `docs/operations.md` with runbook steps.

---

## Suggested Execution Sequence (4 Weeks)

### Week 1
- Provider adapter (Last.fm first) + normalization tests.
- DB schema hardening for idempotent incremental sync.

### Week 2
- Scheduler real execution + persisted run states.
- End-to-end ingestion from one live provider.

### Week 3
- Candidate retrieval + lane/context input computation.
- Deterministic recommendation regression suite.

### Week 4
- Observability hardening + operator runbook.
- Stabilization, bug fixes, and acceptance verification.

---

## Definition of Ready for Phase 3

Before starting Phase 3 (multi-provider expansion + advanced discovery), confirm:
- Phase 2 exit criteria all pass for 7 consecutive daily runs.
- No unresolved P0 defects in ingestion/scheduler/recommendation path.
- Baseline quality report generated and archived.
