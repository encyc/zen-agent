#!/usr/bin/env python3
"""The LOC budget gate (PI-Desktop's check-architecture, scaled down).

Rules:
* Source files above ``DEFAULT_CAP`` lines are over budget — unless they are
  on the allowlist, which records each legacy file's size at registration.
* Allowlisted files may only shrink: growing past the recorded number fails
  the gate, so debt can be paid down but never quietly increased.

Scope: ``zenagent/**.py`` (the shipped package), ``desktop/src`` +
``desktop/e2e`` + ``desktop/tests`` (TS/TSX). Stdlib only; exits 1 with the
offending files listed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CAP = 800

PY_ROOTS = ["zenagent"]
TS_ROOTS = ["desktop/src", "desktop/e2e", "desktop/tests"]


def _scan(roots: list[str], suffixes: tuple[str, ...]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for root in roots:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix in suffixes and path.is_file():
                if "__pycache__" in path.parts or "node_modules" in path.parts:
                    continue
                rel = str(path.relative_to(REPO))
                sizes[rel] = len(path.read_text(encoding="utf-8").splitlines())
    return sizes


def _load_allowlist() -> dict:
    allowlist = REPO / "docs" / "architecture" / "allowlist.json"
    if allowlist.is_file():
        return json.loads(allowlist.read_text(encoding="utf-8"))
    return {"python": {}, "ts": {}}


def check(root: Path | None = None) -> list[str]:
    """Return the list of violations (empty = clean)."""
    global REPO
    if root is not None:
        REPO = root
    allowlist = _load_allowlist()
    violations: list[str] = []

    py = _scan(PY_ROOTS, (".py",))
    budget = allowlist.get("python", {})
    for path, lines in sorted(py.items()):
        cap = budget.get(path, DEFAULT_CAP)
        if lines > cap:
            note = f"(allowlisted at {cap})" if path in budget else "(new file, cap 800)"
            violations.append(f"{path}: {lines} lines {note}")

    ts = _scan(TS_ROOTS, (".ts", ".tsx"))
    budget = allowlist.get("ts", {})
    for path, lines in sorted(ts.items()):
        cap = budget.get(path, DEFAULT_CAP)
        if lines > cap:
            note = f"(allowlisted at {cap})" if path in budget else "(new file, cap 800)"
            violations.append(f"{path}: {lines} lines {note}")

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="scan this tree instead of the repo (for the self-test)",
    )
    args = parser.parse_args()
    violations = check(args.root)
    if violations:
        print("architecture budget exceeded:")
        for line in violations:
            print(f"  {line}")
        return 1
    print("architecture budget: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
