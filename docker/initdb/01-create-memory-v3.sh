#!/usr/bin/env bash
set -euo pipefail

if psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -tAc "SELECT 1 FROM pg_database WHERE datname = 'memory_v3'" | grep -q 1; then
    exit 0
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -c "CREATE DATABASE memory_v3"
