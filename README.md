# MemoryDB

MemoryDB is a Postgres-backed memory server built with FastMCP.

It provides:
- MCP tools for subjects, observations, understandings, retrieval, and consolidation
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
The server serves the v3 API at `/v3/mcp` and `/v3/sse`.

The Docker Compose setup creates `memory_v3` on first initialization. On an
already-initialized Postgres volume, `scripts/start.sh` also checks for
`memory_v3` and creates it before running migrations.

## Admin CLI

Examples:

```bash
memory-admin workspace list
memory-admin workspace create james/gpt
memory-admin workspace remove james/gpt
memory-admin workspace set-documents james/gpt --soul 101 --protocol 102 --orientation 103
memory-admin database backup backup.sql
memory-admin database restore backup.sql --yes
```

## Onboarding a model

When connecting a new model instance to a workspace for the first time, point it at
[`docs/onboarding.md`](./docs/onboarding.md). That document explains the data model,
connection setup, core workflow, and how to bootstrap the workspace special documents
from the seed files in [`docs/seeds/`](./docs/seeds/).

## Notes

- MCP interface details: [`MCP_INTERFACE.md`](./MCP_INTERFACE.md)
- API proposal: [`MEMORY_MCP_API_PROPOSAL_v3.md`](./MEMORY_MCP_API_PROPOSAL_v3.md)
