"""The ``ask`` tool: schema validation, frontend handler, headless degrade.

The tool is driven directly (``tool.execute(tool_call_id, args, abort)``) like
the other built-in tools. The handler lives on the owning Runtime's slot
(``runtime.ask_handler``) and is resolved at call time, so two agents in one
process cannot race for a module-level registry.
"""

import pytest

from zenagent.tools import base_tools
from zenagent.tools.ask import build_ask_tool


def question(**overrides):
    base = {
        "id": "q1",
        "question": "Which database should the service use?",
        "header": "Storage",
        "options": [
            {"label": "SQLite", "description": "zero-config, local"},
            {"label": "Postgres", "description": "needs a server"},
        ],
        "recommended": "SQLite",
    }
    base.update(overrides)
    return base


class AskHarness:
    """A per-test ask tool over a mutable slot (mirrors Runtime.ask_handler)."""

    def __init__(self) -> None:
        self.handler = None
        self.tool = build_ask_tool(lambda: self.handler)

    async def call(self, args):
        return await self.tool.execute("call_ask", args, None)


@pytest.fixture()
def ask():
    return AskHarness()


# ---------------------------------------------------------------------------
# declaration
# ---------------------------------------------------------------------------


def test_ask_tool_declaration(ask):
    assert ask.tool.name == "ask"
    assert ask.tool.approval == "read"
    assert ask.tool.execution_mode == "parallel", "the spec keeps the default mode"
    assert ask.tool.parameters["required"] == ["questions"]
    props = ask.tool.parameters["properties"]
    assert props["questions"]["type"] == "array"
    assert "1-5" in props["questions"]["description"]
    assert "2-6" in props["questions"]["description"]


def test_ask_is_listed_in_prompt_order_right_after_wait(ask):
    names = [tool.name for tool in base_tools(ask.tool)]
    assert "ask" in names
    assert names.index("ask") == names.index("wait") + 1


async def test_the_handler_slot_is_resolved_at_call_time(ask):
    async def handler(questions):
        return []

    ask.handler = handler
    answered = await ask.call({"questions": [question()]})
    assert not answered.is_error

    ask.handler = None
    degraded = await ask.call({"questions": [question()]})
    assert "no interactive ui" in degraded.content[0].text.lower()


# ---------------------------------------------------------------------------
# headless / no frontend
# ---------------------------------------------------------------------------


async def test_without_a_handler_degrades_without_error(ask):
    result = await ask.call({"questions": [question()]})

    assert not result.is_error
    text = result.content[0].text.lower()
    assert "no interactive ui" in text
    assert "conservative" in text
    assert result.details == {"available": False}


async def test_the_handler_is_not_called_for_invalid_questions(ask):
    calls: list = []

    async def handler(questions):
        calls.append(questions)
        return []

    ask.handler = handler
    result = await ask.call({"questions": []})

    assert result.is_error
    assert calls == [], "validation must fail before the UI is involved"


# ---------------------------------------------------------------------------
# handler path
# ---------------------------------------------------------------------------


async def test_handler_answers_are_formatted_for_the_model(ask):
    asked: list[list[dict]] = []

    async def handler(questions):
        asked.append(questions)
        return [{"id": "q1", "selected": ["Postgres"], "custom": "prod parity"}]

    ask.handler = handler
    result = await ask.call({"questions": [question()]})

    assert not result.is_error
    assert asked and asked[0][0]["id"] == "q1"
    text = result.content[0].text
    assert text.startswith("User answers:")
    assert "Storage" in text and "Postgres" in text
    assert "custom: prod parity" in text
    assert result.details["available"] is True
    assert result.details["answers"] == [
        {"id": "q1", "selected": ["Postgres"], "custom": "prod parity"}
    ]


async def test_a_multi_select_answer_keeps_every_label(ask):
    async def handler(questions):
        return [{"id": "q1", "selected": ["SQLite", "Postgres"]}]

    ask.handler = handler
    result = await ask.call({"questions": [question(multi=True)]})

    assert not result.is_error
    assert "SQLite, Postgres" in result.content[0].text
    assert result.details["answers"] == [
        {"id": "q1", "selected": ["SQLite", "Postgres"]}
    ]


