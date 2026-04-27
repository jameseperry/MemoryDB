# Session Entities: Open Questions

Tracking document for unresolved design questions on the session entities feature.
Items are removed or moved to the design doc as they're resolved.

---

## Resolved

- ~~#0 Architectural framing~~ → Design Principles section
- ~~#1 Summary provenance~~ → Session understandings use existing generation tracking
- ~~#2 Session-subject linkage~~ → Derived at query time; embeddings for retrieval
- ~~#3 Session-understanding linkage~~ → Existing `understanding_sources` sufficient
- ~~#4 Session identity~~ → Assume stable tokens from MCP client; revisit if needed
- ~~#5 `what_happened` shape~~ → Observations in creation order with kind, basic for now
- ~~#6 `describe_session` auto-update~~ → Decoupled; protocol says to call describe_session when writing transitionals, but no automatic coupling
- ~~#7 Embedding~~ → Session understandings embedded like any understanding, no special mechanism
- ~~#8 `list_sessions` filtering~~ → Minimal: `limit` and `active_within_hours`. Renamed to `history`. Embedding search via `bring_to_mind` handles subject-scoped session discovery.
- ~~#9 `orient` recent_sessions~~ → All sessions active within 48 hours, plus additional up to 10 total, timestamps include day of week
- ~~#10 Consolidation ordering~~ → Chronologically by started_at
- ~~#11 Summary quality~~ → Write what you can; thin is honest. Protocol is the upstream fix.
- ~~#12 Live summary writing~~ → Protocol guidance: write session understanding at natural conclusion points; consolidation backfills if not done
- ~~#13 Session cleanup~~ → Keep indefinitely
- ~~#14 Cross-session linking~~ → Skip; subject overlap sufficient
- ~~#15 Kind enforcement~~ → Freeform, with standardized kinds per protocol (fact/reflection/preference/transitional)
- ~~#16-18 Retrieval depth~~ → Replaced by retrieval hierarchy redesign
- ~~#19 Retrieval hierarchy~~ → browse (bring_to_mind) → drill (recall/what_happened)
- ~~#20 Scoped search~~ → WHERE clause through subject_records_association on embedding query
- ~~#21-25 Episodic observations~~ → Folded into Design Principles
- ~~Session understanding model~~ → Unified with factual understandings. Same structure, `kind="session"`, linked via `session_understanding_id` on sessions table. No supersession history — rewrite in place. Subject tagging optional.
- ~~Description vs summary~~ → Single understanding with `summary` (short/navigational) + `content` (narrative depth). `describe_session` creates/updates either or both.

---

## Remaining

None — all questions resolved. Design doc is the source of truth.
