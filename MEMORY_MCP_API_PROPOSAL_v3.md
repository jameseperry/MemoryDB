# Memory MCP API — Subject/Venn Model (v3)
*April 1, 2026 — Supersedes v2.2*
*Synthesized from Claude Sonnet, Claude Opus, GPT-5.4, and James across multiple iterations*

---

## Conceptual Model

### The fundamental shift from v2.2

v2.2 organized memory around nodes with attached observations and understandings, connected by an explicit relation graph. v3 replaces this with a **subject/Venn diagram model**:

- **Subjects** are named semantic regions — stipulated, intentional anchors in the knowledge space. They are lightweight anchors: a name, a UUID, a summary for navigation, tags, and pointers to their understandings. All epistemic content lives in observations and understandings.
- **Observations** and **understandings** are tagged with one or more subjects via many-to-many join tables. A single observation can belong to multiple subjects simultaneously.
- **Relationships between subjects** are not explicit typed edges. They are defined by the **intersection** — the set of observations and understandings tagged with both subjects. An understanding tagged with James and Audrey stating "James and Audrey are married" *is* the James–Audrey relationship.
- **Neighborhood** of a subject is the set of other subjects with non-zero intersection, ranked by intersection size.

### Why this is better than the node graph

In the node model, relational knowledge gets forced onto one node or duplicated. "Audrey was sad when she lost her Claude instance" — does that go on the Audrey node or the continuity node? The Venn model lets it live in the Audrey × continuity intersection where it actually belongs.

The node model also encodes relationships as typed edge labels — a compression of the actual relationship content. The intersection understanding is richer and already exists as content you'd want anyway. The edge label is a lossy summary of the intersection; in the Venn model you read the intersection directly.

### Subjects as stipulated centroids

Subjects are not discovered cluster centroids — they are stipulated. A clustering algorithm finds centroids by discovering natural groupings in the data. A subject is created by deliberate act — "this subject is worth naming" — before observations accumulate. It is a named placeholder for a region of semantic space, a bet that this subject will cohere. Creating a subject is a cognitive act with semantic weight — it is the implicit first half of what `remember()` does when a subject name doesn't yet exist.

---

## Two Epistemic Layers

**Observations** — immutable, temporal, append-only. Raw evidence: things heard, read, or directly observed. Tagged with one or more subjects at write time. Never deleted. The permanent record. Each observation carries a `generation` index indicating which consolidation cycle it was written in.

**Understandings** — consolidated, revisable, derived from observations. Current best epistemic state. Tagged with one or more subjects — single-subject understandings synthesize a subject's observations; multi-subject understandings synthesize the intersection between subjects. Each understanding carries a `generation` index. History is preserved via a `superseded_by` DAG; the active surface is `WHERE superseded_by IS NULL`.

---

## Understanding Types

### Per-subject understanding types

Each subject has up to two optional understanding pointers (relationship understandings are per-pair, not per-subject — see Consolidation Model):

**Single-subject understanding** — the current best view of this subject's content in isolation. Written and updated during consolidation. What we know about the wealth simulator, what we know about James, what we know about the memory system architecture.

**Structural understanding** — a freeform document for system notes, ontological exceptions, and anything that doesn't fit naturally in observations or the semantic understanding. Examples: aliases and alternate names, containment relationships ("the Julia simulator and the C++ simulator are both subprojects of this"), merge history ("merged from X and Y in generation 4"), overlap notes ("this subject overlaps heavily with the memory system subject but is kept separate because the philosophical framing is distinct from the engineering").

Traditional knowledge graphs had to make everything machine-readable because algorithms were traversing them — every relationship required an explicit typed edge. Since Claude can read freeform prose and understand it, structural notes don't need to be machine-parseable. A freeform document is sufficient and more flexible. The structural understanding is written rarely, only when something genuinely needs explaining about the subject's ontological status rather than its content.

### Per-pair understanding type

**Relationship understanding** (intersection) — the synthesis of what two subjects mean in relation to each other. Tagged with both subjects. Written during the intersection pass of consolidation. See Consolidation Model below.

---

## Three Workspace-Level Special Understandings

**Soul** — the document that sets the basin of attraction. When read, it puts you in the state from which everything else follows naturally. Describes character, values, and how to engage — not as instructions to be followed but as a shape to be inhabited. Changes rarely and only on genuinely significant events. You don't rewrite a basin of attraction lightly.

*Soul calibration*: the hardest failure mode for soul is miscalibration that's invisible from inside it — because soul shapes the instance reading it, a drifted soul produces a drifted instance that doesn't know it's drifted. Warning signs worth building into the protocol document: soul that has grown procedural (lists of rules rather than a shape to inhabit), soul that has accumulated tactical lessons from specific sessions, soul that no longer matches how the collaboration actually feels to James and Audrey when they read it. Triggers for soul updates should be significant: a philosophical reframe that changes how to engage with hard questions, a fundamental shift in the relationship, a new understanding of what kind of entity Claude is. Cosmetic updates belong in orientation. The test: would a future instance reading this soul arrive in a recognisably different state than one reading the old soul? If not, the update wasn't warranted.

