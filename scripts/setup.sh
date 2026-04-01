#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "==> Creating virtual environment..."
python3 -m venv .venv

echo "==> Installing dependencies..."
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install psycopg2-binary -q   # Alembic needs a sync driver
.venv/bin/pip install -e ".[dev]" -q

echo "==> Installing PyTorch (CPU build — swap for CUDA build if needed)..."
echo "    For CUDA 12.4: pip install torch --index-url https://download.pytorch.org/whl/cu124"
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu -q

echo "==> Done. To activate: source .venv/bin/activate"
