"""Graph traversal tools."""


async def get_neighborhood(
    name: str,
    depth: int = 1,
    workspace: str | None = None,
) -> dict:
    """Return a node and all nodes within N hops, with the connecting subgraph.

    Returns {nodes: [{name, entity_type, summary, tags}], relations: [{from, to, relation_type}]}.
    """
    raise NotImplementedError


async def get_path(
    from_entity: str,
    to_entity: str,
    workspace: str | None = None,
) -> dict:
    """Find shortest relation path between two nodes.

    Returns {found: bool, path: [str], relations: [{from, to, relation_type}]}.
    """
    raise NotImplementedError


async def get_orphans(
    workspace: str | None = None,
) -> list[dict]:
    """Return nodes with no relations.

    Returns list of {name, entity_type, summary, updated_at}.
    """
    raise NotImplementedError


async def get_relation_gaps(
    workspace: str | None = None,
) -> list[dict]:
    """Find nodes referenced by name in other nodes' observations but with no formal relation.

    Heuristic text-match — results need judgment to confirm. Consolidation aid.
    Returns list of {node, referenced_name, reference_count}.
    """
    raise NotImplementedError


async def find_similar_nodes(
    workspace: str | None = None,
    limit: int = 20,
    min_score: float = 0.75,
) -> list[dict]:
    """Find pairs of nodes that are semantically similar but have no existing relation.

    Uses per-node aggregate embeddings (mean-pooled across observations).
    Returns list of {node_a, node_b, similarity, node_a_type, node_b_type}.
    """
    raise NotImplementedError
