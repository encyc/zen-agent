"""Layering invariants the audit (2026-11-14) found worth pinning as tests.

Import-graph discipline is only as strong as the last refactor that forgot
it; these tests make the forgetting red. Scanning is AST-based so comments
and prose never trip a failure, and deferred (function-level) imports count
as real edges.
"""

import ast
import importlib
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "zenagent" / "app"


def _imported_modules(source: str) -> set[str]:
    """Every module any Import/ImportFrom names, at any depth."""
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _py_sources(directory: Path) -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(directory.glob("*.py"))}


def test_app_pkg_has_no_eager_reexport():
    """zenagent.app/__init__ must not import its own submodules eagerly.

    The eager re-exports made six submodules re-enter the package mid-init —
    the anchor of the 15-node app cycle (audit P0-2).
    """
    source = (APP_DIR / "__init__.py").read_text()
    eager = [m for m in _imported_modules(source) if m.startswith("zenagent.app.")]
    assert eager == []


def test_from_app_import_still_works():
    """The lazy re-exports keep the public surface importable."""
    import zenagent.app as app

    for name in app.__all__:
        obj = getattr(app, name)
        assert obj is not None
    # Submodule imports bypass __getattr__ entirely and must keep working:
    # six modules do `from zenagent.app import plugin_wiring` (audit P0-2).
    for sub in ("plugin_wiring", "records", "subagent_runs", "clipboard"):
        importlib.import_module(f"zenagent.app.{sub}")


def test_runtime_does_not_import_zen():
    """The kernel must not name the zen orchestrator (audit P0-3).

    runtime -> zen.identity/zen.prompts/zen.tools -> zen.fleet -> bootstrap
    -> runtime was the one cross-layer cycle; the router surface now arrives
    via Runtime.tool_providers / prompt_note_providers.
    """
    source = (APP_DIR / "runtime.py").read_text()
    zen_imports = sorted(
        m for m in _imported_modules(source) if m.startswith("zenagent.app.zen")
    )
    assert zen_imports == []


def test_agent_never_imports_app_or_cli():
    for name, src in _py_sources(APP_DIR.parent / "agent").items():
        mods = _imported_modules(src)
        assert not any(m.startswith("zenagent.app") for m in mods), (
            f"{name} imports the app layer"
        )
        assert not any(m.startswith("zenagent.cli") for m in mods), (
            f"{name} imports the cli layer"
        )


def test_plugins_never_import_upward():
    for name, src in _py_sources(APP_DIR.parent / "plugins").items():
        mods = _imported_modules(src)
        bad = [m for m in mods if m.startswith(("zenagent.app", "zenagent.cli", "zenagent.agent"))]
        assert not bad, f"{name} violates the plugins-only-downward invariant: {bad}"
