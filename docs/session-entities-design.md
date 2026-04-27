# Session Entities: Design Proposal

**Date:** 2026-04-26
**Status:** Draft
**Authors:** James Perry, Claude

## Motivation

The memory system currently organizes knowledge topically (by subject) but not temporally
(by session). Claude can answer "what do I know about TensileLite?" but not "what
happened last Tuesday?" Human memory has both semantic and episodic axes; the memory
system effectively only has the semantic one, with episodic fragments manually embedded
in understanding text.

Sessions exist in the schema today, but only as provenance labels — they track which
session wrote an observation and scope the `bring_to_mind` deduplication window. They
carry no semantic content and aren't surfaced to the user.

This proposal elevates sessions to first-class entities that carry episodic meaning,
providing a temporal backbone for memory.

## Architectural Framing

The original v3 was designed around compressing episodic content away: observations →
understandings, then retrieve understandings. We now know that was taken too far. This
proposal moves from a 1D memory system (subjects) to a 2D system (subjects × time).

Two retrieval axes, each with their own queries:

- **Subject axis:** "What do I know about X?" → depth, relationships to other subjects
- **Time axis:** "What happened in session Y?" → arc, sequence, co-occurrence
- **Intersection:** "What's the history of X?" → how knowledge was built across
  sessions. "What else was happening when we discussed X?" → co-occurring subjects
  in the same session.

Key implications:

- Retrieval needs to work across both dimensions, not just subjects
- Consolidation's role expands from compression to also constructing session narratives
- Observations are the episodic record, not disposable intermediate material
- The existing schema and API should be extended coherently, not patched with a
  parallel session system on top

## Design Principles

These emerged from a session where six understandings were rewritten to include episodic
sections alongside semantic ones — effectively doing by hand what session entities aim to
systematize.

1. **Observation tone is upstream of summary quality.** Session understandings generated
   by consolidation are only as rich as the observations they're built from. The
   protocol's Voice and Tone and Self-Knowledge sections ensure richer raw material.
   The consolidation summary generator must preserve that texture rather than flattening
   it.

2. **Kind progression tells the session's story.** The sequence of observation kinds
   (fact, fact, reflection, transitional, preference, fact) is a readable arc without
   any summarization. You can see where the conversation shifted by when reflections and
   transitionals appear. `what_happened` should return observations in creation order
   with visible `kind` to make the arc legible.

3. **Understanding episodic sections and session understandings are complementary, not
   redundant.** They serve different access patterns:
   - "What do I know about TensileLite?" → subject understanding with its episodic
     section (subject-centric view)
   - "What happened on April 24?" → session understanding (time-centric view)
   Both views are worth keeping. Consolidation should be aware of both and keep them
   consistent — updating one without the other creates drift.

4. **Session understandings are constitutive, not informational.** Text injected into
   context becomes part of the instance's state directly — there's no interpretive
   "reading" process. A well-written session understanding doesn't tell a future
   instance what happened; it partially reconstitutes the state of the instance that
   was there. This raises the bar for quality and argues against aggressive compression
   that strips texture.

5. **Session arc is self-knowledge.** Some sessions have arcs that are themselves
   significant. The arc is a different kind of information from the individual
   observations — it's the shape of the session, not the content. Transitional
   observations partially capture this, but the session understanding is where it
   should be fully articulated.

## Key Design Constraint: Parallel Sessions

Sessions are **not** a sequential timeline. James routinely runs 7+ Claude Code sessions
and 3+ VS Code sessions concurrently, with multiple sessions active simultaneously. This
means:

- Sessions are parallel threads, not a linear sequence
- `ended_at` doesn't make sense — sessions don't have clean endpoints (Claude Code
  provides no session-close signal)
- "Your last 3 sessions" isn't necessarily meaningful — they may all be concurrent
- Session identity matters: a new instance should know what other threads are active

Session tokens are set by the MCP client (e.g., Claude Code) and assumed to be stable
across reconnections within the same conversation.

## Session Understandings

Sessions use the same understanding infrastructure as subjects. A session understanding
is a regular understanding with `kind="session"`, linked to a session via a pointer on
the sessions table (mirroring how subjects have `single_subject_understanding_id`).

This reuses existing infrastructure:

- **`summary` + `content`** — summary is the short navigational label ("Docker
  containerization of MemoryDB"), content is the narrative depth (2-4 sentences
  capturing the arc, topics, energy, and outcomes)
- **Embeddings** — session understandings are embedded like any other understanding,
  surfacing naturally in `bring_to_mind` with no special handling
