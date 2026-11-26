"""Auto session title: normalization and the background titler."""

from __future__ import annotations

import pytest

from zenagent.app.title import AUTO_TITLE_LIMIT, clean_title


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("登录重试修复", "登录重试修复"),
        ("「登录重试修复」", "登录重试修复"),
        ("`登录重试修复`", "登录重试修复"),
        ("**登录重试修复**", "登录重试修复"),
        ("会话名：登录重试修复", "登录重试修复"),
        ("Title: login retry fix", "login retry fix"),
        ("```\n登录重试修复\n```", "登录重试修复"),
        ("登录重试修复。", "登录重试修复"),
        ("登录重试修复\n\n说明：这是会话名", "登录重试修复"),
        ("  登录   重试  ", "登录 重试"),
        ("", ""),
        ("   \n  ", ""),
        ("长" * (AUTO_TITLE_LIMIT + 20), "长" * AUTO_TITLE_LIMIT),
    ],
)
def test_clean_title(raw: str, expected: str) -> None:
    assert clean_title(raw) == expected


import asyncio

from conftest import text_stream

from zenagent.agent.session import (
    goal_objective,
    read_session_titles,
    session_title,
    set_session_title,
)
from zenagent.app.title import TITLE_MAX_TOKENS, AutoTitler, title_source
from zenagent.types import ImageContent, TextContent, UserMessage


def _recording_stream(text: str, calls: list):
    """A fake provider that records ``(options, messages)`` per call."""
    inner = text_stream(text)

    async def stream(model, messages, tools, options):
        calls.append((options, messages))
        async for event in inner(model, messages, tools, options):
            yield event

    return stream


def test_goal_objective_is_public() -> None:
    wrapped = "<objective>\n修好登录\n</objective>\n\n继续"
    assert goal_objective(wrapped) == "修好登录"
    assert goal_objective("没有包裹") == ""


def test_title_source_reads_text_blocks_and_goals() -> None:
    message = UserMessage(
        content=[TextContent(text="看看这张图"), ImageContent(data="", mime_type="image/png")],
        timestamp=0,
    )
    assert title_source(message) == "看看这张图"
    goal = UserMessage(content="<objective>\n修好登录\n</objective>\n\n继续", timestamp=0, goal=True)
    assert title_source(goal) == "修好登录"


def test_title_source_skips_image_only_messages() -> None:
    message = UserMessage(
        content=[ImageContent(data="", mime_type="image/png")], timestamp=0
    )
    assert title_source(message) == ""


async def test_first_user_message_writes_the_title(runtime_factory) -> None:
    runtime = await runtime_factory(stream_fn=text_stream("登录重试修复"))
    titler = AutoTitler(runtime)
    runtime.agent.subscribe(titler)
    await runtime.agent.prompt("帮我修一下登录重试的 bug")
    await titler.finish()
    assert session_title(runtime.session.path) == "登录重试修复"


async def test_a_stored_title_is_never_overwritten(runtime_factory) -> None:
    calls: list = []
    runtime = await runtime_factory(stream_fn=_recording_stream("模型给的名字", calls))
    set_session_title(runtime.session.path, "手动名字")
    titler = AutoTitler(runtime)
    runtime.agent.subscribe(titler)
    await runtime.agent.prompt("随便说点什么")
    await titler.finish()
    assert session_title(runtime.session.path) == "手动名字"
    assert all(options.max_tokens != TITLE_MAX_TOKENS for options, _ in calls)


async def test_a_rename_during_the_call_wins(runtime_factory) -> None:
    gate = asyncio.Event()
    inner = text_stream("模型给的名字")

    async def stream(model, messages, tools, options):
        if options.max_tokens == TITLE_MAX_TOKENS:
            await gate.wait()
        async for event in inner(model, messages, tools, options):
            yield event

    runtime = await runtime_factory(stream_fn=stream)
    titler = AutoTitler(runtime)
    runtime.agent.subscribe(titler)
    await runtime.agent.prompt("帮我修登录")
    set_session_title(runtime.session.path, "手动名字")
    gate.set()
    await titler.finish()
    assert session_title(runtime.session.path) == "手动名字"


