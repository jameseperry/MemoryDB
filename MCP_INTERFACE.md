# Memory MCP Server — Interface Specification

## Architecture

FastMCP server (Python, Linux) backed by Postgres + pgvector + Ollama.
Exposes tools over SSE transport for remote access by Claude clients.

No separate gateway layer — FastMCP handles the MCP protocol directly.

---

## Concepts

- **Node**: named entity with a type, optional summary, optional tags, and an ordered list of observations.
- **Observation**: a string fact attached to a node, with a stable per-node ordinal. Reads always return in ordinal order.
- **Relation**: directed, typed edge between two nodes (`from_node` → `to_node` with a `relation_type` string).
- **Workspace**: namespace for all data. `workspace_id` is on every table; nullable = default workspace. Enables per-user or shared spaces.
- **Embedding**: each observation is embedded from multiple perspectives (general, technical, relational, temporal, project). Search runs across all perspectives automatically.

---

## Tool Catalog

### Node Management

#### `create_entities`
Create one or more nodes.

```
params:
  entities: list of {
    name: str                  # unique within workspace
    entity_type: str
    observations: list[str]    # optional initial observations, appended in order
    summary: str | None
    tags: list[str]            # optional
  }
  workspace: str | None        # defaults to default workspace

returns:
  list of { name, entity_type, created_at }
```

Schema needs: `nodes` table with `(workspace_id, name)` unique constraint.

---

#### `delete_entities`
Delete nodes by name, including all their observations, relations, embeddings, and events.

```
params:
  entity_names: list[str]
  workspace: str | None

returns:
  { deleted: list[str], not_found: list[str] }
```

Schema needs: cascade deletes from `nodes` → `observations`, `relations`, `embeddings`, `events`.

---

#### `open_nodes`
Retrieve full node content by name, including all observations (ordered by ordinal), summary, tags, and relation stubs.

```
params:
  names: list[str]
  workspace: str | None

returns:
  {
    entities: list of {
      name, entity_type, summary, tags,
      observations: list of { ordinal: int, content: str },
      created_at, updated_at
    }
    relations: list of { from: str, to: str, relation_type: str }
                        # all relations where either endpoint is in the requested set
    not_found: list[str]
  }
```

Schema needs: `observations` table with `(node_id, ordinal)`. Relations query needs both directions.

---

#### `get_nodes_by_type`
List all nodes of a given entity type.

```
params:
  entity_type: str
  workspace: str | None

returns:
  list of { name, entity_type, summary, tags, updated_at }
```

Schema needs: index on `(workspace_id, entity_type)`.

---

#### `get_recently_modified`
Return nodes modified in the last N days, ordered by `updated_at` descending.

```
params:
  days: int = 7
  limit: int = 20
  workspace: str | None

returns:
  list of { name, entity_type, summary, updated_at }
```

Schema needs: `updated_at` timestamp on `nodes`, updated by trigger on any child write.

---

#### `set_summary`
Set or replace the summary field on a node.

```
params:
  name: str
  summary: str
  workspace: str | None

returns:
  { name, summary, updated_at }
```

---

#### `set_tags`
Replace the tag set on a node.

```
params:
  name: str
  tags: list[str]
  workspace: str | None

returns:
  { name, tags }
```

Schema needs: tags as array column on `nodes`, or a separate `tags` junction table. Array column is simpler and sufficient.

---

### Observation Management

#### `add_observations`
Append observations to existing nodes. Each observation gets the next ordinal for that node.

```
params:
  observations: list of {
    entity_name: str
    contents: list[str]    # appended in order, ordinals assigned sequentially
  }
  workspace: str | None

returns:
  list of {
    entity_name: str
    added: list of { ordinal: int, content: str }
    not_found: bool
  }
```

Schema needs: `MAX(ordinal) + 1` per node for new ordinal assignment.

---

#### `replace_observation`
Replace a single observation by its ordinal. Preserves ordinal (in-place replacement).

```
params:
  entity_name: str
  ordinal: int
  new_content: str
  workspace: str | None

returns:
  { entity_name, ordinal, old_content: str, new_content: str }
```

Schema needs: `UPDATE observations SET content = $1 WHERE node_id = $2 AND ordinal = $3`.