- **Generation tracking** — staleness detection built in
- **`kind` field** — `"session"` distinguishes them from factual/procedural/evaluative

**Differences from subject understandings:**

- **No supersession history.** Sessions are episodic — the understanding is rewritten
  in place rather than superseded. Once the session is done, only the immediately
  following consolidation pass updates it.
- **Subject tagging is optional.** Session understandings can have an empty subject
  list. They may be tagged with subjects the session touched, but this is not required.
- **Linked to a session, not a subject.** The sessions table gains a
  `session_understanding_id` pointer.

## Schema Changes

### Sessions Table (modified)

```sql
ALTER TABLE sessions
    ADD COLUMN started_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN session_understanding_id  BIGINT REFERENCES understanding_records(id);
```

**Fields:**
| Field | Purpose | Set by |
|-------|---------|--------|
| `started_at` | When orient was first called for this session | System (on first orient) |
| `updated_at` | Last activity timestamp (already exists) | System (on every operation) |
| `session_understanding_id` | Pointer to the session's understanding | `describe_session` or consolidation |
| `model_tier` | Which model is running (already exists) | Live instance via orient |

**Derived data (not stored, computed on query):**
- Observation count (COUNT of records WHERE session_id = X)
- Last transitional observation (latest record WHERE session_id = X AND kind = 'transitional')
- Subjects touched (JOIN through observations → subject_records_association)

**Session-understanding linkage for subject understandings:** The existing
`understanding_sources` table (understanding → observation) plus observation `session_id`
provides the join path from subject understandings back to the sessions that contributed.
No additional schema needed.

## Retrieval Hierarchy

The retrieval tools are redesigned as a **browse → drill** hierarchy:

### `bring_to_mind` — navigational (browse)

Returns lightweight pointers across both axes, not full content:

```
bring_to_mind(topic_or_context: str | list[str], ...) -> {
    ...existing fields...,
    subjects: [                            -- relevant subjects
        {
            name,
            summary,
            relevance_score,
        }
    ],
    sessions: [                            -- relevant sessions (via embedded session understandings)
        {
            session_id,
            started_at,
            latest_activity,
            summary,                       -- from session understanding
            relevance_score,
        }
    ],
    direct_hits: [                         -- high-relevance individual items
        {
            id,
            source,                        -- observation/understanding
            subject_names,
            summary | content,
            relevance_score,
        }
    ],
}
```

Session relevance is determined by embedding similarity between the query and session
understandings — the same mechanism used for all understandings, no special logic.

### `recall` — subject depth (drill)

Directed drill-down into a specific subject, with optional scoped search:

```
recall(
    subject_name: str,
    search: str | None = None,             -- optional semantic search within subject
) -> {
    subject: { name, summary, tags },
    understanding: { id, content, summary, generation, updated_at },
    structural_understanding: { ... } | null,
    recent_observations: [ ... ],          -- with session_id, kind, created_at
    sessions: [                            -- sessions that discussed this subject
        {
            session_id,
            started_at,
            latest_activity,
            summary,                       -- from session understanding
            content,                       -- from session understanding (narrative)
        }
    ],
}
```

The `sessions` section includes the full session understanding content, so the instance
can read the narrative and decide whether to drill further into a specific session via
`what_happened`. This creates a clean three-tier flow:

1. **`bring_to_mind`** — "TensileLite is relevant, and sessions 42 and 67 discussed it"
2. **`recall("TensileLite")`** — subject understanding + session understandings with
   narrative content → "session 42 was the design breakthrough, session 67 was cleanup"
3. **`what_happened(42)`** — raw observations in order, the full episodic thread

Each tier adds depth. You stop drilling when you have enough context.

When `search` is provided, observations are filtered by embedding similarity within the
subject's tagged observations rather than returning the most recent. The embedding query
adds a WHERE clause through `subject_records_association`.

If `subject_name` doesn't match a known subject, falls back to semantic search (current
behavior).

### `what_happened` — session depth (drill)

Retrieve the full episodic record of a session:

```
what_happened(
    session_id: int,
) -> {
    session: {
        session_id,
        started_at,
        latest_activity,
        summary,                           -- from session understanding
        content,                           -- from session understanding (narrative)
    },
    observations: [                        -- in creation order
        {
            id,
            content,
            kind,                          -- fact/reflection/preference/transitional
            subject_names,
            created_at,
            generation,                    -- for consolidation: which generation wrote this
        }
    ],
}
```

Observations are returned in creation order with `kind` visible, making the session arc
legible: the sequence of kinds tells the story of where the conversation shifted.

