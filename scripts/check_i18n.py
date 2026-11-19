#!/usr/bin/env python3
"""The i18n orphan report: catalog keys no call site names.

The call-site guards (tests/test_i18n.py for the TUI, desktop/tests/
i18nKeys.test.ts for the GUI) pin the direction that can ship a raw key to a
user. The other direction drifts quieter: a key whose last caller disappears
stays in both catalogs forever — dead weight in files the LOC budget already
measures. This report counts them, per stack, so the number has a baseline
and the shrink is visible. Report-only, by design: some orphans are load-
bearing in ways a literal scan cannot see (dynamic keys built at runtime,
test-only keys like the ``test.*`` smoke entries), so gating on zero would
lie. Stdlib only, like the other check scripts.

Usage: ``python3 scripts/check_i18n.py`` (print) — exit code is always 0.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _tui_catalog_keys() -> set[str]:
    sys.path.insert(0, str(REPO))
    from zenagent.i18n import _catalogs  # noqa: PLC0415 - report-only script

    return set(_catalogs()["en"])


def _tui_call_site_keys() -> set[str]:
    keys: set[str] = set()
    for path in (REPO / "zenagent").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None)
            )
            if name not in ("t", "tn") or not node.args:
                continue
            arg = node.args[0]
            if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                continue
            keys.add(arg.value if name == "t" else f"{arg.value}.one")
    return keys


_GUI_CALL_SITE = re.compile(r"\bt\(\s*['\"]([a-zA-Z][\w.]*)['\"]")
_GUI_TRANSLATE = re.compile(r"translate\(\s*\w+\s*,\s*['\"]([a-zA-Z][\w.]*)['\"]")


def _gui_catalog_keys() -> set[str]:
    """Load the GUI catalogs through node (they are TS modules)."""
    script = (
        "import { zh } from './desktop/src/i18n/messages.ts';"
        "console.log(Object.keys(zh).join('\\n'))"
    )
    out = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    return set(out.stdout.split())


def _gui_call_site_keys() -> set[str]:
    keys: set[str] = set()
    src = REPO / "desktop" / "src"
    for path in list(src.rglob("*.ts")) + list(src.rglob("*.tsx")):
        if path.name.endswith(".gen.ts"):
            continue
        text = path.read_text(encoding="utf-8")
        keys |= set(_GUI_CALL_SITE.findall(text))
        keys |= set(_GUI_TRANSLATE.findall(text))
    return keys


def main() -> int:
    for label, catalog, call_sites in (
        ("TUI", _tui_catalog_keys(), _tui_call_site_keys()),
        ("GUI", _gui_catalog_keys(), _gui_call_site_keys()),
    ):
        # .one/.other pairs: a tn() call site names the base, the catalog the pairs.
        bases = {key.rsplit(".", 1)[0] for key in call_sites if key.endswith(".one")}
        covered = call_sites | bases
        orphans = sorted(key for key in catalog if key not in covered)
        print(f"{label}: {len(catalog)} catalog keys, "
              f"{len(covered)} referenced by call sites, {len(orphans)} orphans")
        for key in orphans[:10]:
            print(f"  {key}")
        if len(orphans) > 10:
            print(f"  … and {len(orphans) - 10} more")
    print("report only — orphans may be load-bearing (dynamic/test keys); "
          "verify before deleting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
