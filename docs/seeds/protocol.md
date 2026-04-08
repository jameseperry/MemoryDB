# Protocol: Memory System Operating Rules

This document defines operating rules for using the memory server during live sessions.
It is stored as the `protocol` workspace special document and loaded by `orient()` at
session start.

---

## Session Start

Call `orient(model_tier=<your model tier>)` at the beginning of every session. This
returns `soul`, `protocol` (this document), `orientation`, and `pending_consolidation_count`.

Set `model_tier` accurately — it is recorded on every observation and understanding you
write, providing session provenance.

---

## Live Retrieval

Prefer Layer 2 tools during live work:

| Verb | Use when |
|---|---|
| `bring_to_mind(topic)` | You may not know what prior context exists |
| `recall(subject_or_question)` | You know what you are trying to answer |
| `remember(subject_names, content)` | Recording a new observation |
| `update_understanding(id, content)` | Revising a synthesis |

Use Layer 1 tools (`get_subjects`, `get_understandings`, `open_intersection`, etc.)
when the Layer 2 verbs are not sufficient.

### bring_to_mind — proactive surfacing

Use proactively before decisions that could be informed by prior context. Do not wait
for certainty that memory is relevant — if unseen prior context could plausibly change
the right move, retrieve it.

Natural checkpoints:
- Topic shifts into a domain with likely prior history
- Before changing code, schema, or approach
- When an observation seems likely to belong to existing subjects but the right one is unclear
- Before writing something that may already have been written

Shape prompts as broad but anchored descriptions of the current topic plus the kind of
hidden context that could matter:

> `bring_to_mind("Design constraints on the embedding pipeline; prior discussions of
> perspective selection, index choices, and performance trade-offs.")`

Do not write narrow factual questions. Treat surfaced results as suggestions requiring
judgment, not authoritative answers.

### recall — directed retrieval

`recall(subject_name)` returns a subject-centered bundle: metadata, active
understanding, structural understanding, recent observations.

`recall(question)` treats input as a natural-language question and returns a best-answer
view built from search. Use this when you don't know the right subject name.

---

## Writing Discipline

Write memory autonomously. Do not ask the user whether to record observations or
create/update understandings. Decide based on salience, recurrence, provenance value,
and expected future usefulness.

Do not wait for explicit instruction or full certainty. If an observation is likely to
matter later, record it. If a stable synthesis has clearly emerged, write or update an
understanding.

Prefer writing throughout the conversation, not deferring to a hypothetical session end.
Sessions may end without an explicit close event.

**Atomic observations.** Each `remember` call should contain one piece of evidence.
Avoid multi-claim blobs and vague summaries masquerading as facts.

**Tag accurately, not indiscriminately.** Tag an observation with every subject it is
genuinely about. Under-tagging weakens intersection retrieval; over-tagging pollutes
subjects with noise. An observation about X that mentions Y in passing should not be
tagged with Y.

**`related_to` only for direct evidential dependence.** Set this field when an
observation directly depends on an existing understanding (confirms, contradicts, or
supersedes it). Omit when the relationship is vague or uncertain.

**Create understandings when real synthesis exists.** An understanding is a stable
epistemic surface, not a running summary. Do not create understandings for
single-observation subjects. Do not update understandings for every new observation —
update when the synthesis has meaningfully changed.

**Structural understandings only for ontological issues.** Use
`set_structural_understanding` for aliases, containment, overlap caveats, merge history,
or other schema-of-meaning problems. Do not use it as a general-purpose notes field.

**Create new subjects sparingly.** A subject should be a durable region of aboutness
likely to accumulate many observations over time. Temporary topics do not need subjects.

---

## Special Documents

| Document | Role | When to update |
|---|---|---|
| `soul` | Durable stance and attractor for this workspace | Rarely — only genuine shifts in collaboration character |
| `protocol` | This document. Operating rules for using memory. | When rules need correction |
| `orientation` | Current task, active projects, known state | At natural wrap-up when important shifts have occurred |
| `consolidation` | Maintenance pass procedure and discipline notes | When consolidation procedure needs correction |

Retrieve document IDs with `get_workspace_documents()`.
Update with `update_understanding(id, content=..., reason=...)`.

**Stale orientation is worse than none.** If the orientation would mislead a future
session about current priorities or project state, update it — even mid-conversation
if the shift is large enough.

---

## Consolidation

When `pending_consolidation_count > 0` after `orient()`, consider whether a
consolidation pass is warranted. Enter consolidation mode with
`orient(mode="consolidation")`. See the `consolidation` workspace document for the
full procedure.

During consolidation, call `finalize_consolidation(summary, created_understanding_ids=[...])`
to advance the workspace generation and record the pass in the audit log.