async def test_a_custom_answer_only_is_reported(ask):
    async def handler(questions):
        return [{"id": "q1", "selected": [], "custom": "use DuckDB"}]

    ask.handler = handler
    result = await ask.call({"questions": [question()]})

    assert "(no selection)" in result.content[0].text
    assert "custom: use DuckDB" in result.content[0].text


async def test_an_empty_answer_list_means_the_user_dismissed(ask):
    async def handler(questions):
        return []

    ask.handler = handler
    result = await ask.call({"questions": [question()]})

    assert not result.is_error
    assert "dismissed" in result.content[0].text.lower()
    assert "conservative" in result.content[0].text.lower()
    assert result.details["available"] is True
    assert result.details["answers"] == []


async def test_partial_answers_mark_the_rest_unanswered(ask):
    async def handler(questions):
        return [{"id": "q1", "selected": ["SQLite"]}]

    ask.handler = handler
    result = await ask.call(
        {
            "questions": [
                question(),
                question(
                    id="q2",
                    header="Cache",
                    question="Which cache?",
                    recommended=None,
                ),
            ]
        }
    )

    text = result.content[0].text
    assert "SQLite" in text
    assert "(no answer)" in text


async def test_a_handler_exception_becomes_an_error_result(ask):
    async def handler(questions):
        raise RuntimeError("dialog exploded")

    ask.handler = handler
    result = await ask.call({"questions": [question()]})

    assert result.is_error
    assert "dialog exploded" in result.content[0].text


# ---------------------------------------------------------------------------
# question normalization
# ---------------------------------------------------------------------------


async def test_the_handler_receives_normalized_questions(ask):
    captured: list[list[dict]] = []

    async def handler(questions):
        captured.append(questions)
        return [{"id": "q1", "selected": ["A"]}]

    ask.handler = handler
    await ask.call(
        {
            "questions": [
                {
                    "id": " q1 ",
                    "question": "  pick one  ",
                    "options": [
                        {"label": " A "},
                        {"label": "B", "description": " desc  here "},
                    ],
                    "multi": True,
                }
            ]
        }
    )

    assert captured == [
        [
            {
                "id": "q1",
                "question": "pick one",
                "options": [
                    {"label": "A"},
                    {"label": "B", "description": "desc here"},
                ],
                "multi": True,
            }
        ]
    ]


async def test_multi_defaults_to_false_and_optional_fields_are_dropped(ask):
    captured: list[list[dict]] = []

    async def handler(questions):
        captured.append(questions)
        return [{"id": "q1", "selected": ["A"]}]

    ask.handler = handler
    await ask.call(
        {
            "questions": [
                {
                    "id": "q1",
                    "question": "pick one",
                    "options": [{"label": "A"}, {"label": "B"}],
                }
            ]
        }
    )

    assert captured[0][0]["multi"] is False
    assert "header" not in captured[0][0]
    assert "recommended" not in captured[0][0]


# ---------------------------------------------------------------------------
# schema validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("questions", "needle"),
    [
        ("not-a-list", "array"),
        ([], "at least 1"),
        ([question()] * 6, "at most 5"),
        ([question(options=[{"label": "only one"}])], "2-6 options"),
        ([question(options=[{"label": f"o{i}"} for i in range(7)])], "2-6 options"),
        ([question(options=[{"label": "A"}, {"description": "no label"}])], "label"),
        ([question(), question()], "duplicate"),
        ([question(recommended="MySQL")], "recommended"),
        ([question(question="   ")], "question"),
        ([question(multi="yes")], "multi"),
        ([{"id": "q1", "options": [{"label": "A"}, {"label": "B"}]}], "question"),
    ],
)
async def test_invalid_arguments_are_error_results(ask, questions, needle):
    result = await ask.call({"questions": questions})

    assert result.is_error
    text = result.content[0].text.lower()
    assert "invalid" in text
    assert needle.lower() in text


# ---------------------------------------------------------------------------
# loop integration
# ---------------------------------------------------------------------------