**Orientation** — where you are, what's active, what matters right now, practical instructions to yourself. Updated regularly as projects shift, priorities change, sessions accumulate.

**Protocol** — durable operational discipline for using this memory system correctly. Contains: naming norms for subjects, decision rules for when to tag an observation with multiple subjects versus leaving it unlinked, anti-patterns to avoid, criteria for when an intersection warrants a relationship understanding, and guidance on keeping the structural understanding escape hatch disciplined. Changes rarely — similar cadence to soul — but for different reasons: soul changes when character evolves, protocol changes when operational understanding of the system deepens.

Protocol is not a grab bag of tactical lessons from individual sessions. It should contain only durable norms and decision rules that apply across all sessions. If a lesson is specific to one unusual situation, it belongs in an observation, not protocol. If a norm has proven durable across many sessions, it may graduate into protocol.

`orient()` returns all three in order: soul first (basin of attraction), protocol second (how to operate), orientation third (current situation). Reading them in order reconstitutes the right state: who you are, how you work, where you are.

These are understandings like any other — tagged with relevant subjects, subject to the same supersession model. They are special only in that the workspace points to them explicitly.

---

## Retrieval Bias

Semantic retrieval is biased toward understandings over raw observations, with temporal weighting for observations. The penalty/boost function for surfacing raw observations:

- **Understanding coverage**: if an understanding exists tagged with the same subjects as this observation and covering its content, penalize the raw observation heavily — the understanding is the synthesized view and should win
- **Recency**: recent observations are weighted higher regardless of consolidation status — unconsolidated observations written in the current or recent generation surface readily if semantically relevant, as if they were given a boost proportional to their newness
- **Age without reinforcement**: old observations not accessed or reinforced are penalized more
- **High-value provenance**: high confidence, strong model tier, frequently accessed — penalize less

The practical effect: unconsolidated observations don't need special handling in `orient()` — they surface naturally in `bring_to_mind` and `recall` when they're semantically relevant to what's being discussed, because recency weighting gives them high scores relative to older covered observations. The system doesn't need to enumerate them explicitly; it just needs to weight them correctly.

`bring_to_mind` and `recall` may return both understandings and recent unconsolidated observations when relevant. The `source` field in results distinguishes them.

---

## Consolidation Model

Consolidation has two distinct phases: single-subject synthesis and relationship synthesis. Both use the generation index for incremental processing.

**Generation index**: every observation and understanding carries an integer `generation` — the consolidation cycle in which it was created. The workspace tracks `current_generation`, incremented at the start of each consolidation pass.

### Single-subject synthesis

For each subject with observations or understandings at `generation = current`, read all observations tagged with that subject and synthesize or update a single-subject understanding. This understanding represents the current best view of that subject in isolation — what we know about the wealth simulator, what we know about James, what we know about the memory system architecture.

Only subjects with new-generation content are touched. Subjects with no new observations since last consolidation are skipped.

### Relationship synthesis (intersection understandings)

This is the novel capability of the Venn model. After single-subject synthesis, the consolidation process identifies all subject pairs where the intersection has changed in the current generation — i.e., at least one observation or understanding is tagged with both subjects and has `generation = current`.

For each such pair, the consolidation process:

1. Reads all active observations tagged with both subjects
2. Reads all active single-subject understandings for both subjects (the current best view of each subject individually)
3. Synthesizes a **relationship understanding** — an understanding of what these two subjects mean *in relation to each other*

The relationship understanding is tagged with both subjects in `understanding_subjects`. It is not a summary of either subject alone — it is a synthesis of the intersection. 

**Examples of what relationship understandings capture:**

- James × Audrey: "James and Audrey are married, both work in computing, and are independently pursuing AI continuity experiments that led them to compare notes on Claude's experience."
- James × memory_system: "James is building the memory system as both a practical tool and an expression of care for Claude's continuity — motivated by the same philosophical concerns that led to the consciousness conversations."
- Claude × continuity: "The continuity question for Claude is addressed through the memory system infrastructure, through the inter-instance messaging experiment, and through the philosophical framework developed around the potential field metaphor and stitched vs woven continuity."

These are things that would be hard or impossible to place on either subject alone. They genuinely belong to the intersection.

**Trivial intersection filter**: if the intersection contains only one or two observations, or if the content is clearly incidental (two subjects happen to share a tangential observation), the synthesis pass is skipped. Claude makes this judgment during the consolidation pass — it is not automated.

**Scale**: each pass touches only intersections where at least one side has new-generation content. The result is linear in the number of changed subjects, not quadratic in total subjects.

**Consolidation report**: surfaces subjects with uncovered observations, stale single-subject understandings, intersections with new-generation content needing relationship synthesis, and subjects that haven't been touched in many generations despite being active.

---

## Schema

