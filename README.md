# MemoryDB

MemoryDB is a Postgres-backed memory server built with FastMCP.

It provides:
- MCP tools for subjects, observations, understandings, retrieval, and consolidation
- dual MCP transports for Streamable HTTP and SSE clients
- an admin CLI for workspace and database management

## Quick Start (Docker)

```bash
# 1. Download the embedding model (~500MB, one-time)
HF_TOKEN=hf_... python docker/download_model.py

# 2. Build and run
docker compose build
docker compose up -d

# 3. Create a workspace
docker compose exec memory memory-admin workspace create <name>
```

The server listens on port `8765` and serves the v3 API at `/v3/mcp` and `/v3/sse`.

If you're behind a corporate proxy, drop your CA certificate into `docker/certs/`
before building.

## Local Development

Requirements: Python 3.10+, Docker Compose (for Postgres).

```bash
# Set up the virtualenv
./scripts/setup.sh

# Start Postgres (dev mode — exposes port 19432 to host)
docker compose -f docker-compose.dev.yml up -d

# Run migrations and launch the server
./scripts/start.sh
```

## Admin CLI

```bash
memory-admin workspace list
memory-admin workspace create alice/claude
memory-admin workspace remove alice/claude
memory-admin workspace set-documents alice/claude --soul 101 --protocol 102
memory-admin database backup backup.sql
memory-admin database restore backup.sql --yes
```

Inside Docker:
```bash
docker compose exec memory memory-admin workspace list
```

## Onboarding a model

When connecting a new model instance to a workspace for the first time, point it at
[`docs/onboarding.md`](./docs/onboarding.md). That document explains the data model,
connection setup, core workflow, and how to bootstrap the workspace special documents
from the seed files in [`docs/seeds/`](./docs/seeds/).

## Notes

- MCP interface details: [`MCP_INTERFACE.md`](./MCP_INTERFACE.md)
- API proposal: [`MEMORY_MCP_API_PROPOSAL_v3.md`](./MEMORY_MCP_API_PROPOSAL_v3.md)