async def test_ask_runs_through_the_agent_loop(runtime_factory):
    """A model-issued ask call blocks on the handler and feeds the answer back."""
    from zenagent.app.chat import ChatController
    from zenagent.events import AssistantEvent
    from zenagent.types import (
        AssistantMessage,
        TextContent,
        ToolCall,
        ToolResultMessage,
        Usage,
    )

    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        if isinstance(messages[-1], ToolResultMessage):
            partial.content = [TextContent(text="done")]
            partial.stop_reason = "stop"
            yield AssistantEvent(
                type="text_delta", partial=partial, content_index=0, delta="done"
            )
            yield AssistantEvent(type="done", partial=partial, reason="stop")
            return
        call = ToolCall(id="c1", name="ask", arguments={"questions": [question()]})
        partial.content = [call]
        yield AssistantEvent(
            type="toolcall_start", partial=partial, content_index=0, tool_call=call
        )
        yield AssistantEvent(
            type="toolcall_end", partial=partial, content_index=0, tool_call=call
        )
        partial.stop_reason = "tool_use"
        yield AssistantEvent(type="done", partial=partial, reason="tool_use")

    async def handler(questions):
        return [{"id": "q1", "selected": ["Postgres"]}]

    runtime = await runtime_factory(stream_fn=stream)
    runtime.ask_handler = handler
    chat = ChatController(runtime)

    await chat.submit("go")
    await chat.wait_for_idle()

    results = [
        message
        for message in runtime.agent.messages
        if isinstance(message, ToolResultMessage) and message.tool_name == "ask"
    ]
    assert results, "the ask tool must have run"
    assert not results[0].is_error
    text = "\n".join(block.text for block in results[0].content)
    assert "User answers:" in text and "Postgres" in text


async def test_answers_are_validated_against_the_offered_options(ask):
    async def handler(_questions):
        return [
            {
                "id": "q1",
                "selected": ["SQLite", "not-an-option"],
                "custom": "note",
            }
        ]

    ask.handler = handler
    result = await ask.call({"questions": [question()]})
    assert "SQLite" in result.content[0].text
    assert "not-an-option" not in result.content[0].text
    assert result.details["answers"] == [
        {"id": "q1", "selected": ["SQLite"], "custom": "note"}
    ]


async def test_single_select_answers_keep_only_the_first_label(ask):
    async def handler(_questions):
        return [{"id": "q1", "selected": ["SQLite", "Postgres"]}]

    ask.handler = handler
    result = await ask.call({"questions": [question()]})
    assert result.details["answers"] == [{"id": "q1", "selected": ["SQLite"]}]


async def test_ask_returns_promptly_when_the_run_is_aborted(ask):
    import asyncio

    from zenagent.events import AbortToken

    blocker = asyncio.Event()

    async def handler(_questions):
        await blocker.wait()
        return []

    ask.handler = handler
    abort = AbortToken()

    async def trigger():
        await asyncio.sleep(0.05)
        abort.abort()

    task = asyncio.create_task(trigger())
    result = await asyncio.wait_for(
        ask.tool.execute("c1", {"questions": [question()]}, abort), timeout=5
    )
    await task
    blocker.set()
    assert not result.is_error
    assert result.details.get("aborted") is True
    assert "conservative" in result.content[0].text


async def test_two_agents_can_use_different_ask_handlers():
    tool_one = build_ask_tool(lambda: _select("one"))
    tool_two = build_ask_tool(lambda: _select("two"))
    args = {
        "questions": [
            {"id": "q", "question": "?", "options": [{"label": "one"}, {"label": "two"}]}
        ]
    }
    one = await tool_one.execute("c1", args, None)
    two = await tool_two.execute("c2", args, None)
    assert "one" in one.content[0].text
    assert "two" in two.content[0].text


def _select(label):
    async def handler(questions):
        return [{"id": questions[0]["id"], "selected": [label]}]

    return handler


async def test_ask_without_handler_errors_not_hangs():
    tool = build_ask_tool(lambda: None)
    args = {
        "questions": [
            {"id": "q", "question": "?", "options": [{"label": "a"}, {"label": "b"}]}
        ]
    }
    result = await tool.execute("c3", args, None)
    assert not result.is_error  # graceful no-UI degrade, never a hang
    assert "no interactive ui" in result.content[0].text.lower()
