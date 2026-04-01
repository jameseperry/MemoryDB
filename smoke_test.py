"""Quick smoke test for core CRUD tools against live Postgres."""

import asyncio
import os

os.environ.setdefault("ASYNC_DATABASE_URL", "postgresql://memory:memory@localhost:5432/memory")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://memory:memory@localhost:5432/memory")

from memory_mcp.db import init_pool, close_pool
from memory_mcp.tools.nodes import (
    create_entities, delete_entities, open_nodes,
    get_nodes_by_type, set_summary, set_tags,
)
from memory_mcp.tools.observations import add_observations, replace_observation, delete_observations
from memory_mcp.tools.relations import create_relations, delete_relations, get_relations_between


async def main():
    await init_pool()

    # --- nodes ---
    print("create_entities...")
    result = await create_entities([
        {"name": "test_node_a", "entity_type": "test", "observations": ["first obs", "second obs"], "tags": ["smoke"]},
        {"name": "test_node_b", "entity_type": "test", "observations": ["b's first obs"]},
    ])
    assert len(result) == 2, result
    print(f"  created: {[r['name'] for r in result]}")

    print("open_nodes...")
    result = await open_nodes(["test_node_a", "test_node_b", "nonexistent"])
    assert len(result["entities"]) == 2
    assert result["not_found"] == ["nonexistent"]
    obs = result["entities"][0]["observations"]
    assert obs[0]["ordinal"] == 0 and obs[0]["content"] == "first obs"
    assert obs[1]["ordinal"] == 1 and obs[1]["content"] == "second obs"
    print(f"  entities: {[e['name'] for e in result['entities']]}, not_found: {result['not_found']}")

    print("get_nodes_by_type...")
    result = await get_nodes_by_type("test")
    assert len(result) >= 2
    print(f"  found {len(result)} nodes of type 'test'")

    print("set_summary...")
    result = await set_summary("test_node_a", "A test node for smoke testing")
    assert result["summary"] == "A test node for smoke testing"
    print(f"  summary set: {result['summary']!r}")

    print("set_tags...")
    result = await set_tags("test_node_a", ["smoke", "test", "v2"])
    assert set(result["tags"]) == {"smoke", "test", "v2"}
    print(f"  tags: {result['tags']}")

    # --- observations ---
    print("add_observations...")
    result = await add_observations([
        {"entity_name": "test_node_a", "contents": ["third obs", "fourth obs"]},
        {"entity_name": "nonexistent_node", "contents": ["should fail gracefully"]},
    ])
    assert result[0]["added"][0]["ordinal"] == 2
    assert result[1]["not_found"] is True
    print(f"  added {len(result[0]['added'])} obs to test_node_a, not_found={result[1]['not_found']}")

    print("replace_observation...")
    result = await replace_observation("test_node_a", 0, "replaced first obs")
    assert result["new_content"] == "replaced first obs"
    print(f"  replaced ordinal 0: {result['new_content']!r}")

    print("delete_observations...")
    result = await delete_observations([{"entity_name": "test_node_a", "ordinals": [1, 99]}])
    assert 1 in result[0]["deleted_ordinals"]
    assert 99 in result[0]["not_found_ordinals"]
    print(f"  deleted: {result[0]['deleted_ordinals']}, not_found: {result[0]['not_found_ordinals']}")

    # --- relations ---
    print("create_relations...")
    result = await create_relations([
        {"from_entity": "test_node_a", "to_entity": "test_node_b", "relation_type": "relates_to"},
        {"from_entity": "test_node_a", "to_entity": "test_node_b", "relation_type": "relates_to"},  # duplicate
        {"from_entity": "test_node_a", "to_entity": "ghost_node", "relation_type": "points_to"},    # missing
    ])
    assert len(result["created"]) == 1
    assert len(result["already_existed"]) == 1
    assert "ghost_node" in result["not_found"]
    print(f"  created={len(result['created'])}, already_existed={len(result['already_existed'])}, not_found={result['not_found']}")

    print("get_relations_between...")
    result = await get_relations_between("test_node_a", "test_node_b")
    assert len(result) == 1 and result[0]["relation_type"] == "relates_to"
    print(f"  {result}")

    print("delete_relations...")
    result = await delete_relations([
        {"from_entity": "test_node_a", "to_entity": "test_node_b", "relation_type": "relates_to"}
    ])
    assert result["deleted"] == 1
    print(f"  deleted={result['deleted']}")

    # --- cleanup ---
    print("delete_entities...")
    result = await delete_entities(["test_node_a", "test_node_b", "nonexistent"])
    assert set(result["deleted"]) == {"test_node_a", "test_node_b"}
    assert result["not_found"] == ["nonexistent"]
    print(f"  deleted={result['deleted']}, not_found={result['not_found']}")

    print("\nAll smoke tests passed.")
    await close_pool()


asyncio.run(main())
