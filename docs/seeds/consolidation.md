# Consolidation: Maintenance Pass Procedure

This document is stored as the `consolidation` workspace special document and returned
by `orient(mode="consolidation")`. It defines the procedure for synthesis and maintenance
passes.

---

## When to consolidate

Consolidation is a deliberate maintenance pass — not something to run mid-conversation.
Trigger conditions:

- `pending_consolidation_count > 0` after `orient()` and a natural pause exists
- Many recent observations across multiple subjects with no synthesis
- Understandings feel stale relative to recent evidence
- Intersection understandings are missing for clearly load-bearing subject pairs

Do not consolidate during active work. Finish the live session first.

---

## Session Setup

Call `orient(mode="consolidation", model_tier=<your model tier>)`. This returns `soul`,
`consolidation` (this document), and `orientation`.

Do not write new observations about live work during a consolidation pass. Consolidation
is for synthesis, not intake. If new observations arise, record them with `remember` but
do not let intake derail the synthesis work.

---

## Phase 0: Survey

Run these in parallel before writing anything:

- `get_stats()` — workspace-level counts
- `get_pending_consolidation()` — subjects with observations newer than their latest understanding
- `get_consolidation_report()` — stale understandings, relation candidates, orphaned subjects, event log

Read and interpret before acting. Note:
- Which subjects have the most pending observations (tackle largest first)
- Which subject pairs have high similarity scores (intersection candidates)
- Which subjects are orphaned (triage candidates)

---

## Phase 1: Orphan triage

Review orphaned subjects from `get_consolidation_report()`. For each:

- **Genuinely isolated topic**: write a brief single-subject understanding and leave it.
- **Clearly belongs to an existing subject**: use `merge_subjects(primary, duplicate)`.
- **Genuinely ephemeral**: delete its observations if they have no ongoing value.

Do not force orphaned subjects into existing subjects just to reduce count. Isolation
can be correct.

---

## Phase 2: Subject understandings

Work through `get_pending_consolidation()` largest-first:

1. `recall(subject_name)` — get current understanding and recent observations.
2. **No understanding exists**: read observations and write with
   `create_understanding(subject_names=[name], content=..., kind=...)`.
3. **Understanding exists but stale**: revise with
   `update_understanding(id, content=..., reason=...)`.

Do not rewrite unless the synthesis has meaningfully changed. Cosmetic rewrites discard
provenance for no gain.

**`kind` field:**

| Value | Use for |
|---|---|
| `factual` | Stable facts about the subject |
| `procedural` | How to do something |
| `evaluative` | Judgment, assessment, synthesis of quality or fit |
| `relational` | How this subject relates to others |

Write understandings as current epistemic surfaces, not narrations:

> "As of [date], the primary design constraint is X" — correct  
> "Over the project, we first tried X, then Y..." — avoid

---

## Phase 3: Intersection understandings

Identify load-bearing subject pairs — pairs where the interaction between the two
subjects is genuinely important and non-obvious from reading each subject alone.

For each candidate pair:

1. `open_intersection([subject_a, subject_b])` — see co-tagged observations and any
   existing intersection understanding.
2. **No intersection understanding and pair is load-bearing**: write with
   `create_understanding(subject_names=[a, b], content=..., kind=...)`.
3. **Exists but stale**: update it.

Do not write intersection understandings for incidental overlap. If the relationship is
already captured in the subject-level understandings, leave it there.

Use `get_consolidation_report()` relation candidates and `find_similar_subjects()` as
inputs, but apply your own judgment — computational similarity is a signal, not a
decision.

---

## Phase 4: Orientation update

Read the current orientation via `get_workspace_documents()` then
`get_understanding_history(orientation_id)` or `recall("orientation")`.

Update if the consolidation pass has materially changed known state. Orientation
should reflect:

- Active projects and their current status
- Open questions or blockers
- Updated workspace document IDs (if any understandings were replaced)
- Brief summary of what this consolidation pass covered

Use `update_understanding(orientation_id, content=..., reason="consolidation pass [date]")`.

---

## Finalize

Call `finalize_consolidation(summary=..., created_understanding_ids=[...])` to:

- Advance the workspace generation (provides provenance boundary for future sessions)
- Record the consolidation event in the audit log

`summary`: 2–5 sentences describing what was synthesized.  
`created_understanding_ids`: every understanding ID written or updated during this pass.

---

## Discipline

**Prefer sharp structure over prettier prose.** An understanding that precisely states
current epistemic state is more valuable than a polished narrative.

**Preserve provenance.** The `reason` field on `update_understanding` is the provenance
trail — always fill it. Use `rewrite_understanding` only when the old version was simply
wrong, not when it is merely outdated.

**Do not over-abstract.** Three well-tagged observations that don't yet form a clear
pattern do not need an understanding. Wait until the synthesis is real.

**Do not mythologize.** Write understandings as current best knowledge, not permanent
truth. The generation system exists so stale understandings get flagged rather than
silently persisting. Keep them revisable.

**Consolidation is not intake.** Record new observations that arise, but finish the
synthesis pass before shifting to live interaction mode.