---

#### `delete_observations`
Delete specific observations by ordinal. Ordinals of remaining observations are not renumbered.

```
params:
  deletions: list of {
    entity_name: str
    ordinals: list[int]
  }
  workspace: str | None

returns:
  list of { entity_name, deleted_ordinals: list[int], not_found_ordinals: list[int] }
```

---

#### `query_observations`
Search within a single node's observations. Supports both embedding similarity (default)
and full-text search. Embedding search is the primary mode; FTS is useful when you need
exact-term matching or when the embedding pipeline is unavailable.

```
params:
  entity_name: str
  query: str
  mode: "embedding" | "text" = "embedding"
  workspace: str | None

returns:
  list of { ordinal: int, content: str, score: float }
```

Schema needs:
- Embedding mode: reuses `embeddings` table, filters by node.
- Text mode: `tsvector` generated column on `observations.content` with GIN index.
  Note: text mode scores are keyword match weights, not semantic rankings. Use embedding mode when ranking quality matters.

---

### Relation Management

#### `create_relations`
Create directed typed edges between nodes.

```
params:
  relations: list of {
    from_entity: str
    to_entity: str
    relation_type: str
  }
  workspace: str | None

returns:
  { created: list[relation], already_existed: list[relation], not_found: list[str] }
```

Schema needs: `(workspace_id, from_node_id, to_node_id, relation_type)` unique constraint.

---

#### `delete_relations`
Delete specific relations.

```
params:
  relations: list of {
    from_entity: str
    to_entity: str
    relation_type: str
  }
  workspace: str | None

returns:
  { deleted: int, not_found: int }
```

---

#### `update_relation_type`
Rename the type string on an existing relation.

```
params:
  from_entity: str
  to_entity: str
  old_type: str
  new_type: str
  workspace: str | None

returns:
  { from_entity, to_entity, old_type, new_type }
```

---

#### `get_relations_between`
Return all relations between two specific nodes (both directions).

```
params:
  entity_a: str
  entity_b: str
  workspace: str | None

returns:
  list of { from: str, to: str, relation_type: str }
```

---

### Graph Traversal

#### `get_neighborhood`
Return a node and all nodes within N hops, with the subgraph of relations between them.

```
params:
  name: str
  depth: int = 1             # number of hops
  workspace: str | None

returns:
  {
    nodes: list of { name, entity_type, summary, tags }
    relations: list of { from, to, relation_type }
  }
```

Schema needs: recursive CTE or application-level BFS. Recursive CTE preferred.

---

#### `get_path`
Find the shortest relation path between two nodes.

```
params:
  from_entity: str
  to_entity: str
  workspace: str | None

returns:
  {
    found: bool
    path: list[str]          # ordered node names from source to target
    relations: list of { from, to, relation_type }
  }
```

Schema needs: recursive CTE with path tracking.

---

#### `get_orphans`
Return nodes with no relations.

```
params:
  workspace: str | None

returns:
  list of { name, entity_type, summary, updated_at }
```

---

#### `get_relation_gaps`
Return nodes that have observations referencing other node names but no formal relation to them.
(Useful for finding implicit connections that should be explicit.)

```
params:
  workspace: str | None

returns:
  list of { node: str, referenced_name: str, reference_count: int }
```

Note: implemented as text-matching against known node names in observation content.
This is heuristic — results need Claude's judgment to confirm if a relation is warranted.

---

#### `find_similar_nodes`
Find pairs of nodes that are semantically similar based on their full observation content,
but have no existing relation between them. Uses per-node aggregate embeddings.

Useful during consolidation to surface candidates for explicit relations, merges, or grouping.

```
params:
  workspace: str | None
  limit: int = 20              # top N most similar unrelated pairs
  min_score: float = 0.75      # cosine similarity threshold

returns:
  list of {
    node_a: str
    node_b: str
    similarity: float
    node_a_type: str
    node_b_type: str
  }
```

Schema needs: per-node aggregate embedding (mean-pooled across all observation embeddings for a
given perspective). Stored in a `node_embeddings` table or computed on demand.
HNSW index enables approximate nearest-neighbor search over node-level vectors.

---

### Search

