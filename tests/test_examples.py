"""Examples stay importable and runnable offline."""

import importlib.util
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NAMES = ["headless", "custom_tool", "mcp", "rpc_client"]


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", NAMES)
def test_examples_import_without_side_effects(name):
    module = load(name)
    assert hasattr(module, "main")


async def test_custom_tool_example_runs_offline():
    module = load("custom_tool")
    assert await module.main() == 0
