# MemoryDB

MemoryDB is a Postgres-backed memory server built with FastMCP.

It provides:
- MCP tools for nodes, observations, relations, search, graph traversal, and consolidation
- dual MCP transports for Streamable HTTP and SSE clients
- an admin CLI for workspace and database management

## Requirements

- Python 3.12+
- Docker Compose

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Run

Start Postgres, run migrations, and launch the MCP server:

```bash
./scripts/start.sh
```

The server listens on port `8765` by default.

## Admin CLI

Examples:

```bash
memory-admin workspace list
memory-admin workspace create james/gpt
memory-admin workspace rehome-null james/gpt
memory-admin database backup backup.sql
memory-admin database restore backup.sql --yes
```

## Notes

- MCP interface details: [`MCP_INTERFACE.md`](./MCP_INTERFACE.md)
- API proposal: [`MEMORY_MCP_API_PROPOSAL_v3.md`](./MEMORY_MCP_API_PROPOSAL_v3.md)