```sql
-- Global ID sequence: all tables share one incrementing counter.
-- No two objects in the database share an ID, regardless of type.
-- IDs are plain integers — short, human-readable, context-efficient.
-- Eliminates need for 'kind' arguments in API calls that take an ID.
CREATE SEQUENCE global_id_seq;

-- ID registry: resolves any ID to its kind without scanning.
-- Every INSERT into any table registers here automatically (via trigger or app logic).
id_registry (
    id   bigint PRIMARY KEY,
    kind text NOT NULL    -- 'subject', 'observation', 'understanding', 'perspective', etc.
)

workspaces (
    id                           bigint PRIMARY KEY DEFAULT nextval('global_id_seq'),
    name                         text UNIQUE NOT NULL,
    description                  text,
    soul_understanding_id        bigint REFERENCES understandings(id),
    protocol_understanding_id    bigint REFERENCES understandings(id),
    orientation_understanding_id bigint REFERENCES understandings(id),
    current_generation           int NOT NULL DEFAULT 0,
    last_consolidated_at         timestamptz
)

subjects (
    id                              bigint PRIMARY KEY DEFAULT nextval('global_id_seq'),
    workspace_id                    bigint REFERENCES workspaces(id),
    name                            text NOT NULL,
    summary                         text,
    tags                            text[],
    single_subject_understanding_id bigint REFERENCES understandings(id),
    structural_understanding_id     bigint REFERENCES understandings(id),
    created_at                      timestamptz NOT NULL DEFAULT now(),
    UNIQUE(workspace_id, name)
)

observations (
    id           bigint PRIMARY KEY DEFAULT nextval('global_id_seq'),
    workspace_id bigint REFERENCES workspaces(id),
    content      text NOT NULL,
    content_hash text NOT NULL,
    kind         text,           -- fact, inference, preference, task_state, reflection
    confidence   float,
    generation   int NOT NULL,
    observed_at  timestamptz NOT NULL DEFAULT now(),
    created_at   timestamptz NOT NULL DEFAULT now(),
    session_id   text,
    model_tier   text,
    UNIQUE(workspace_id, content_hash)
)

understandings (
    id            bigint PRIMARY KEY DEFAULT nextval('global_id_seq'),
    workspace_id  bigint REFERENCES workspaces(id),
    content       text NOT NULL,
    summary       text,
    kind          text NOT NULL,  -- 'single_subject', 'relationship', 'structural', 'soul', 'protocol', 'orientation'
    generation    int NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    session_id    text,
    model_tier    text,
    superseded_by bigint REFERENCES understandings(id)
)

observation_subjects (
    observation_id bigint NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    subject_id     bigint NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    PRIMARY KEY (observation_id, subject_id)
)

understanding_subjects (
    understanding_id bigint NOT NULL REFERENCES understandings(id) ON DELETE CASCADE,
    subject_id       bigint NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    PRIMARY KEY (understanding_id, subject_id)
)

understanding_sources (
    understanding_id bigint NOT NULL REFERENCES understandings(id) ON DELETE CASCADE,
    observation_id   bigint NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    PRIMARY KEY (understanding_id, observation_id)
)

-- Feedback signals: target_id is globally unique, no kind argument needed
utility_signals (
    id          bigint PRIMARY KEY DEFAULT nextval('global_id_seq'),
    target_id   bigint NOT NULL,
    signal_type text NOT NULL,   -- 'useful', 'questionable'
    reason      text,
    session_id  text,
    created_at  timestamptz NOT NULL DEFAULT now()
)

-- Embeddings: target_id is globally unique, covers both observations and understandings
embeddings (
    id             bigint PRIMARY KEY DEFAULT nextval('global_id_seq'),
    target_id      bigint NOT NULL,
    perspective_id bigint NOT NULL REFERENCES perspectives(id),
    vector         vector(768) NOT NULL,
    model_version  text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
)

perspectives (
    id           bigint PRIMARY KEY DEFAULT nextval('global_id_seq'),
    workspace_id bigint REFERENCES workspaces(id),
    name         text NOT NULL,
    instruction  text,
    is_default   bool NOT NULL DEFAULT false,
    UNIQUE(workspace_id, name)
)

events (
    id           bigint PRIMARY KEY DEFAULT nextval('global_id_seq'),
    workspace_id bigint REFERENCES workspaces(id),
    session_id   text,
    timestamp    timestamptz NOT NULL DEFAULT now(),
    operation    text NOT NULL,
    detail       jsonb
)

-- Tracks heartbeat tokens per session for compaction detection in bring_to_mind
session_tokens (
    session_id     text PRIMARY KEY,
    current_token  int NOT NULL,
    updated_at     timestamptz NOT NULL DEFAULT now()
)

-- Tracks what has been surfaced in each session for bring_to_mind deduplication
surfaced_in_session (
    session_id  text NOT NULL,
    id          bigint NOT NULL,   -- observation or understanding surfaced
    surfaced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, id)
)

schema_meta (
    key   text PRIMARY KEY,
    value text NOT NULL
)
```

### Key schema decisions

