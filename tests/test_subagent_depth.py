"""Delegation depth in task transcripts: nesting survives a resume.

A task spawned by the main runtime is depth 1; a task spawned from inside a
task body is depth 2; and so on (main = 0, never stamped). The depth lives in
the transcript's ``meta`` line — a runtime-only depth would reset a resumed
child to top-level.
"""

import json
import time
from pathlib import Path

from zenagent.agent.subagents import SubagentStore, task_dir
from zenagent.agent.subagents_runtime import (
    _delegation_depth,
    run_child,
)
from zenagent.types import AssistantMessage, TextContent, Usage


def session_file(tmp_path) -> Path:
    return tmp_path / "sessions" / "20260101-120000.jsonl"


def begin_and_read(tmp_path, **kwargs) -> dict:
    path = session_file(tmp_path)
    store = SubagentStore(path)
    store.begin("c1", "research", "deepseek-flash", **kwargs)
    return json.loads((task_dir(path) / "c1.jsonl").read_text().splitlines()[0])


def test_begin_stamps_depth_into_meta(tmp_path):
    meta = begin_and_read(tmp_path, delegation_depth=2)
    assert meta["delegationDepth"] == 2

    plain = begin_and_read(tmp_path)
    assert "delegationDepth" not in plain


def test_facts_read_back_depth(tmp_path):
    path = session_file(tmp_path)
    store = SubagentStore(path)
    store.begin("c1", "research", "deepseek-flash", delegation_depth=2)
    store.append(
        "c1",
        [
            AssistantMessage(
                content=[TextContent(text="done")],
                provider="p",
                model="m",
                usage=Usage(),
                stop_reason="stop",
                timestamp=2,
            )
        ],
    )
    store.finish("c1", status="ok", duration_ms=10, cost=0.0, tokens=0)

    meta = store.list()[0]
    assert meta.delegation_depth == 2

    # A legacy transcript without the key reads as top-level.
    path2 = session_file(tmp_path)
    store2 = SubagentStore(path2)
    store2.begin("c2", "legacy", "m")
    store2.finish("c2", status="ok", duration_ms=1, cost=0.0, tokens=0)
    assert store2.list()[0].delegation_depth == 0


class FakeModel:
    id = "test-model"


class FakeChild:
    """Just what run_child touches: subscribe/abort/prompt/messages/state."""

    def __init__(self, on_prompt=None, messages=None):
        self.model = FakeModel()
        self.messages = list(messages or [])
        self.error_message = ""
        self.termination = "stop"
        self._on_prompt = on_prompt

    def subscribe(self, fn) -> None:
        pass

    def abort(self) -> None:
        pass

    async def prompt(self, body) -> None:
        if self._on_prompt is not None:
            await self._on_prompt(self)


def make_store(tmp_path) -> SubagentStore:
    return SubagentStore(session_file(tmp_path))


async def dispatch(tmp_path, call_id, on_prompt=None):
    return await run_child(
        child=FakeChild(on_prompt=on_prompt),
        prompt_body="do it",
        name="research",
        call_id=call_id,
        store=make_store(tmp_path),
        started=time.time(),
        timeout_s=0,
        on_update=None,
    )


async def test_run_child_stamps_and_scopes_depth(tmp_path):
    seen: list[int] = []

    async def on_prompt(child) -> None:
        seen.append(_delegation_depth.get())

    _, details = await dispatch(tmp_path, "c1", on_prompt)

    assert details["status"] == "ok"
    assert seen == [1], "the child body must see depth 1"
    assert _delegation_depth.get() == 0, "the set must not leak to the caller"
    lines = (task_dir(session_file(tmp_path)) / "c1.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["delegationDepth"] == 1


async def test_nested_spawn_compounds_depth(tmp_path):
    async def outer_prompt(child) -> None:
        await dispatch(tmp_path, "c-inner", on_prompt=None)

    _, details = await dispatch(tmp_path, "c-outer", outer_prompt)

    assert details["status"] == "ok"
    lines = (task_dir(session_file(tmp_path)) / "c-outer.jsonl").read_text().splitlines()
    inner = (task_dir(session_file(tmp_path)) / "c-inner.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["delegationDepth"] == 1
    assert json.loads(inner[0])["delegationDepth"] == 2
    assert _delegation_depth.get() == 0
