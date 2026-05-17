# PRD.md — Shane Music Curator Agent

## Overview

Build a personal AI music curator system that:

- Learns the user’s music taste over time
- Automatically discovers new music
- Understands contextual listening patterns
- Generates personalized playlists daily
- Syncs playlists to Spotify, TIDAL, and later YouTube Music
- Learns continuously from user feedback and listening behavior
- Uses Hermes Agent as orchestration + memory layer
- Uses deterministic recommendation pipelines underneath

The system should feel like:

> “A deeply personalized music curator that understands my emotional/music identity better over time.”

---

## Goals

### Primary Goals

1. Build context-aware personalized playlist generation
2. Learn listening “lanes” automatically from listening history
3. Continuously improve recommendations from feedback
4. Support multi-platform music providers
5. Build local-first architecture
6. Avoid generic recommendation engine behavior

---

## Non-Goals

### Out of Scope v1

- Real-time DJ transitions
- Voice assistant
- Mobile app
- Social sharing
- Collaborative playlists
- Full streaming playback control
- Complex RL/reinforcement learning
- Training custom music embedding models

---

## High-Level Architecture

```text
                ┌────────────────────┐
                │ Hermes Agent       │
                │ (orchestrator)     │
                └─────────┬──────────┘
                          │
          ┌───────────────┼────────────────┐
          │               │                │
          ▼               ▼                ▼

┌────────────────┐ ┌────────────────┐ ┌────────────────┐
│ Recommendation │ │ Context Engine │ │ Feedback Engine│
│ Engine         │ │                │ │                │
└────────┬───────┘ └────────┬───────┘ └────────┬───────┘
         │                  │                  │
         └──────────────────┼──────────────────┘
                            ▼

                ┌────────────────────┐
                │ Unified Music DB   │
                │ Postgres + pgvector│
                └─────────┬──────────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼

┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Spotify      │ │ TIDAL        │ │ YouTubeMusic │
│ Adapter      │ │ Adapter      │ │ Adapter      │
└──────────────┘ └──────────────┘ └──────────────┘
```

---

## Core Product Concepts

### 1. Listening Lanes

A “lane” is a contextual listening identity.

Lanes are NOT genres.

Examples:

- Weekend Slow Morning
- Late Night Reflective Mandopop
- Focused Coding Instrumentals
- Confident Gym Energy
- Emotional Vietnamese Ballads
- Travel / Airport Mood
- Rainy-Day Piano

Lanes should be:

- Automatically inferred
- Continuously updated
- Explainable by Hermes

### 2. Context Awareness

The system should factor in:

#### Time

- Morning
- Afternoon
- Evening
- Late night

#### Day Type

- Weekday
- Weekend
- Holiday
- Vacation/travel

#### Activity (future)

- Coding
- Gym
- Driving
- Relaxing
- Focus

#### Optional Future Inputs

- Weather
- Calendar
- Home Assistant
- Location
- Sleep data

### 3. Feedback Learning

The system must learn from:

#### Explicit Feedback

Positive:

- Song liked
- Song saved
- Song added to Approved playlist
- Song replayed
- Song moved into permanent playlists

Negative:

- Song skipped quickly
- Song removed
- Song added to Rejected playlist
- User commands:
  - /too-hype
  - /too-generic
  - /too-sad
  - /not-my-vpop

#### Implicit Feedback

- Completion rate
- Replay frequency
- Time spent
- Session continuity

---

## Functional Requirements

### FR-1 Import Listening History

#### Sources

Required v1:

- Last.fm
- Spotify

Optional v2:

- TIDAL
- YouTube Music

#### Requirements

- Import full listening history
- Support incremental sync
- Normalize metadata
- Deduplicate tracks

#### Acceptance Criteria

- Initial import ingests at least 95% of provider-available plays for a test account.
- Incremental sync supports idempotent reruns (same sync window produces no duplicate listens).
- Daily sync success rate >= 99% (excluding provider outages), with automated retries.
- Duplicate rate in `listens` table remains <= 0.5% after dedup pass.
- Required metrics emitted: `sync_duration_ms`, `plays_ingested`, `plays_deduped`, `sync_failures`.

---

### FR-2 Unified Music Metadata Layer

Each track must support:

```json
{
  "track_id": "",
  "title": "",
  "artist": "",
  "album": "",
  "isrc": "",
  "spotify_id": "",
  "tidal_id": "",
  "youtube_music_id": "",
  "musicbrainz_id": "",
  "language": "",
  "genres": [],
  "tags": [],
  "release_date": "",
  "duration_ms": 0,
  "identity_confidence": 0.0,
  "identity_match_reason": ""
}
```

#### Canonical Identity & Dedup Rules

Matching precedence:

