"""Layering invariants the audit (2026-11-14) found worth pinning as tests.

Import-graph discipline is only as strong as the last refactor that forgot
it; these tests make the forgetting red.
"""

import ast
import importlib
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "zenagent" / "app"


def _top_level_app_imports(source: str) -> list[str]:
    """Module names imported by top-level statements (strings are ignored)."""
    tree = ast.parse(source)
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def test_app_pkg_has_no_eager_reexport():
    """zenagent.app/__init__ must not import its own submodules eagerly.

    The eager re-exports made six submodules re-enter the package mid-init —
    the anchor of the 15-node app cycle (audit P0-2).
    """
    source = (APP_DIR / "__init__.py").read_text()
    eager = [m for m in _top_level_app_imports(source) if m.startswith("zenagent.app.")]
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
