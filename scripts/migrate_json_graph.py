"""Migrate the legacy NDJSON memory graph into the new Postgres-backed store.

Usage:
    .venv/bin/python scripts/migrate_json_graph.py <path-to-memory.json> [--workspace NAME]

Options:
    --workspace NAME   Import into an existing named workspace
    --dry-run          Parse and report what would be imported, without writing
    --skip-embeddings  Skip embedding generation (faster; run search won't work until re-embedded)

The source format is NDJSON (one JSON object per line):
    {"type": "entity", "name": "...", "entityType": "...", "observations": [...]}
    {"type": "relation", "from": "...", "to": "...", "relationType": "..."}
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Allow running from project root without installing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

os.environ.setdefault("ASYNC_DATABASE_URL", "postgresql://memory:memory@localhost:5432/memory")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://memory:memory@localhost:5432/memory")

from memory_mcp.db import close_pool, init_pool
from memory_mcp.tools.nodes import create_entities
from memory_mcp.tools.relations import create_relations


def parse_ndjson(path: Path) -> tuple[list[dict], list[dict]]:
    entities, relations = [], []
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  WARNING: skipping line {lineno} (parse error: {e})")
                continue
            if obj.get("type") == "entity":
                entities.append(obj)
            elif obj.get("type") == "relation":
                relations.append(obj)
            else:
                print(f"  WARNING: skipping line {lineno} (unknown type: {obj.get('type')!r})")
    return entities, relations


async def migrate(
    path: Path,
    workspace: str,
    dry_run: bool,
    skip_embeddings: bool,
) -> None:
    entities, relations = parse_ndjson(path)
    print(f"Parsed: {len(entities)} entities, {len(relations)} relations")

    if dry_run:
        print("\n-- Dry run: no changes written --")
        print("\nEntities:")
        for e in entities:
            print(f"  [{e['entityType']}] {e['name']}  ({len(e.get('observations', []))} obs)")
        print("\nRelations:")
        for r in relations:
            print(f"  {r['from']} --[{r['relationType']}]--> {r['to']}")
        return

    if skip_embeddings:
        # Monkey-patch embed_observations to a no-op so writes are fast
        import memory_mcp.embeddings as emb
        async def _noop(*args, **kwargs): pass
        emb.embed_observations = _noop

    await init_pool()

    # --- Entities ---
    print(f"\nImporting entities into workspace={workspace!r}...")
    entity_payloads = [
        {
            "name": e["name"],
            "entity_type": e["entityType"],
            "observations": e.get("observations", []),
        }
        for e in entities
    ]

    # create_entities handles batches; import all at once
    results = await create_entities(entity_payloads, workspace=workspace)
    print(f"  Imported {len(results)} entities.")

    # --- Relations ---
    print(f"Importing relations...")
    relation_payloads = [
        {
            "from_entity": r["from"],
            "to_entity": r["to"],
            "relation_type": r["relationType"],
        }
        for r in relations
    ]

    result = await create_relations(relation_payloads, workspace=workspace)
    print(f"  Created:        {len(result['created'])}")
    print(f"  Already existed: {len(result['already_existed'])}")
    if result["not_found"]:
        print(f"  Skipped (node not found): {result['not_found']}")

    await close_pool()
    print("\nMigration complete.")


def main():
    parser = argparse.ArgumentParser(description="Migrate NDJSON memory graph to Postgres.")
    parser.add_argument("path", help="Path to the .json NDJSON graph file")
    parser.add_argument("--workspace", required=True, help="Target workspace name (must already exist)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report without writing")
    parser.add_argument("--skip-embeddings", action="store_true", help="Skip embedding generation")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"Error: file not found: {path}")
        sys.exit(1)

    asyncio.run(migrate(path, args.workspace, args.dry_run, args.skip_embeddings))


if __name__ == "__main__":
    main()