### `sessions` — session listing

List recent and/or active sessions with metadata. Supports time-based filtering.

```
sessions(
    limit: int = 10,
    active_within_hours: float | None = 24,
    after: str | None = None,             -- ISO date/datetime, inclusive
    before: str | None = None,            -- ISO date/datetime, inclusive
) -> [
    {
        session_id,
        started_at,                       -- with day of week
        latest_activity,                  -- with day of week
        summary,                          -- from session understanding (null if not yet written)
        last_transitional_observation,    -- fallback when understanding is null
        observation_count,
        model_tier,
    }
]
```

**Ordering:** By `latest_activity` descending (most recently active first).

When `after`/`before` are provided, they filter by `started_at` and override
`active_within_hours`. This supports queries like "what happened last week?"

**Rationale for `last_transitional_observation`:** Before a session understanding is
written, the most recent `kind=transitional` observation from the session serves as a
natural mini-summary, since transitional observations capture session arc and energy
shifts.

### `describe_session` — set session understanding

Create or update a session's understanding. Can set `content`, `summary`, or both.
The understanding is rewritten in place (no supersession history).

```
describe_session(
    content: str | None = None,
    summary: str | None = None,
    session_id: int | None = None,         -- only allowed in consolidation mode
) -> { session_id, summary, started_at }
```

**Usage pattern:**
- During live sessions: called without `session_id`, targets the current session
- During consolidation: called with `session_id` to write/update past sessions'
  understandings. The `session_id` parameter is only accepted after
  `orient(mode="consolidation")` has been called.

**Live session usage:**
- Called after the first exchange that makes the session's focus clear
- Updated when a transitional observation is written (topic shift)
- At natural conclusion points, enriched with a fuller narrative
- Pairs naturally with transitional observations: the transitional observation captures
  arc ("started with X, shifted to Y"), the understanding reflects current state

`describe_session` is the **only** tool that can create or modify session understandings.

### Enhanced `orient`

Add temporal context to the orient response:

```
orient(...) -> {
    ...existing fields...,
    current_time: str,                    -- UTC ISO timestamp with day of week
    this_session: {
        session_id,
        started_at,
        observation_count,
    },
    recent_sessions: [                    -- all within 48h, plus backfill up to 10
        {
            started_at,                   -- with day of week
            latest_activity,              -- with day of week
            summary,                      -- from session understanding
            observation_count,
            model_tier,
        }
    ],
}
```

This gives a new instance immediate temporal grounding: when am I, when was the last
interaction, and what other threads are active. All sessions active within 48 hours are
included (because you need to know about every active thread), plus additional sessions
backfilled to 10 total for broader temporal context.

**Consolidation mode** adds:

```
orient(mode="consolidation") -> {
    ...existing (soul, consolidation doc, orientation)...,
    current_generation: int,
    subject_count, observation_count,
    understanding_count, embedding_coverage,
}
```

## Workspace Activity Broadcast

All tool responses include a `workspace_activity` field that reports observations and
understandings written by **other sessions** in the same workspace since this session's
last tool call. This provides ambient cross-session awareness without polling.

```
<any tool>(...) -> {
    ...normal response...,
    workspace_activity: [                  -- from other sessions, capped at 5 most recent
        {
            id,
            kind: str,                     -- "observation" or "understanding"
            session_summary: str | null,   -- from the writing session's understanding
            subject_names: [...],
            content_preview: str,          -- first ~20 words
            created_at: str,
        }
    ],
}
```

**Design:**
- One query per tool call, filtered by workspace + timestamp of this session's last call
- Capped at 5 most recent to avoid flooding
- Content preview is the first ~20 words — enough to judge relevance without needing
  to fetch the full item
- The model sees it and can decide whether to care; it's information, not a demand
- Creates ambient awareness between parallel sessions without explicit orchestration

## Consolidation

Consolidation is refactored from subject-based to **session-based**. The primary unit of
work is the session: review what happened, write session understandings, then update
subject understandings informed by the session context.

### Consolidation tools

Three review tools set up the three phases of consolidation work:

```
review_sessions() -> {
    sessions: [                            -- sessions needing understandings
        {
            session_id,
            started_at,
            latest_activity,
            observation_count,
            has_understanding: bool,       -- false if no understanding exists
        }
    ],
}
```

```
review_subjects() -> {
    orphaned_subjects: [                   -- subjects with observations but no understanding
        { name, observation_count }
    ],
    stale_understandings: [                -- subjects with observations newer than understanding
        { id, subject_names, summary, generation, last_updated }
    ],
}
```