async def test_a_failing_title_call_is_silent(runtime_factory) -> None:
    inner = text_stream("ok")

    async def stream(model, messages, tools, options):
        if options.max_tokens == TITLE_MAX_TOKENS:
            raise RuntimeError("boom")
        async for event in inner(model, messages, tools, options):
            yield event

    runtime = await runtime_factory(stream_fn=stream)
    titler = AutoTitler(runtime)
    runtime.agent.subscribe(titler)
    await runtime.agent.prompt("帮我改一下")  # must not raise
    await titler.finish()
    assert read_session_titles(runtime.session.path.parent) == {}


async def test_only_the_first_message_titles(runtime_factory) -> None:
    calls: list = []
    runtime = await runtime_factory(stream_fn=_recording_stream("名字", calls))
    titler = AutoTitler(runtime)
    runtime.agent.subscribe(titler)
    await runtime.agent.prompt("第一句")
    await runtime.agent.prompt("第二句")
    await titler.finish()
    assert sum(1 for options, _ in calls if options.max_tokens == TITLE_MAX_TOKENS) == 1


async def test_a_resumed_session_is_not_retitled(runtime_factory) -> None:
    first = await runtime_factory(stream_fn=text_stream("ok"))
    await first.agent.prompt("第一句")
    path = first.session.path
    await first.close()  # one writer per session: release the lease before resuming
    calls: list = []
    resumed = await runtime_factory(resume=path, stream_fn=_recording_stream("不该出现", calls))
    titler = AutoTitler(resumed)
    resumed.agent.subscribe(titler)
    await resumed.agent.prompt("第二句")
    await titler.finish()
    assert all(options.max_tokens != TITLE_MAX_TOKENS for options, _ in calls)


async def test_a_goal_turn_titles_from_its_objective(runtime_factory) -> None:
    calls: list = []
    runtime = await runtime_factory(stream_fn=_recording_stream("登录目标", calls))
    titler = AutoTitler(runtime)
    runtime.agent.subscribe(titler)
    await runtime.agent.prompt("<objective>\n修好登录\n</objective>\n\n继续", goal=True)
    await titler.finish()
    title_calls = [messages for options, messages in calls if options.max_tokens == TITLE_MAX_TOKENS]
    assert title_calls
    prompt = title_calls[0][-1].content
    assert "修好登录" in prompt
    assert "<objective>" not in prompt


async def test_finish_gives_up_on_a_hung_call(runtime_factory) -> None:
    inner = text_stream("ok")

    async def stream(model, messages, tools, options):
        if options.max_tokens == TITLE_MAX_TOKENS:
            await asyncio.sleep(30)
        async for event in inner(model, messages, tools, options):
            yield event

    runtime = await runtime_factory(stream_fn=stream)
    titler = AutoTitler(runtime)
    runtime.agent.subscribe(titler)
    await runtime.agent.prompt("帮我改一下")
    await titler.finish(timeout_s=0.05)  # returns; the hung call is cancelled
    assert read_session_titles(runtime.session.path.parent) == {}


from zenagent.app.agent_profile import AgentProfile
from zenagent.app.bootstrap import prepare
from zenagent.app.runtime import RuntimeOptions


def _offline_options(root) -> RuntimeOptions:
    return RuntimeOptions(
        root=root,
        profile=AgentProfile(mcp=False, skills=False, templates=False),
    )


async def test_prepare_attaches_the_titler(app_env, tmp_path) -> None:
    # No injected stream_fn: naming rides the runtime's own provider, and the
    # fake is installed afterwards so the assembly path stays the production one.
    prepared = await prepare(_offline_options(app_env), views=False)
    runtime = prepared.runtime
    runtime.agent.stream_fn = text_stream("登录重试修复")
    titler = runtime.auto_titler
    assert titler is not None
    try:
        await runtime.agent.prompt("帮我修一下登录重试的 bug")
        await titler.finish()
        assert session_title(runtime.session.path) == "登录重试修复"
    finally:
        await runtime.close()


async def test_close_lands_an_inflight_title(app_env, tmp_path) -> None:
    prepared = await prepare(_offline_options(app_env), views=False)
    runtime = prepared.runtime
    runtime.agent.stream_fn = text_stream("登录重试修复")
    await runtime.agent.prompt("帮我修一下")
    await runtime.close()
    assert session_title(runtime.session.path) == "登录重试修复"
    assert runtime.auto_titler is None
