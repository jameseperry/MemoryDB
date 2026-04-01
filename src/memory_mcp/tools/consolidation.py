"""Consolidation and stats tools."""


async def get_consolidation_report(
    workspace: str | None = None,
) -> dict:
    """Full consolidation report: stale summaries, relation candidates, orphans, clusters, event log.

    Backend computes candidates; Claude applies judgment.
    Returns {stale_summaries, relation_candidates, orphaned_nodes, cluster_summary, event_summary}.
    """
    raise NotImplementedError


async def get_pending_consolidation(
    workspace: str | None = None,
) -> list[dict]:
    """Nodes that have no summary, or whose summary predates their last observation.

    Returns list of {name, entity_type, observation_count, last_observation_at, summary_updated_at}.
    """
    raise NotImplementedError


async def get_stats(
    workspace: str | None = None,
) -> dict:
    """Summary statistics for the workspace.

    Returns {node_count, observation_count, relation_count, embedding_coverage, workspace}.
    """
    raise NotImplementedError
