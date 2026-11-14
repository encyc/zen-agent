"""The limits knobs live beside Runtime, not inside it (audit finding 9)."""

from __future__ import annotations

import inspect

from zenagent.app import limits_apply
from zenagent.app.runtime import Runtime


def test_module_functions_exist():
    assert callable(limits_apply.overrides)
    assert callable(limits_apply.apply)


def test_runtime_methods_delegate():
    assert "limits_apply." in inspect.getsource(Runtime.limit_overrides)
    assert "limits_apply." in inspect.getsource(Runtime.apply_limits)


async def test_overrides_maps_the_cli_switches(runtime_factory):
    runtime = await runtime_factory(max_turns=20, skills=False, stream_fn=None)
    active = limits_apply.overrides(runtime)
    assert active["max_turns"] == "--max-turns 20"
    assert active["max_index_bytes"] == "--no-skills"
    assert active["max_body_bytes"] == "--no-skills"
    await runtime.close()


async def test_apply_pushes_loop_guard_threshold(runtime_factory):
    runtime = await runtime_factory(stream_fn=None)
    runtime.agent.loop_guard_threshold = -1
    limits_apply.apply(runtime)
    assert runtime.agent.loop_guard_threshold == runtime.limits.loop_guard_threshold
    await runtime.close()
