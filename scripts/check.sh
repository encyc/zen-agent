#!/usr/bin/env bash
# The single gate: CI runs exactly this, so local and CI can never drift.
set -euo pipefail

cd "$(dirname "$0")/.."

# A virtualenv bakes its absolute path into activate files and console-script
# shebangs, so a renamed/moved checkout keeps pointing at the old directory and
# every entrypoint dies with "bad interpreter" or "No module named zenagent".
# Rebuild it here rather than fail later somewhere confusing.
if [ -f .venv/bin/activate ]; then
  recorded_env="$(sed -n "s/^VIRTUAL_ENV='\(.*\)'$/\1/p" .venv/bin/activate | head -n 1)"
  current_env="$(cd .venv && pwd -P)"
  if [ -n "$recorded_env" ] && [ "$recorded_env" != "$current_env" ]; then
    echo "==> .venv was created for $recorded_env; rebuilding for $current_env"
    rm -rf .venv
  fi
fi

echo "==> sync (kernel + cli extras + dev tools)"
uv sync --extra cli --dev

echo "==> lint"
uv run ruff check .

echo "==> tests"
uv run pytest -q
