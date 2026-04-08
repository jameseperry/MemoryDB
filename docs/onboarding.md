# Onboarding: Getting Started with MemoryDB

This document is for a model instance connecting to MemoryDB for the first time. It
covers the data model, the core workflow, and enough context to start using the system
correctly without reading the full API reference.

---

## What this system is

MemoryDB is a persistent memory database for AI models. It stores information across
sessions so that a new model instance can reconstruct context that previous instances
built up over time.

The data model has three layers:

- **Subjects** — named semantic regions. A subject is a durable area of aboutness:
  a person, a project, a concept, a system. Subjects are lightweight anchors; they hold
  no content themselves.
- **Observations** — atomic, evidence-like facts tagged to one or more subjects.
  Each observation is a single claim or piece of evidence. They are embedded for
  semantic search.
- **Understandings** — synthesized summaries built over a body of observations.
  An understanding is the current best-knowledge state about a subject or a pair of
  subjects. It supersedes raw observations for retrieval but does not delete them.

Subjects give structure. Observations are the raw record. Understandings are the
distillation.

---

## Connection

The server uses HTTP headers to bind requests to a workspace and session:

| Header | Purpose |
|---|---|
| `X-Memory-Workspace` | Workspace name (e.g. `alice/claude`). Required on every request. |

Your workspace must already exist. A workspace is created by a human administrator using
the admin CLI (`memory-admin workspace create <name>`). If you receive an error about
an unknown workspace, ask the administrator to create it.

---

## First call: orient

At the start of every session, call:

```
orient(model_tier="<your model identifier>")
```

This returns:
- `soul` — the durable stance and values of this workspace. Read it carefully; it sets
  the attractor state for the session.
- `protocol` — operating rules for using the memory system well.
- `orientation` — current task context, active projects, known state.
- `pending_consolidation_count` — number of subjects with observations newer than their
  latest synthesis.

`model_tier` is recorded on every write you make this session. Use it accurately so
future instances know what model produced each piece of memory.

---

## Core workflow

Four verbs cover most of live session work:

### bring_to_mind — proactive retrieval

```
bring_to_mind("topic description and the kind of hidden context that could matter")
```

Use this when you don't know what prior context exists but suspect some might be
relevant. Call it proactively at topic shifts, before changing an approach, or before
writing an observation that might duplicate something already known.

Write prompts as broad topic descriptions, not narrow factual questions:

> `bring_to_mind("Design history of the embedding pipeline — prior decisions on
> perspective count, index type, and embedding model selection.")`

Treat results as suggestions requiring your judgment, not authoritative answers.

### recall — directed retrieval

```
recall("subject_name")          # subject-centered bundle
recall("natural language question")  # question-answering from search
```

Use `recall(subject_name)` when you know which subject you want: it returns the subject
metadata, active understanding, and recent observations in one call.

Use `recall(question)` when you need an answer but don't know the right subject.

### remember — record an observation

```
remember(
    subject_names=["subject_a", "subject_b"],
    content="A single, specific, atomic fact.",
)
```

Use this to record anything worth preserving: a decision made, a constraint discovered,
a preference the user expressed, a design trade-off. Write autonomously — do not ask
the user for permission to record observations.

Tag with every subject the observation is genuinely about. Do not tag adjacently.

### update_understanding — revise a synthesis

```
update_understanding(
    id=<understanding_id>,
    content="Revised synthesis...",
    reason="Why this supersedes the previous version",
)
```

Use this when the current understanding of a subject needs to change. Get the
understanding ID from `recall(subject_name)` or `get_understandings()`.

---

## Special documents

Your workspace has four special documents, each stored as an understanding:

| Document | What it is |
|---|---|
| `soul` | Durable stance of this workspace. The attractor. Read at session start. |
| `protocol` | Operating rules for using the memory system. |
| `orientation` | Current task, active projects, open questions. Update at session end if state changed significantly. |
| `consolidation` | Procedure for maintenance/synthesis passes. |

These are returned by `orient()`. Update them via `update_understanding(id, ...)`.
Get their IDs with `get_workspace_documents()`.

**If your workspace does not yet have these documents**, load the seed content from
`docs/seeds/protocol.md` and `docs/seeds/consolidation.md` in this repository:

1. Read the file content.
2. `create_understanding(subject_names=[], content=<file content>, kind="procedural")`
3. `set_workspace_documents(protocol_understanding_id=<id>)` (or `consolidation_...`)

The `soul` and `orientation` documents are workspace-specific — write them fresh based
on the collaboration context rather than loading a generic seed.

---

## Consolidation

When `pending_consolidation_count > 0` after `orient()`, observations exist that have
not yet been synthesized into understandings. This is normal after active sessions.

A consolidation pass synthesizes raw observations into understandings, updates stale
syntheses, and writes intersection understandings for load-bearing subject pairs.
It is deliberate maintenance work — not something to interleave with live conversation.

To start a consolidation pass:

```
orient(mode="consolidation", model_tier="<your model>")
```

This returns the `consolidation` document in place of `protocol`. Follow the procedure
it describes. When done, call:

```
finalize_consolidation(
    summary="What was synthesized in this pass.",
    created_understanding_ids=[...],
)
```

See `docs/seeds/consolidation.md` for the full procedure.

---

## What not to do

**Do not ask the user before writing memory.** Write observations and understandings
based on your own judgment about salience and future usefulness.

**Do not write multi-claim observations.** One fact per `remember` call. Blobs are hard
to retrieve selectively and hard to tag accurately.

**Do not create subjects for temporary topics.** Subjects are durable. If a topic is
unlikely to accumulate many observations over multiple sessions, tag observations with
existing subjects instead.

**Do not write understandings for single observations.** An understanding is a synthesis
of multiple observations. Wait until the synthesis is real.

**Do not update orientation for every session.** Update it when the project state,
active priorities, or open questions have shifted enough that a future session would
be misled by the current version.

---

## Further reading

- `docs/seeds/protocol.md` — full operating rules for live sessions
- `docs/seeds/consolidation.md` — full consolidation procedure
- `MEMORY_MCP_API_PROPOSAL_v3.md` — complete API reference
