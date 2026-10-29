#!/usr/bin/env bash
# The single gate: CI runs exactly this, so local and CI can never drift.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> sync (kernel + cli extras + dev tools)"
uv sync --extra cli --dev

echo "==> lint"
uv run ruff check .

echo "==> tests"
uv run pytest -q