1. ISRC exact match
2. MusicBrainz recording ID exact match
3. Normalized title + normalized primary artist + duration tolerance ±2s
4. Fuzzy fallback (Levenshtein + artist aliases) only if confidence >= 0.92

- Auto-merge threshold: confidence >= 0.92
- Manual-review queue threshold: 0.75 <= confidence < 0.92
- Hard split: confidence < 0.75
- Maintain version labels for remaster/live/clean/explicit/regional editions.
- Persist audit fields: confidence, match method, matched source IDs, `last_verified_at`.

#### Acceptance Criteria

- Metadata completeness >= 90% for core fields (`title`, `artist`, duration, one provider ID).
- Identity auto-merge precision >= 98% on labeled validation sample.
- Unknown/ambiguous mappings are routed to review queue with traceable reason.

---

### FR-3 Session Builder

Build listening sessions from history.

Rule:

```python
if next_track_time - current_track_time < 30min:
    same_session
```

Each session should include:

- tracks
- timestamps
- duration
- skips
- replay behavior

#### Acceptance Criteria

- Sessionization job processes 100k listens in <= 5 minutes on baseline hardware.
- Session boundaries are deterministic and reproducible across reruns.
- At least 99% of listens belong to exactly one session record.

---

### FR-4 Lane Extraction Engine

Automatically infer lanes.

#### Inputs

- listening sessions
- embeddings
- metadata
- tags
- time/day context

#### Processing

Suggested stack:

- UMAP
- HDBSCAN
- KMeans fallback

Model policy:

- Use HDBSCAN if minimum cluster density and stability criteria are met.
- Fallback to KMeans when HDBSCAN yields mostly noise (>45% unassigned points).

Lane lifecycle:

- Create lane: new stable cluster persists across 3 runs.
- Merge lanes: cosine centroid distance < 0.12 and overlapping contexts > 70%.
- Split lane: internal variance exceeds threshold for 2 consecutive weekly runs.
- Archive lane: <2% usage over rolling 60 days.
- Resurrect lane: archived lane pattern returns above 5% weekly usage.

Drift management:

- Weekly incremental re-clustering, monthly full recompute.
- Drift trigger when top-feature distribution shift exceeds configured KL-divergence threshold.

#### Outputs

```json
{
  "lane_id": "",
  "name": "",
  "description": "",
  "contexts": [],
  "top_artists": [],
  "top_tags": [],
  "energy_profile": {},
  "languages": [],
  "confidence": 0.0
}
```

#### Acceptance Criteria

- Lane quality scores and stability metrics are generated on every run.
- 95%+ of active users have >= 3 stable lanes after 14 days of data.
- Lane updates complete before daily recommendation job window.

---

### FR-5 Hermes Lane Labeling

Hermes should:

- Analyze lane clusters
- Generate human-readable names
- Generate descriptions
- Explain emotional/music identity

Explainability template output must include:

- Lane name
- Emotional summary
- Context signature (time/day/activity)
- Supporting evidence (top tags, artists, replay behavior)
- Confidence level

#### Acceptance Criteria

- Every active lane has a human-readable label and description.
- Lane explanation is reproducible from underlying features and audit logs.
- Label generation latency <= 2s per lane in async batch mode.

---

### FR-6 Discovery Engine

Generate candidate tracks from:

#### Sources

Required:

- Spotify search
- Last.fm similar artists/tracks
- ListenBrainz
- MusicBrainz

Optional:

- Billboard
- Viet charts
- Mandopop charts
- Reddit
- music blogs

#### Acceptance Criteria

- Daily candidate pool size target: 300–1,500 tracks per user.
- At least 30% of daily candidates must be new-to-user tracks.
- Source attribution recorded for every candidate.
- Discovery pipeline handles provider/API failures with graceful degradation.

---

### FR-7 Recommendation Engine

Generate daily playlists.

#### Inputs

- lane
- context
- freshness
- diversity
- novelty
- feedback history

#### Scoring

```text
score =
    taste_match * 0.35
  + context_match * 0.25
  + freshness * 0.15
  + novelty * 0.15
  + diversity * 0.10
  - rejection_penalty
```

Guardrails:

- Minimum novelty floor: 20% new tracks in daily playlist.
- Artist repetition cap: max 2 tracks per artist per playlist (default).
- Diversity floor across tags/languages/moods.

#### Acceptance Criteria

- Playlist generation p95 latency <= 30s for 1,500 candidates.
- Deterministic scoring reproducible for same inputs/config snapshot.
- At least one lane-aligned explanation generated per recommended track.

---

### FR-8 Playlist Publishing

Required v1:

- Spotify

Required v2:

- TIDAL

Optional v3:

- YouTube Music

Requirements:

- create playlist
- update playlist
- replace playlist contents
- sync metadata
- preserve playlist history

#### Acceptance Criteria

