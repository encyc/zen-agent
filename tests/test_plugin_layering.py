"""The plugins package is a base-cluster extension surface: it may not import
the MCP client, the app layer or the CLI harness."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "zenagent"
FORBIDDEN = (
    "zenagent.mcp",
    "zenagent.app",
    "zenagent.cli",
    "zenagent.agent",
    "zenagent.tools",
    "zenagent.providers",
)


def imports_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
    return found


def test_plugins_package_imports_only_base_modules():
    violations = []
    for path in (ROOT / "plugins").rglob("*.py"):
        for module in imports_of(path):
            if module.startswith(FORBIDDEN):
                violations.append(f"{path.name}: {module}")
    assert violations == []
