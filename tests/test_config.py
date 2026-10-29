"""config.toml parsing/overrides and the context-window prune."""

import time

import pytest

from pyagent.config import (
    ContextGuard,
    UserConfig,
    apply_user_config,
    load_user_config,
    naive_overflow_prune,
    token_estimate,
)
from pyagent.events import AgentEvent
from pyagent.providers.catalog import find_model, load_catalog
from pyagent.types import (
    AssistantMessage,
    SystemMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def write_config(tmp_path, text: str):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def assistant(text: str = "ok", tools: list[ToolCall] | None = None) -> AssistantMessage:
    content = [TextContent(text=text)] if text else []
    content.extend(tools or [])
    return AssistantMessage(
        content=content,
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="tool_use" if tools else "stop",
        timestamp=int(time.time()),
    )


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_missing_file_gives_defaults(tmp_path):
    config = load_user_config(tmp_path / "absent.toml")
    assert config == UserConfig()


def test_parses_default_model_and_provider_overrides(tmp_path):
    path = write_config(
        tmp_path,
        'default_model = "deepseek-flash"\n\n[providers.deepseek]\n'
        'base_url = "https://mirror.example/v1"\napi_key_env = "MY_KEY"\n',
    )
    config = load_user_config(path)
    assert config.default_model == "deepseek-flash"
    assert config.provider_overrides["deepseek"] == {
        "base_url": "https://mirror.example/v1",
        "api_key_env": "MY_KEY",
    }


def test_unknown_provider_key_is_rejected(tmp_path):
    path = write_config(tmp_path, '[providers.deepseek]\nmodel = "x"\n')
    with pytest.raises(ValueError, match="unknown keys"):
        load_user_config(path)


def test_broken_toml_raises_a_clear_error(tmp_path):
    path = write_config(tmp_path, "default_model = [unclosed\n")
    with pytest.raises(ValueError, match="cannot read config"):
        load_user_config(path)


def test_apply_overrides_rewrites_models_and_key_env():
    catalog = load_catalog()
    before = find_model(catalog, "deepseek-flash")
    assert before.base_url == "https://api.deepseek.com"

    apply_user_config(
        catalog,
        UserConfig(
            provider_overrides={
                "deepseek": {"base_url": "https://mirror.example/v1", "api_key_env": "K"}
            }
        ),
    )
    model = find_model(catalog, "deepseek-flash")
    assert model.base_url == "https://mirror.example/v1"
    assert catalog["deepseek"].api_key_env == "K"


def test_unknown_provider_override_is_ignored():
    catalog = load_catalog()
    apply_user_config(catalog, UserConfig(provider_overrides={"ghost": {"base_url": "x"}}))
    assert find_model(catalog, "deepseek-flash").base_url == "https://api.deepseek.com"


# ---------------------------------------------------------------------------
# pruning
# ---------------------------------------------------------------------------


def test_token_estimate_prefers_real_usage():
    messages = [UserMessage(content="x" * 4000, timestamp=1)]
    assert token_estimate(messages) > 100
    assert token_estimate(messages, Usage(input=42, cache_read=8)) == 50


def turn(n: int) -> list:
    return [
        UserMessage(content=f"question {n} " * 50, timestamp=n),
        assistant(f"answer {n} " * 50),
    ]


async def test_small_context_is_left_alone():
    messages = [UserMessage(content="hi", timestamp=1), assistant()]
    assert await naive_overflow_prune(messages, 100_000) == messages


async def test_overflow_drops_oldest_turns_but_keeps_system_and_last():
    # the loop passes the system message first, which must survive pruning
    messages = [SystemMessage(content="sys", timestamp=0)]
    for n in range(10):
        messages.extend(turn(n))
    pruned = await naive_overflow_prune(messages, context_window=800)

    assert len(pruned) < len(messages)
    assert pruned[0] is messages[0]  # leading message survives
    assert pruned[-2:] == messages[-2:]  # newest turn survives
    # turns stay whole: the transcript still alternates user/assistant
    assert [type(m).__name__ for m in pruned[1:3]] == ["UserMessage", "AssistantMessage"]


async def test_prune_never_leaves_a_tool_call_without_its_result():
    messages: list = []
    for n in range(6):
        call = ToolCall(id=f"c{n}", name="bash", arguments={})
        messages.append(UserMessage(content="run it " * 200, timestamp=n))
        messages.append(assistant(tools=[call]))
        messages.append(
            ToolResultMessage(
                tool_call_id=f"c{n}",
                tool_name="bash",
                content=[TextContent(text="output " * 200)],
                is_error=False,
                timestamp=n,
            )
        )
    pruned = await naive_overflow_prune(messages, context_window=600)

    call_ids = {
        block.id
        for message in pruned
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, ToolCall)
    }
    result_ids = {
        message.tool_call_id
        for message in pruned
        if isinstance(message, ToolResultMessage)
    }
    assert call_ids == result_ids  # paired, no dangling calls


async def test_zero_window_is_a_no_op():
    messages = [UserMessage(content="hi", timestamp=1)]
    assert await naive_overflow_prune(messages, 0) == messages


async def test_context_guard_tracks_usage_and_prunes():
    guard = ContextGuard(context_window=10)
    assert guard.last_usage is None
    guard.on_event(AgentEvent(type="message_end", message=assistant()))
    assert guard.last_usage is not None
    messages = [UserMessage(content="x" * 2000, timestamp=1), assistant()]
    assert len(await guard.transform(messages)) <= len(messages)
