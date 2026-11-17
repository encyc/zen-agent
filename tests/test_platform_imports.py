"""Portability guards for code that only ever runs on the developer's OS.

The Windows installers are frozen from the same package; a module-level import
of a POSIX-only stdlib module is invisible on macOS/Linux and only shows up as
``ModuleNotFoundError`` inside the PyInstaller build (v0.13.8 died there).
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "zenagent"

#: Stdlib modules that do not exist on Windows (``msvcrt``-only counterparts).
POSIX_ONLY = frozenset(
    {"crypt", "fcntl", "grp", "pty", "pwd", "resource", "syslog", "termios"}
)

#: Importing under one of these is a guard: a platform check, a fallback, or a
#: helper that only runs when something else already decided the platform.
GUARDING_NODES = (ast.If, ast.Try, ast.FunctionDef, ast.AsyncFunctionDef)


def _imported_module(node: ast.stmt) -> str | None:
    if isinstance(node, ast.Import):
        names = [alias.name.split(".")[0] for alias in node.names]
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        names = [node.module.split(".")[0]]
    else:
        return None
    hits = POSIX_ONLY.intersection(names)
    return min(hits) if hits else None


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }


def test_posix_only_modules_are_imported_behind_a_guard():
    offenders: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = _parents(tree)
        for node in ast.walk(tree):
            module = _imported_module(node)
            if module is None:
                continue
            ancestor = parents.get(node)
            while ancestor is not None:
                if isinstance(ancestor, GUARDING_NODES):
                    break
                ancestor = parents.get(ancestor)
            else:
                offenders.append(
                    f"{path.relative_to(PACKAGE_ROOT.parent)}:{node.lineno} "
                    f"imports {module} at module level"
                )
    assert not offenders, (
        "POSIX-only modules must be imported inside a platform guard so the "
        "Windows sidecar can still be frozen: " + "; ".join(offenders)
    )
