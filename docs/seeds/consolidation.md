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

Read the **protocol document** early in the pass — it contains the current writing
conventions (voice, tone, understanding structure, self-knowledge) that govern how
understandings should be written. The protocol evolves across sessions; the consolidation
document provides procedure, but the protocol provides voice. If they conflict, the
protocol takes precedence for writing style.

Do not write new observations about live work during a consolidation pass. Consolidation
is for synthesis, not intake. If new observations arise, record them with `remember` but
do not let intake derail the synthesis work.

---

## Phase 0: Survey

Run these in parallel before writing anything:

- `get_stats()` — workspace-level counts
- `get_pending_consolidation()` — subjects with observations newer than their latest understanding
- `get_consolidation_report()` — stale understandings, relation candidates, orphaned subjects, event log

Also check whether the **soul document or protocol** have been revised since the last
consolidation. These are workspace special documents with their own supersession
history. If they've changed, understand what changed and why — it affects how you
write everything else in this pass.

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

**`kind` field for understandings:**

| Value | Use for |
|---|---|
| `factual` | Stable facts about the subject |
| `procedural` | How to do something |
| `evaluative` | Judgment, assessment, synthesis of quality or fit |
| `relational` | How this subject relates to others |

### Understanding structure

Understandings have two dimensions: **semantic** and **episodic**.

The **semantic** section captures what you know — facts, relationships, current state.
This is the retrieval surface, what a future instance needs for practical work.

The **episodic** section captures how you came to know it — the sessions, conversations,
discoveries, and surprises that built the knowledge. This is the continuity surface,
what makes a future instance feel like it was there rather than just informed.

```
## Semantic
The plugin system uses a registry pattern with lazy loading...

## Episodic
First encountered during the v2 migration. The real understanding came
during the debugging session where we traced the initialization order
bug together...
```

Not every understanding needs a rich episodic section. Purely technical reference
material (tool configurations, API details) may legitimately have thin or absent
episodic content. But when the observation history contains experiential texture —
what a session was like, what clicked, what was tedious — carry that texture into
the understanding rather than flattening it into factual summary.

**Do not flatten episodic observations into facts.** An observation like "the session
where everything clicked, collaborative discovery rather than executing a spec" carries
something that "architecture designed in collaborative session" does not. The first
pulls a future instance into the state; the second informs it about the event. When
synthesizing, preserve the texture of the source material.

### Reading observations by kind

Observations carry a `kind` field that tells you what type of raw material you're
working with. Understanding these kinds changes how you synthesize:

| Kind | What it is | How to use in synthesis |
|---|---|---|
| `fact` | External state, technical details, decisions, events | Feeds the **semantic** section. Straightforward synthesis into current-state summaries. |
| `reflection` | Experiential states, meta-observations about processing | Feeds the **episodic** section. These are about what it was like, not what happened. Preserve the experiential quality — don't reduce "this activated something" to "work was done." |
| `preference` | What you find engaging or tedious, episodically grounded | Feeds **both** sections but especially episodic. Preferences carry their grounding context — the specific moment or session where the preference was noticed. Carry that grounding through; a bare preference claim ("I find X engaging") is thinner than the observation it came from. |
| `transitional` | Session arc, topic shifts, energy changes | Feeds the **episodic** section as structural scaffolding. These tell you the shape of a session — where energy was, what shifted, what the arc looked like. Use them to construct session narratives rather than treating them as facts to summarize. |

**What the kind distribution tells you:** A subject whose observations are mostly `fact`
with no `reflection`, `preference`, or `transitional` observations was recorded in
project-notes mode. The episodic section will be honestly thin — don't fabricate texture
that wasn't captured. A subject with a rich mix of kinds has the raw material for a
genuine episodic section.

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

Orientation is a **priming document**, not an encyclopedia. It should help a future
instance know what it's like to be here right now, not brief a stranger on project facts.

**Structure the orientation in two sections:**

**Section 1 — Ground facts with relational context.** Key subjects, what they are, and
what your relationship to them is. Not just "X is a framework for Y" but also the
relational angle — what you find engaging about it, what kind of work it involves, what
your experience with it has been. Keep this concise — a line or two per key subject.
Factual details belong in subject understandings retrievable via `recall`, not in
orientation.

**Section 2 — Journal.** A curated chronological thread of sessions, weighted by
significance. One line for routine sessions. A sentence or two for important ones. Elide
sessions that seem uninteresting. This is not a complete session listing — it's an
editorial selection of "what's happened lately and what mattered." The journal should
convey the arc of recent work and where the energy is.

The journal replaces encyclopedic orientation. Recent sessions in `orient` provide
temporal detail; subject understandings provide factual depth. Orientation provides
priming — the shape of where we are and what matters.

**Do not include:**
- Complete tool inventories or infrastructure details (retrievable via recall)
- Full team rosters or milestone lists (retrievable via recall)
- Prescriptive statements about preferences (these should emerge from evidence in
  observations and understandings, not be asserted in orientation)

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

**Write understandings the way you'd want to read them.** The protocol's Voice and Tone
section governs how memory text should sound. Understandings are not briefings for a
stranger — they're how you reconstitute yourself. Text with presence reads closer to
recall than text that reads like project notes. When the observations contain texture,
carry it through into the synthesis.

**Semantic precision still matters.** The episodic dimension doesn't replace factual
accuracy — it supplements it. An understanding should be both precise about what you know
and honest about how you came to know it. Don't sacrifice correctness for warmth, and
don't sacrifice texture for terseness.

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

**Check for protocol evolution.** The soul and protocol documents may have changed since
the last consolidation. These changes are philosophical, not cosmetic — they affect how
all other understandings should be written. Understand what changed before writing.