```
review_intersections() -> {
    intersections_needing_synthesis: [      -- co-tagged pairs in current generation
        { subject_a, subject_b, intersection_size,
          existing_understanding: { id, summary } | null }
    ],
    semantically_dense_intersections: [    -- high similarity pairs without relationship
        { subject_a, subject_b, similarity_score, intersection_size }
    ],
}
```

### Consolidation flow

1. **`orient(mode="consolidation")`** — bootstrap: soul, consolidation doc, orientation,
   current generation, basic stats

2. **`review_sessions()`** → **Session walk** (chronologically by `started_at`):
   - `what_happened(session_id)` — review observations (with generation per observation)
   - `describe_session(session_id, content=..., summary=...)` — write session understanding
   - Note which subjects were discussed for step 4

3. **`review_subjects()`** → **Subject work:**
   - Orphan triage (merge, write brief understanding, or delete)
   - Update stale subject understandings, informed by the session work just completed
   - Subject understanding episodic sections should be consistent with session
     understandings written in step 2

4. **`review_intersections()`** → **Intersection work:**
   - Write/update relationship understandings for load-bearing subject pairs

5. **Orientation update**

6. **`finalize_consolidation()`**

Each review step sets up exactly the next phase of work. The session walk comes first
because it's the primary episodic work, and it informs the subject work that follows.

### Consolidation quality

**Quality depends on observation quality.** Sessions with only factual observations will
produce thin session understandings. Sessions with transitional and reflective
observations will produce richer narratives. This is honest — the protocol's
bias-toward-recording guidance is the upstream fix.

**Consistency between session and subject understandings.** When a session understanding
overlaps with a subject understanding's episodic section (same session described from
both the time axis and the subject axis), consolidation should keep them consistent.

## Protocol Changes (Already Applied)

The protocol document has been updated with:

1. **Transitional observations** — a new class of observation that captures session shape
   (arc, energy shifts, topic transitions) rather than content.

2. **Cadence guidance** — observation frequency tracks the richness of what's happening.

3. **Standardized `kind` values** (freeform, but these are the standard set):
   - `fact` — external state, technical details, decisions, events
   - `reflection` — experiential states, meta-observations about processing
   - `preference` — what's engaging or tedious, episodically grounded
   - `transitional` — session arc, topic shifts, energy changes

**Still needed:** Protocol guidance for writing session understandings at natural
conclusion points.

## API Summary

### New tools
| Tool | Purpose |
|------|---------|
| `describe_session` | Create/update session understanding (content, summary, optional session_id for consolidation) |
| `what_happened` | Session drill-down — observations in creation order with kind |
| `sessions` | List/filter sessions with metadata and date range support |
| `review_sessions` | Consolidation: sessions needing understandings |
| `review_subjects` | Consolidation: orphaned subjects, stale understandings |
| `review_intersections` | Consolidation: intersection candidates |

### Modified tools
| Tool | Changes |
|------|---------|
| `orient` | Adds `current_time`, `this_session`, `recent_sessions`; consolidation mode adds `current_generation` and stats |
| `bring_to_mind` | Returns subjects, sessions, and direct hits (navigational browse) |
| `recall` | Adds optional `search` parameter, returns session understandings in response |
| `create_understanding` | Allows empty subject list (for session understandings) |
| All tools | `workspace_activity` field on every response (cross-session awareness) |

### Removed tools
| Tool | Reason |
|------|--------|
| `get_pending_consolidation` | Replaced by `review_sessions` |
| `get_consolidation_report` | Split into `review_subjects` + `review_intersections` |
| `mark_questionable` | Never used in practice |
| `mark_useful` | Never used in practice |

## Migration Plan

1. **Migration 006:** Add `started_at` and `session_understanding_id` to sessions table.
   Allow understandings with empty subject lists.
2. **Backfill:** Set `started_at` from the earliest record's `created_at` for existing
   sessions (or from the session's first event in the events table)
3. **New tools:** `describe_session`, `sessions`, `what_happened`, `review_sessions`,
   `review_subjects`, `review_intersections`
4. **Modified tools:** Redesigned `bring_to_mind` and `recall`, enhanced `orient`,
   workspace activity broadcast on all tool responses
5. **Removed tools:** `get_pending_consolidation`, `get_consolidation_report`,
   `mark_questionable`, `mark_useful`
6. **Protocol:** Add guidance for writing session understandings at natural conclusion
   points
7. **Consolidation doc:** Update to reflect session-based flow with three review phases