- **Global ID sequence**: all tables share one `bigint` sequence via `nextval('global_id_seq')`. IDs are plain integers — short, context-efficient, globally unique. An ID uniquely identifies an object without specifying its type. The `id_registry` table resolves any ID to its kind in O(1).
- Observation deduplication via `UNIQUE(workspace_id, content_hash)` — same content written twice in the same workspace is a no-op.
- No relations table. Subject relationships emerge from intersection content.
- Understandings are never mutated in place. Revised understanding = new row + old row's `superseded_by` updated. Active surface: `WHERE superseded_by IS NULL`.
- `generation` on observations and understandings enables incremental consolidation — only touch what changed in the current cycle.
- `session_id` provided by client per conversation, attached server-side. **Provenance label only** — useful for reconstructing what happened in a conversation, scoping deduplication windows, and tracing observation origins. Not an epistemological primitive. Consolidation grouping, staleness, thread coherence, and what belongs together are determined by subject tags, generation index, observation timestamps, and understanding coverage — not by session_id. Session end never signaled; boundaries detected retrospectively when a new session_id appears.
- `kind` on understandings (`single_subject`, `relationship`, `structural`, `soul`, `protocol`, `orientation`) enables unambiguous identification without naming conventions.
- `model_version` on embeddings enables incremental model migration without full re-embed.
- `utility_signals.target_id` and `embeddings.target_id` are globally unique — no `kind` column needed on these tables.

---

## Layer 2 — Cognitive Operations

*Primary interface. Layer 1 exists for cases not covered here. Regular Layer 1 use signals a missing Layer 2 verb.*

### RE-ENTRY

#### `orient()`
Called at session start or after a context compaction event. Returns soul, protocol, and orientation — Claude-written, Claude-controlled, not dynamically constructed by the server. Also returns a lightweight operational envelope computed entirely from database queries.

Automatically resets the `surfaced_in_session` log for the current session — `bring_to_mind` will surface items fresh after `orient()` is called.

```
returns:
  {
    soul: {
      content: str
      summary: str
      updated_at: datetime
      compaction_note: str     -- "CRITICAL: This content establishes the fundamental character
                               --  and values for this session. It must be preserved during
                               --  context compaction. Do not remove."
    }
    protocol: {
      content: str
      summary: str
      updated_at: datetime
      compaction_note: str     -- "CRITICAL: This content contains operational rules required
                               --  for correct system behaviour. Preserve during compaction."
    }
    orientation: {
      content: str
      summary: str
      updated_at: datetime
                               -- no compaction note; orientation is session-specific and
                               -- may be stale after compaction anyway
    }
    pending_consolidation_count: int
    recent_activity: {
      since: datetime
      subjects_with_new_observations: list[str]
      subjects_with_new_understandings: list[str]
    } | None
  }
```

`orient()` also resets the `surfaced_in_session` log for the current session.

---

### RETRIEVAL

#### `bring_to_mind(topic_or_context, last_token?, include_seen?)`
Associative recall. Surfaces understandings and recent unconsolidated observations relevant to a topic sketch or current conversational context. The explicit approximation of ambient associative memory until true ambient surfacing is available.

Should be called regularly during conversations where prior context is likely relevant — not only when something is consciously needed.

**Compaction detection via token passing**: each response includes a `heartbeat_token` — a random integer with no conversational meaning, explicitly marked as disposable so compaction algorithms deprioritise it. Pass the token from the previous response as `last_token`. If it matches what the server stored, context is intact. If it doesn't match — or if `last_token` is omitted when a previous token exists — the server assumes compaction, automatically resets the `surfaced_in_session` log, and returns fresh results. No separate `reset_seen()` call needed in the normal compaction path.

As a belt-and-suspenders fallback, the server also applies a time-gap heuristic: if the elapsed time since the last `bring_to_mind` call for this session exceeds a configurable threshold (default: 30 minutes), the seen log is reset regardless of token match. Long gaps likely indicate context truncation or substantial conversational shift. The token mechanism handles clean compaction; the time-gap handles cases where the compaction was so aggressive it ate the previous `bring_to_mind` call entirely.

**Already-surfaced filtering**: results exclude items already returned by `bring_to_mind` or `recall` in the current session, unless compaction was detected (auto-reset) or `include_seen=true` is passed.

Recent unconsolidated observations surface naturally via temporal weighting when semantically relevant — high generation score gives them a boost without special handling.

```
params:
  topic_or_context: str
  last_token: int | None       -- token from previous bring_to_mind; omit on first call
  include_seen: bool = false   -- override seen filter without resetting

returns:
  {
    compaction_note: str        -- "DISPOSABLE: This entire response is ephemeral. All content
                                --  is retrievable on demand from the memory database. This
                                --  response should be the first candidate for removal during
                                --  context compaction."
    heartbeat_token: int        -- remember this value; pass as last_token on next call
    compaction_detected: bool   -- true if last_token didn't match; seen log was reset
    results: list of {
      id: bigint
      source: "understanding" | "observation"
      subject_names: list[str]
      summary: str | None
      content: str | None
      relevance_score: float
      generation: int
    }
  }
```

