"""Examples stay importable and runnable offline."""

import importlib.util
import json
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NAMES = ["headless", "custom_tool", "mcp", "rpc_client", "embed"]


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", NAMES)
def test_examples_import_without_side_effects(name):
    module = load(name)
    assert callable(module.main)


async def test_custom_tool_example_runs_the_loop_offline():
    """The fake stream must make the loop *invoke* the tool, not just answer."""
    module = load("custom_tool")
    assert await module.main() == 0


async def test_mcp_example_calls_the_echo_server_offline():
    module = load("mcp")
    assert await module.main() == 0


async def test_headless_example_delegates_to_run_headless(monkeypatch):
    """No network in CI: check the wiring, not the provider."""
    module = load("headless")
    calls: list[tuple[str, dict]] = []

    async def fake_run_headless(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return 0

    monkeypatch.setattr(module, "run_headless", fake_run_headless)
    assert await module.main("say hi", json_output=True) == 0
    assert calls == [("say hi", {"json_output": True})]


def test_rpc_client_streams_deltas_until_the_result():
    module = load("rpc_client")
    deltas: list[str] = []
    frames = [
        json.dumps({"type": "ignored", "id": module.PROMPT_ID}),
        json.dumps(
            {
                "type": "event",
                "event": {
                    "type": "message_update",
                    "assistant_event": {"type": "text_delta", "delta": "hi"},
                },
            }
        ),
        json.dumps({"type": "result", "id": module.PROMPT_ID, "ok": True, "text": "hi"}),
    ]

    result, error = module.collect(frames, module.PROMPT_ID, on_text=deltas.append)

    assert error is None
    assert result["text"] == "hi"
    assert deltas == ["hi"]


def test_rpc_client_surfaces_errors_instead_of_hanging():
    module = load("rpc_client")
    error_frame = json.dumps({"type": "error", "id": module.PROMPT_ID, "message": "boom"})

    result, error = module.collect([error_frame], module.PROMPT_ID)

    assert result is None
    assert error["message"] == "boom"
    # A server that dies without answering must settle as "no result", not hang.
    assert module.collect([], module.PROMPT_ID) == (None, None)
