#!/usr/bin/env bash
set -euo pipefail

DB_HOST="${DB_HOST:-postgres}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-memory}"
DB_NAME="${DB_NAME:-memory_v3}"

# --- Wait for Postgres ---
echo "==> Waiting for Postgres at ${DB_HOST}:${DB_PORT}..."
until python -c "
import socket, sys
s = socket.socket()
try:
    s.settimeout(2)
    s.connect(('${DB_HOST}', ${DB_PORT}))
    s.close()
except Exception:
    sys.exit(1)
" 2>/dev/null; do
    sleep 1
done
echo "    Postgres is reachable."

# --- Ensure memory_v3 database exists ---
echo "==> Ensuring ${DB_NAME} database exists..."
python -c "
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

conn = psycopg2.connect(host='${DB_HOST}', port=${DB_PORT}, user='${DB_USER}', password='${DB_USER}', dbname='postgres')
conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
cur = conn.cursor()
cur.execute(\"SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'\")
if not cur.fetchone():
    cur.execute('CREATE DATABASE ${DB_NAME}')
    print('    Created database ${DB_NAME}.')
else:
    print('    Database ${DB_NAME} already exists.')
cur.close()
conn.close()
"

# --- Run migrations ---
echo "==> Running migrations..."
alembic -c alembic.ini upgrade head

# --- Start the MCP server ---
echo "==> Starting memory MCP server on port ${MCP_PORT:-8765}..."
exec memory-mcp