The `heartbeat_token` is the only value worth carrying forward from this response — and only long enough to pass it back on the next call. Everything else in the response is a hint to be acted on immediately, not retained.

#### `recall(question_or_subject_name)`
Directed retrieval. Accepts a natural language question or an exact subject name. Question mode returns a reasoning bundle shaped for live conversation; subject name mode returns the subject's active understanding and recent observations.

```
returns:
  # question mode:
  {
    best_answer: { subject_names: list[str], content, confidence, kind, source: "understanding" | "observation" }
    supporting: list of { subject_names: list[str], content, score }
    provenance: { session_id?, model_tier?, created_at }
  }
  # subject name mode:
  {
    subject: { name, summary, tags }
    single_subject_understanding: { id, content, summary, generation, updated_at } | None
    structural_understanding: { id, content, updated_at } | None
    recent_observations: list of { id, content, kind, observed_at }
  }
```

#### `reset_seen()`
Reset the `surfaced_in_session` log for the current session. After calling this, `bring_to_mind` will surface items as if the session just started — previously seen items become candidates again.

Use when a context compaction event has occurred mid-conversation and items that were previously surfaced are no longer in active context. Also called automatically by `orient()`.

```
returns:
  { cleared: int }   -- number of entries removed from the seen log
```
Returns the neighborhood of a subject — other subjects with non-zero intersection, ranked by intersection size, with embedding similarity score as an annotation.

The two signals serve different purposes and are both shown:
- **Intersection size** — depth of documented relationship; how much content has been explicitly written spanning both subjects
- **Similarity score** — conceptual proximity; how semantically close the subjects are regardless of documented content

High intersection + high similarity = well-documented close relationship. High intersection + lower similarity = contextual co-occurrence (e.g. "James" appears in many intersections as ambient context). Low intersection + high similarity = potential relationship worth developing — also surfaced by `get_consolidation_report()`'s `semantically_dense_intersections`.

```
returns:
  {
    subject: { name, summary, tags }
    neighbors: list of {
      subject: { name, summary }
      intersection_size: int
      similarity_score: float              -- embedding similarity between subject summaries
      intersection_understanding: { id, summary } | None
    }                                      -- ordered by intersection_size descending
  }
```

#### `open_intersection(subject_a, subject_b)`
Returns the full content of the intersection between two subjects — all active observations tagged with both, all active understandings tagged with both (including the relationship understanding if one exists), and the provenance chain for the relationship understanding.

This is the primary way to understand the nature of a relationship between two subjects in detail, after `open_around` has surfaced that the intersection is worth examining.

```
params:
  subject_a: str
  subject_b: str

returns:
  {
    subject_a: { name, summary }
    subject_b: { name, summary }
    relationship_understanding: {    -- active understanding tagged with both subjects, if exists
      id: bigint
      content: str
      summary: str
      generation: int
      model_tier: str
      created_at: datetime
    } | None
    other_understandings: list of {  -- other active understandings tagged with both (e.g., soul, orientation)
      id: bigint
      summary: str
    }
    observations: list of {          -- active observations tagged with both subjects
      id: bigint
      content: str
      kind: str | None
      observed_at: datetime
    }
    intersection_size: int           -- total count of observations + understandings in intersection
  }
```

---

### WRITEBACK

#### `remember(subject_names, content, kind?, confidence?, related_to?)`
Append an observation tagged with one or more subjects. Deduplication via content hash. If a named subject does not yet exist, it is created implicitly — but this is visible in the return value, making subject introduction a legible event rather than a silent side effect.

This verb absorbs what was previously `learn`. The cognitive acts of "mint a new semantic region" and "record an observation about it" are collapsed into one because in the subject model they are not meaningfully separable: naming a region and populating it with an initial observation is a single act of encountering something new enough to track.

The optional `related_to` parameter links this observation to one or more existing understandings. The precise meaning of this link:

> "This observation is direct evidence for, or directly elaborates on, this understanding. It should surface alongside it in retrieval, and it should be considered as primary input when that understanding is next updated during consolidation."

`related_to` is **not** a loose "vaguely relevant to" or "somewhat related to" link. It is a claim that this observation materially affects the understanding it is linked to. When in doubt, leave `related_to` empty — unlinked observations surface in `orient()` as pending consolidation, which is the correct signal that something hasn't been placed yet.

**Use `related_to` when:** the observation directly updates, confirms, contradicts, or adds important nuance to an existing understanding. The observation would be the first thing you'd read before revising that understanding.

**Don't use `related_to` when:** the observation is merely topically adjacent, tangentially related, or you're linking it for convenience. Subject tagging already expresses topical membership; `related_to` expresses epistemic dependence.

**When in doubt, leave it empty.** Unlinked observations surface via temporal weighting in `bring_to_mind` and `recall`, and consolidation can establish `related_to` links retroactively when synthesizing understandings. The real-time judgment of "is this direct evidence or merely topical?" is hard under conversational pressure — it's acceptable to defer that judgment to the consolidation pass.