#### `search_nodes`
Search across all nodes. Default mode is semantic (multi-perspective embeddings);
FTS mode is available for exact-term or keyword queries.

In embedding mode: query is embedded once per perspective, parallel searches run,
results are merged and deduplicated, best score and matched perspective returned.

In text mode: Postgres full-text search across all observation content, ranked by ts_rank.

```
params:
  query: str
  limit: int = 10
  mode: "embedding" | "text" = "embedding"
  workspace: str | None

returns:
  list of {
    name: str
    entity_type: str
    summary: str | None
    matched_observation: str       # the observation that matched
    matched_perspective: str | None  # which perspective matched (embedding mode only)
    score: float
  }
```

Schema needs:
- Embedding mode: `embeddings` table keyed by `(observation_id, perspective_id)` with vector column, HNSW index.
- Text mode: `tsvector` generated column on `observations.content` with GIN index (shared with `query_observations`).
  Note: text mode scores (`ts_rank`) are keyword match weights, not semantic rankings. Use as a filter with approximate ordering,
  not as a reliable relevance signal. Prefer embedding mode when ranking quality matters.

---

### Consolidation

#### `get_consolidation_report`
Returns a structured report identifying areas where the graph needs attention.
Backend provides computational candidates; Claude applies judgment.

```
params:
  workspace: str | None

returns:
  {
    stale_summaries: list of {
      name: str
      entity_type: str
      last_modified: datetime
      observation_count: int
      draft_summary: str | None    # auto-generated by local model if available
    }
    relation_candidates: list of {
      node_a: str
      node_b: str
      similarity_score: float
      rationale: str | None        # from local model
    }
    orphaned_nodes: list of { name, entity_type, updated_at }
    cluster_summary: list of {
      cluster_id: int
      node_names: list[str]
      centroid_label: str | None
    }
    event_summary: {
      since: datetime
      creates: int
      updates: int
      deletes: int
    }
  }
```

Schema needs: `events` table with operation type + timestamp. Embedding similarity for relation candidates.

---

#### `get_pending_consolidation`
Lightweight version of consolidation report — just the nodes that haven't been summarized
or haven't had their summary updated since their last observation was added.

```
params:
  workspace: str | None

returns:
  list of { name, entity_type, observation_count, last_observation_at, summary_updated_at | None }
```

---

#### `get_stats`
Summary statistics about the workspace.

```
params:
  workspace: str | None

returns:
  {
    node_count: int
    observation_count: int
    relation_count: int
    embedding_coverage: float      # fraction of observations with embeddings
    workspace: str | None
  }
```

---

## Schema Checklist

Things the schema must support, derived from the tool catalog above:

| Requirement | Tool(s) |
|---|---|
| `(workspace_id, name)` unique on nodes | `create_entities`, all lookups |
| `observations` table with `(node_id, ordinal)`, stable ordinals | `add_observations`, `replace_observation`, `delete_observations` |
| Cascade delete nodes → observations, relations, embeddings, events | `delete_entities` |
| `updated_at` on nodes, updated by trigger on child writes | `get_recently_modified` |
| Index on `(workspace_id, entity_type)` | `get_nodes_by_type` |
| `(workspace_id, from_id, to_id, relation_type)` unique on relations | `create_relations` |
| `embeddings` table: `(observation_id, perspective_id)` → vector | `search_nodes`, `query_observations`, consolidation |
| `node_embeddings` table (or materialized): per-node aggregate vector per perspective | `find_similar_nodes` |
| HNSW index on embedding vectors (both tables) | `search_nodes`, `find_similar_nodes` |
| `tsvector` generated column on `observations.content` + GIN index | `search_nodes` (text mode), `query_observations` (text mode) |
| `events` table: node_id, operation, timestamp | `get_consolidation_report`, `get_stats` |
| `perspectives` table (configurable, per-workspace) | embedding pipeline |
| Tags as array column on nodes | `set_tags`, `get_nodes_by_type` |
| Recursive CTE support (Postgres native) | `get_neighborhood`, `get_path` |
| `summary_updated_at` on nodes (separate from `updated_at`) | `get_pending_consolidation` |

---

## Transport

FastMCP SSE transport. Claude clients connect via HTTP to the Linux server.
No gateway process required.

Default port: `8765` (configurable via env).
