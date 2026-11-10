"""Layering gates: imports point downward and ``app/`` stays UI-free."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "zenagent"

#: Lower rank may not import a higher one.
LAYERS = {"cli": 0, "app": 1, "tools": 2, "agent": 3, "providers": 4}

#: What ``zenagent/app/__init__.py`` promises: no UI library, no CLI package.
FORBIDDEN_IN_APP = ("argparse", "rich", "textual", "prompt_toolkit")
FORBIDDEN_PREFIX_IN_APP = "zenagent.cli"


def _imports(path: Path) -> list[str]:
    """Every module name imported anywhere in ``path`` (incl. function bodies)."""
    tree = ast.parse(path.read_text())
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(node.module)
            if node.module == "zenagent":
                found.extend(f"zenagent.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
    return found


def _package(module: str) -> str | None:
    parts = module.split(".")
    if parts[0] != "zenagent" or len(parts) < 2:
        return None
    return parts[1] if parts[1] in LAYERS else None


def test_layers_import_downward_only():
    violations = []
    for path in ROOT.rglob("*.py"):
        parts = path.relative_to(ROOT).parts
        if len(parts) < 2 or parts[0] not in LAYERS:
            continue
        source = parts[0]
        for module in _imports(path):
            target = _package(module)
            if target is not None and LAYERS[target] < LAYERS[source]:
                violations.append(f"{path.relative_to(ROOT)}: {module}")
    assert violations == []


def test_app_is_ui_and_cli_free():
    violations = []
    for path in (ROOT / "app").rglob("*.py"):
        for module in _imports(path):
            root = module.split(".")[0]
            if root in FORBIDDEN_IN_APP or module.startswith(FORBIDDEN_PREFIX_IN_APP):
                violations.append(f"{path.relative_to(ROOT)}: {module}")
    assert violations == []


def test_only_the_cli_package_imports_the_cli_package():
    """Everything below ``cli/`` must stay free of the harness."""
    violations = []
    for path in ROOT.rglob("*.py"):
        parts = path.relative_to(ROOT).parts
        if parts[0] == "cli":
            continue
        if parts == ("__main__.py",):
            continue  # the package entry shim is a face, not a kernel module
        for module in _imports(path):
            if module == "zenagent.cli" or module.startswith("zenagent.cli."):
                violations.append(f"{path.relative_to(ROOT)}: {module}")
    assert violations == []


def test_stream_options_lives_in_neutral_module():
    from zenagent.providers import openai_completions, stream_options

    assert openai_completions.StreamOptions is stream_options.StreamOptions


def test_loop_does_not_import_concrete_adapter():
    import inspect

    from zenagent.agent import loop

    src = inspect.getsource(loop)
    assert "openai_completions" not in src


def test_tool_protocol_lives_outside_loop():
    import inspect

    import zenagent.agent.protocol as proto
    from zenagent.agent import loop

    assert "class AgentTool" in inspect.getsource(proto)
    assert "class AgentTool" not in inspect.getsource(loop)


def test_loop_imports_tool_exec_at_module_level():
    import ast
    import inspect

    from zenagent.agent import loop

    fns = [
        n
        for n in ast.walk(ast.parse(inspect.getsource(loop)))
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "agent_loop"
    ]
    body_src = ast.unparse(fns[0])
    assert "from zenagent.agent.tool_exec" not in body_src


def test_root_package_exports_the_kernel_facade():
    """A newcomer's first import should be ``from zenagent import Agent``."""
    from zenagent import (
        Agent,
        AgentConfig,
        AgentState,
        AgentTool,
        StreamOptions,
        agent_loop,
        register_stream_fn,
    )

    assert callable(agent_loop)
    assert callable(register_stream_fn)
    assert Agent is not None and AgentConfig is not None
    assert AgentState is not None and AgentTool is not None
    assert StreamOptions is not None