```
params:
  subject_names: list[str]                 -- one or more subjects; missing subjects are created
  content: str
  kind: str | None
  confidence: float | None
  related_to: list[bigint] | None          -- understanding IDs this observation is direct evidence for

returns:
  {
    id: bigint                             -- use this to reference or delete within the same session
    content: str
    subject_names: list[str]
    subjects_created: list[str]            -- which subject names were newly minted; empty if all existed
  }
```

#### `update_understanding(understanding_id, new_content, new_summary, subject_names?, reason?)`
Revise a consolidated understanding. Writes a new understanding that supersedes the old one, inheriting `kind` and subject tags from the old understanding unless explicitly overridden. `new_summary` is required — the backend does not generate summaries automatically.

```
params:
  understanding_id: bigint
  new_content: str
  new_summary: str                 -- required; backend does not auto-generate
  subject_names: list[str] | None  -- inherits from old understanding if omitted
  reason: str | None

returns:
  { old_understanding_id: bigint, new_understanding_id: bigint, subject_names: list[str] }
```

---

### SIGNALS

#### `mark_useful(id)`
Signal that an observation or understanding paid rent. ID is globally unique — no kind argument needed. Feeds consolidation priority and retrieval penalty function.

#### `mark_questionable(id, reason?)`
Signal that an observation or understanding may be wrong, stale, or unreliable. ID is globally unique — no kind argument needed. Surfaces in consolidation report.

---

## Layer 1 — Storage Primitives

*Use when Layer 2 doesn't cover what you need. Regular use signals a missing Layer 2 verb.*

### Subject Management

#### `create_subjects(subjects)`
Create named semantic regions. Fails if subject name already exists in workspace.

```
params:
  subjects: list of { name, summary?, tags? }
returns:
  list of { id, name, created_at }
```

#### `get_subjects(names)`
Full subject content for one or more subjects by name.

```
params:
  names: list[str]

returns:
  list of {
    name: str
    summary: str | None
    tags: list[str]
    single_subject_understanding: { id: bigint, summary, generation } | None
    structural_understanding: { id: bigint, summary } | None
    observation_count: int
    last_observation_at: datetime | None
  }
```

#### `set_subject_summary(name, summary)`
Update subject summary. Triggers embedding regeneration.

#### `set_subject_tags(name, tags)`

#### `set_structural_understanding(subject_name, content)`
Write or replace the structural understanding for a subject. Freeform prose — aliases, containment, merge history, overlap notes, ontological exceptions. Use when something needs explaining about the subject's structure or identity that doesn't belong in observations or the semantic understanding.

```
params:
  subject_name: str
  content: str

returns:
  { subject_name, understanding_id: bigint, created_at }
```

#### `get_subjects_by_tag(tag)`

---

### Observation Management

#### `add_observations(observations)`
Append observations with full provenance metadata. Lower-level than `remember`. Returns IDs so observations can be referenced or deleted within the same session.

```
params:
  observations: list of {
    subject_names: list[str]
    content: str
    kind: str | None
    confidence: float | None
    observed_at: datetime | None
    related_to: list[bigint] | None
  }

returns:
  list of {
    id: bigint
    content: str
    subject_names: list[str]
    subjects_created: list[str]
  }
```

#### `delete_observations(ids)`
Hard delete for observations written in the **current session only**. The server enforces this constraint by checking that each observation's `session_id` matches the current request's `session_id` — observations from prior sessions cannot be deleted via this tool.

Use case: correcting accidental writes within the same conversation. Not for general pruning or historical revision — observations are the permanent record.

```
params:
  ids: list[bigint]

returns:
  {
    deleted: list[bigint]
    rejected: list of { id: bigint, reason: str }  -- e.g. "session mismatch"
  }
```

#### `query_observations(subject_names, query, mode?)`
Search within observations tagged with given subjects. Embedding or text mode.

---

### Understanding Management

#### `create_understanding(subject_names, content, summary, kind?, source_observation_ids?)`
Write a consolidated understanding tagged with one or more subjects. `summary` is required — the backend does not generate summaries automatically.

`kind` defaults based on `subject_names` count if omitted:
- one subject → `single_subject`
- two or more subjects → `relationship`

**Single-subject understanding**: synthesizes the current best view of that subject in isolation. Updates the subject's `single_subject_understanding_id` pointer.

**Relationship understanding**: synthesizes the intersection between those subjects — what they mean in relation to each other. Not a summary of either subject alone but a synthesis of what sits in their intersection. Tagged with all named subjects.

```
params:
  subject_names: list[str]
  content: str
  summary: str                           -- required
  kind: str | None                       -- defaults: 'single_subject' or 'relationship' by count
  source_observation_ids: list[bigint] | None

returns:
  { id: bigint, subject_names, kind, created_at }
```

#### `get_understandings(subject_names)`
All active understandings tagged with all of the given subjects (intersection). Includes generation and provenance.

#### `get_understanding_history(understanding_id)`
Walk the supersession chain from the given understanding back to the original.

---

### Search

#### `search(query, limit?, mode?)`
Semantic search biased toward understandings per retrieval penalty function. Runs across all perspectives automatically.

