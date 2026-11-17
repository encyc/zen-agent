#!/usr/bin/env python3
"""The LOC budget gate (PI-Desktop's check-architecture, scaled down).

Rules:
* Code files above ``DEFAULT_CAP`` lines are over budget — unless they are
  on the allowlist, which records each legacy file's size at registration.
* Catalog files (message dictionaries, label tables) are measured in *entries*,
  not lines — a navigation budget rather than a coupling budget. See
  ``CATALOG_CAP`` and the rationale below.
* Allowlisted files may only shrink: growing past the recorded number fails
  the gate, so debt can be paid down but never quietly increased.

Why catalogs get their own metric: LOC is a proxy for coupling, and a file that
is 95% ``'key': 'value',`` has no coupling to measure — but it does have a
navigation cost, bounded by how many entries you scroll past. Public i18n
guidance (i18next namespaces, Rails' ``config/locales``) draws the same line:
split when a file stops being scannable, not when it stops being small.
An entry is one ``key: value`` line, so the two catalog shapes in this repo are
measured on the same footing *per line of scroll*:

* GUI — ``messages*.ts`` holds both locales, so each key costs 2 entries.
* TUI — ``i18n/{en,zh}.py`` holds one locale, so each key costs 1 entry.

This is deliberately not language-neutral: a bilingual file does read twice as
long, and the budget says so. If a third locale arrives, the honest response is
a split (per locale, the TUI shape; or per domain, the Rails shape) rather than
a bigger cap.

The entry ratio is the structural guard: a file in catalog territory may carry
at most ``(1 - CATALOG_ENTRY_RATIO) / CATALOG_ENTRY_RATIO`` code lines per
entry (~18% of its lines). Bloat it with logic and it drops out of catalog
territory, lands back under the line cap, and fails — so "hide the logic in the
dictionary" is not a way out.

Scope: ``zenagent/**.py`` (the shipped package), ``desktop/src`` +
``desktop/e2e`` + ``desktop/tests`` (TS/TSX). Stdlib only; exits 1 with the
offending files listed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CAP = 800
CATALOG_CAP = 800

#: A file must clear all three to be measured as a catalog: enough entries to
#: be one at all, and a ratio high enough that its code lines stay bounded.
CATALOG_MIN_ENTRIES = 50
CATALOG_ENTRY_RATIO = 0.85

PY_ROOTS = ["zenagent"]
TS_ROOTS = ["desktop/src", "desktop/e2e", "desktop/tests"]

# One catalog entry on one line: `'key': 'value',` / `"key": "value"` / `key: 'value'`.
_QUOTED = r"""'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|`(?:[^`\\]|\\.)*`"""
_PY_ENTRY = re.compile(
    rf"""^\s*(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")\s*:\s*(?:{_QUOTED})\s*,?\s*$"""
)
_TS_ENTRY = re.compile(
    rf"""^\s*(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|[A-Za-z_$][\w$]*)\s*:\s*(?:{_QUOTED})\s*,?\s*$"""
)
_COMMENT_PREFIXES = ("//", "#", "/*", "*", "*/", "--")


class Measure(NamedTuple):
    """One file's cost: its line count, plus its entry count when it is a catalog."""

    lines: int
    entries: int | None


def _measure(path: Path) -> Measure:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    entry_re = _PY_ENTRY if path.suffix == ".py" else _TS_ENTRY
    entries = 0
    code = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(_COMMENT_PREFIXES):
            continue
        if entry_re.match(line):
            entries += 1
        else:
            code += 1
    counted = entries + code
    if entries < CATALOG_MIN_ENTRIES or not counted:
        return Measure(len(lines), None)
    if entries / counted < CATALOG_ENTRY_RATIO:
        return Measure(len(lines), None)
    return Measure(len(lines), entries)


def _scan(roots: list[str], suffixes: tuple[str, ...]) -> dict[str, Measure]:
    sizes: dict[str, Measure] = {}
    for root in roots:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix in suffixes and path.is_file():
                if "__pycache__" in path.parts or "node_modules" in path.parts:
                    continue
                sizes[str(path.relative_to(REPO))] = _measure(path)
    return sizes


def _load_allowlist() -> dict:
    allowlist = REPO / "docs" / "architecture" / "allowlist.json"
    if allowlist.is_file():
        return json.loads(allowlist.read_text(encoding="utf-8"))
    return {"python": {}, "ts": {}}


#: Top-level allowlist keys the gate actually reads, plus the note block.
_ALLOWLIST_SECTIONS = ("python", "ts", "_notes")


def _stray_allowlist_keys(allowlist: dict) -> list[str]:
    """Bare top-level entries are dead config: the gate never reads them.

    Three real paths drifted up there and sat unread for days, which is worse
    than a violation — a cap nobody enforces reads like a cap that is holding.
    """
    return [
        f'docs/architecture/allowlist.json: stray top-level key "{key}": '
        f"{value} (move it under "
        f'"{("ts" if str(key).startswith("desktop/") else "python")}")'
        for key, value in allowlist.items()
        if key not in _ALLOWLIST_SECTIONS
    ]


def _violations(files: dict[str, Measure], budget: dict[str, int]) -> list[str]:
    out: list[str] = []
    for path, (lines, entries) in sorted(files.items()):
        if entries is not None:
            cap = budget.get(path, CATALOG_CAP)
            if entries > cap:
                note = (
                    f"(allowlisted at {cap})"
                    if path in budget
                    else f"(catalog cap {cap})"
                )
                out.append(f"{path}: {entries} entries {note}")
            continue
        cap = budget.get(path, DEFAULT_CAP)
        if lines > cap:
            note = (
                f"(allowlisted at {cap})" if path in budget else "(new file, cap 800)"
            )
            out.append(f"{path}: {lines} lines {note}")
    return out


def check(root: Path | None = None) -> list[str]:
    """Return the list of violations (empty = clean)."""
    global REPO
    if root is not None:
        REPO = root
    allowlist = _load_allowlist()
    return [
        *_stray_allowlist_keys(allowlist),
        *_violations(_scan(PY_ROOTS, (".py",)), allowlist.get("python", {})),
        *_violations(_scan(TS_ROOTS, (".ts", ".tsx")), allowlist.get("ts", {})),
    ]


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
