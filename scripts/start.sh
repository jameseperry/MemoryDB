#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Load .env if present
if [[ -f .env ]]; then
    set -a; source .env; set +a
fi

# --- Start Postgres if not already running ---
echo "==> Starting Postgres..."
docker compose up -d postgres

echo "==> Waiting for Postgres to be ready..."
until docker compose exec -T postgres pg_isready -U memory -q; do
    sleep 1
done
echo "    Postgres is ready."

# --- Run migrations ---
echo "==> Running migrations..."
DATABASE_URL="${DATABASE_URL:-postgresql+psycopg2://memory:memory@localhost:5432/memory}" \
    .venv/bin/alembic upgrade head

# --- Start the MCP server ---
echo "==> Starting memory MCP server on port ${MCP_PORT:-8765}..."
exec .venv/bin/memory-mcp