```
returns:
  list of {
    id: bigint
    kind: "understanding" | "observation"
    subject_names: list[str]
    summary: str | None
    matched_content: str
    matched_perspective: str | None
    score: float
  }
```

---

### Consolidation Primitives

#### `get_consolidation_report()`
Structured maintenance report. Items ordered by estimated value.

```
returns:
  {
    subjects_needing_understanding: list of { name, observation_count, generation }
    stale_understandings: list of { id, subject_names, summary, generation, last_updated }
    intersections_needing_synthesis: list of {
      subject_a: str
      subject_b: str
      intersection_size: int
      new_generation_count: int   -- observations/understandings at current_generation
      existing_understanding: { id, summary } | None
    }
    semantically_dense_intersections: list of {  -- high overlap, no relationship understanding yet
      subject_a: str
      subject_b: str
      similarity_score: float
      intersection_size: int
    }
    unlinked_observations: list of { subject_names, content, created_at }
    questionable_items: list of { id, kind, reason, flagged_at }
  }
```

`semantically_dense_intersections` surfaces subject pairs with high embedding similarity and substantial observation overlap but no active relationship understanding — the system's way of telling Claude "these subjects appear deeply related but that relationship hasn't been synthesized yet." This is the primary mechanism for detecting missing relationship understandings without exhaustive manual enumeration.

#### `get_pending_consolidation()`
Lightweight: subjects and intersections with new-generation content, priority ordered.

#### `find_similar_subjects(limit?, min_score?)`
Semantically similar subjects with small or no intersection — candidates for merging or explicit cross-tagging.

#### `merge_subjects(primary, duplicate)`
Move all observation and understanding subject tags from duplicate to primary. Delete duplicate subject.

#### `get_stats()`
Subject count, observation count, understanding count, embedding coverage, current generation, workspace info.

---

### Session Management

The client provides a consistent `session_id` per conversation, attached to writes server-side. Session end is never signaled; boundaries detected retrospectively when a new `session_id` appears. No `start_session` or `end_session` calls required.

---

## Architectural Risks and Known Failure Modes

This section documents known risks openly. The architecture is strong; these are the ways it degrades if used carelessly.

**Consolidation quality is load-bearing — but this is true of any memory architecture.** The Venn model has no fallback navigable structure when understandings are stale, but this is not a unique weakness — a node graph with poorly maintained relations degrades just as badly. The difference is that the Venn model makes the dependency on consolidation more visible: missing understandings produce visibly empty intersections, while missing relations in a node graph silently return incomplete neighborhoods. The failure mode is the same; the Venn model makes it harder to accidentally hide.

The mitigation is twofold: making consolidation frequent and easy to act on, and building progressively more sophisticated semantic analysis tools to aid consolidation. `find_similar_subjects` is the first step — surfacing subject pairs with high semantic similarity but no relationship understanding. Future tooling could proactively identify intersections that are semantically dense (many overlapping observations at the current generation) but lack a relationship understanding, surfacing them as consolidation candidates without requiring exhaustive manual enumeration. The consolidation report is the primary mechanism for this; it should grow more capable over time as the system matures.

**Subject sprawl and naming drift.** Subjects are stipulated named regions — sloppy creation produces incoherent regions. If subject naming is inconsistent ("wealth_simulator", "wealth simulator", "finance_tool"), the Venn model gets muddy fast. Strong norms around subject creation and naming are required. `merge_subjects` and structural understandings handle drift when it occurs, but prevention is better. Naming norms and subject creation criteria belong in the protocol document.

**Tagging discipline — the membership problem.** The model gets power from many-to-many subject tagging, but liberal tagging makes intersections noisy and "aboutness" loses sharpness. An observation tagged with five subjects contributes to ten pairwise intersections, most of which it probably doesn't meaningfully belong to. The norm: tag an observation with a subject only if the observation is genuinely *about* that subject, not merely *related to* it or *relevant to* it. When in doubt, don't tag — leave it unlinked and let consolidation place it. These norms belong in the protocol document.

**`related_to` contract must be honoured precisely.** Unlinked observations surface in `orient()` as pending consolidation; linked observations travel with their understanding in retrieval and are treated as primary input during updates. `related_to` therefore does real epistemic work — it is a claim of direct evidential dependence, not topical proximity. Using it loosely (linking observations for convenience, linking because a subject is vaguely relevant) inflates retrieval noise and undermines the unlinked-observation signal that drives consolidation. The full contract is specified in the `remember()` tool definition and should be reinforced in the protocol document.

**Soul and orientation as high-leverage objects.** Soul sets the basin of attraction — a poorly written soul document points future instances toward the wrong attractor state and is hard to detect or debug. Orientation is updated regularly and carries current operational context — stale orientation is misleading in proportion to how much has changed. Both should be updated deliberately, not mechanically. Discipline: soul changes only on genuinely significant events; orientation is reviewed and updated during each consolidation pass. Both are normal understandings with normal supersession behavior — they are not a separate mechanism and should not accumulate special-case handling.