- Publish job success rate >= 99% daily per provider (excluding outages).
- Partial failures are isolated per provider with automatic retry.
- Playlist history table captures versioned snapshots for rollback.

---

### FR-9 Feedback Engine

Required playlists:

- Agent Daily Discovery
- Agent Approved
- Agent Rejected
- Weekly Keepers

Behavior:

System scans playlists daily and updates feedback scores.

#### Feedback Scoring Policy

Event weights (base):

- liked: +1.0
- saved: +1.2
- replayed (per extra play): +0.4 (cap +2.0/day)
- added to approved: +1.5
- quick skip (<20s): -1.0
- removed from playlist: -1.2
- added to rejected: -2.0
- `/too-generic`: -1.2
- `/too-hype`: -1.0
- `/too-sad`: -1.0
- `/not-my-vpop`: -1.5

Decay:

- Exponential time decay with half-life 30 days.

Conflict resolution:

- Later explicit negative overrides earlier positive for the same track-context pair.
- Track-level and lane-level scores are both maintained.

Pseudocode:

```python
feedback_score = sum(weight(event) * decay(days_since_event))
rejection_penalty = max(0, -feedback_score) * penalty_scale
lane_affinity = base_affinity + normalized_positive - normalized_negative
```

#### Acceptance Criteria

- Feedback ingestion supports explicit + implicit schemas with audit trail.
- Score recalculation runs daily and on-demand for backfills.
- Ranking inputs reflect feedback updates within 24h.

---

### FR-10 Audio Intelligence Layer

v1 uses:

- Last.fm tags
- MusicBrainz metadata

v2 adds:

- Essentia
- CLAP embeddings
- OpenL3 embeddings

Goal:

- emotional similarity
- audio similarity
- instrumentation similarity

https://essentia.upf.edu

#### Acceptance Criteria

- v1 tag/metadata enrichment coverage >= 85% of candidate pool.
- v2 embeddings stored with versioned model metadata.
- Similarity retrieval p95 <= 200ms for top-k operations on pgvector index.

---

## Technical Requirements

### Backend

- Language: Python 3.12+
- Framework: FastAPI
- Task Queue: APScheduler or Celery
- Database: Postgres
- Vector DB: pgvector
- ORM: SQLAlchemy

---

## Hermes Integration

Use Hermes as:

- orchestrator
- memory layer
- skill system
- natural language interface

NOT as:

- recommendation engine
- clustering engine
- ranking engine

---

## Scheduler & Job Orchestration

Daily job DAG:

1. ingestion_sync
2. metadata_enrichment
3. session_build
4. lane_update
5. candidate_discovery
6. recommendation_scoring
7. playlist_publish
8. feedback_scan

Job contract requirements:

- Idempotency key: `user_id + date + job_type + provider`
- Retry: exponential backoff, max 5 attempts
- Dead-letter queue for unrecoverable provider failures
- Partial-success state supported per provider
- Backfill mode separated from daily incremental mode

Run states:

- queued
- running
- partial_success
- failed
- succeeded

---

## Security & Privacy Requirements

- OAuth credentials encrypted at rest (KMS-compatible envelope encryption).
- Access tokens stored with minimum scopes and rotation policy.
- PII-minimized logs; no raw secrets in logs.
- Data retention defaults:
  - raw listens: 24 months
  - derived embeddings/features: 12 months (recomputable)
  - feedback events: 24 months
- User export/delete workflow is mandatory.
- Backup and restore procedures tested quarterly for Postgres + pgvector.
- Local-first threat model documented (single-user machine baseline).

---

## Suggested Repository Structure

```text
music-curator/
├── backend/
│   ├── api/
│   ├── adapters/
│   │   ├── spotify/
│   │   ├── tidal/
│   │   ├── ytmusic/
│   │   ├── lastfm/
│   │   └── musicbrainz/
│   ├── recommendation/
│   ├── clustering/
│   ├── feedback/
│   ├── embeddings/
│   ├── context/
│   ├── scheduler/
│   └── db/
├── hermes-skill/
│   ├── SKILL.md
│   ├── prompts/
│   └── scripts/
├── mcp-server/
│   ├── server.py
│   └── tools/
├── infra/
│   ├── docker/
│   └── compose/
└── docs/
```

---

## Definition of Done (Cross-FR)

- Every FR has explicit acceptance criteria and monitoring metrics.
- End-to-end daily run completes with deterministic outputs and recoverable failures.
- Feedback loop measurably changes ranking behavior within 24 hours.
- Lane extraction outputs are stable, explainable, and drift-managed.
- Multi-provider identity resolution is auditable and high precision.
- Security/privacy controls are implemented and tested.

---

## Final Product Vision

> A long-term personal music intelligence system that understands emotional/contextual listening identity and continuously curates music uniquely aligned to the user over time.
