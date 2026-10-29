#!/usr/bin/env bash
# Behavioral evals (real model, costs money). Not part of scripts/check.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python scripts/eval.py "$@"