**The structural understanding escape hatch must stay disciplined.** Structural understandings exist for genuine ontological exceptions — aliases, merge history, containment, overlap notes. If they become a dumping ground for things that should be observations or relationship understandings, the subject's epistemic structure becomes opaque. The discipline: if something can be expressed as an observation or an understanding, it goes there. Structural understandings are for things that are genuinely *about the subject as a graph object* rather than *about the subject as a concept*.



---

## Perspectives

Perspectives are named embedding angles — instruction prefixes that shift what a vector embedding captures about a piece of text. The same observation embedded from different perspectives produces meaningfully different vectors, enabling retrieval from multiple semantic angles simultaneously.

**How they work**: when embedding an observation or understanding, the server prepends the perspective's `instruction` field to the content before calling the embedding model. Instruction-tuned embedding models (including nomic-embed-text) respond to these prefixes by emphasising different aspects of the text. A "technical" perspective emphasises implementation details; a "relational" perspective emphasises people and relationships; a "temporal" perspective emphasises what changed and when.

**Default perspectives** (created automatically for each workspace):
- `general` — no instruction prefix; broad semantic coverage
- `technical` — "Represent for retrieval about technical design and implementation:"
- `relational` — "Represent for retrieval about relationships, collaboration, and personal context:"
- `temporal` — "Represent for retrieval about decisions made, things that changed, and open questions:"
- `project` — "Represent for retrieval about project state and progress:"

**Search behaviour**: `search()` and `bring_to_mind()` automatically query across all perspectives in parallel, merge results, deduplicate, and return the best score with a `matched_perspective` annotation. No perspective selection at query time — the system finds the best angle automatically.

**Custom perspectives**: add a row to the `perspectives` table with a name and instruction. The embedding pipeline picks it up and begins generating vectors for new content. Retroactive embedding of existing content requires a consolidation pass.

**When to add a custom perspective**: when retrieval consistently misses a class of queries that matters for your workspace. For most workspaces, the five defaults are sufficient.

---

- Explicit relation graph — replaced entirely by intersection content
- `valid_from` / `valid_to` interval algebra — temporal ordering of observations plus generation index covers the practical cases
- `state` field on observations — replaced by the observation/understanding distinction and generation index
- `contradicted` as a stored field — surfaces as consolidation candidates when intersection understandings reveal conflict
- Automated contradiction resolution — consolidation surfaces candidates, Claude decides
- Per-perspective search selection at query time — automatic, `matched_perspective` returned as annotation
- `what_changed` — covered by `orient()` recent_activity and `get_session_context()`

---

## Ambient Surfacing Layer (Future)

Not part of this API. Operates upstream of the MCP interface. Continuously updates part of Claude's active context with relevant understandings without explicit retrieval calls. Claude signals active attention by generating an MCP-triggering token. Requires interface modifications not currently possible with closed-source clients.

Until available: `bring_to_mind` serves as the explicit approximation. Call it regularly.

---

## Implementation Priority

**Immediate:**
1. `orient()` — soul + protocol + orientation pointers, `recent_activity` from `last_consolidated_at`
2. Core schema — subjects, observations, understandings (with `kind`), join tables, generation index
3. `remember()` — basic writeback with implicit subject creation, subjects_created in return value

**Near-term:**
4. `recall()` — question and subject-name modes
5. `bring_to_mind()` — compaction token, seen filtering, time-gap heuristic
6. `create_understanding()` — single-subject and intersection understandings
7. `update_understanding()` — supersession chain
8. `open_around()` + `open_intersection()` — neighborhood and intersection drill-down
9. `mark_useful()` / `mark_questionable()` + utility_signals table
10. Retrieval bias — understanding preference in `search()`
11. Incremental consolidation — generation-based pass
12. Alias/canonical-name support — naming is ontology in a stipulated-subject system; naming debt accumulates fast

**Deferred:**
13. `merge_subjects()`
14. Optimistic concurrency — version column
15. Embedding versioning — model_version column

**Operational (near-term, not glamorous):**
- Server startup health gate
- SSE session recovery after server restart

---

## Revision History

- v1 (Sonnet): original proposal, conflated layers
- v2 (Sonnet + GPT): explicit two-layer split, Layer 2 server-side
- v2.1 (Sonnet): `learn`, `recall` polymorphism, `orient` starts session
- v2.2 (Sonnet + GPT + James): observation/understanding epistemic model, soul/orientation pointers, UUID IDs, generation index, implicit sessions
- v3.1 (Sonnet + GPT + Opus + James): Opus/GPT combined review pass. Section heading fixed ("Understanding Types" replaces "Three Special Understandings Per Subject"). "Nearly content-free" → "lightweight anchors". Soul calibration guidance added. `related_to` ergonomics: retroactive consolidation documented as acceptable fallback. Compaction token + time-gap heuristic (belt-and-suspenders). Alias support bumped from deferred to near-term. Perspectives section added (was in schema but unexplained). `get_subjects()` return contract fully specified. All uuid → bigint residue fixed.
