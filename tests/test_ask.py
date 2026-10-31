"""The ``ask`` tool: schema validation, frontend handler, headless degrade.

The tool is driven directly (``ASK.execute(tool_call_id, args, abort)``) like the
other built-in tools. The handler registry is process-global by design (one
frontend owns the interactive surface), so every test clears it around itself.
"""

import pytest

from zenagent.tools import ALL_TOOLS
from zenagent.tools.ask import ASK, get_ask_handler, set_ask_handler


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


async def call(args):
    return await ASK.execute("call_ask", args, None)


@pytest.fixture(autouse=True)
def clean_handler():
    set_ask_handler(None)
    yield
    set_ask_handler(None)


# ---------------------------------------------------------------------------
# declaration
# ---------------------------------------------------------------------------


def test_ask_tool_declaration():
    assert ASK.name == "ask"
    assert ASK.approval == "read"
    assert ASK.execution_mode == "parallel", "the spec keeps the default mode"
    assert ASK.parameters["required"] == ["questions"]
    props = ASK.parameters["properties"]
    assert props["questions"]["type"] == "array"
    assert "1-5" in props["questions"]["description"]
    assert "2-6" in props["questions"]["description"]


def test_ask_is_listed_in_all_tools_right_after_wait():
    names = [tool.name for tool in ALL_TOOLS]
    assert "ask" in names
    assert names.index("ask") == names.index("wait") + 1


def test_set_ask_handler_round_trips():
    async def handler(questions):
        return []

    set_ask_handler(handler)
    assert get_ask_handler() is handler
    set_ask_handler(None)
    assert get_ask_handler() is None


# ---------------------------------------------------------------------------
# headless / no frontend
# ---------------------------------------------------------------------------


async def test_without_a_handler_degrades_without_error():
    result = await call({"questions": [question()]})

    assert not result.is_error
    text = result.content[0].text.lower()
    assert "no interactive ui" in text
    assert "conservative" in text
    assert result.details == {"available": False}


async def test_the_handler_is_not_called_for_invalid_questions():
    calls: list = []

    async def handler(questions):
        calls.append(questions)
        return []

    set_ask_handler(handler)
    result = await call({"questions": []})

    assert result.is_error
    assert calls == [], "validation must fail before the UI is involved"


# ---------------------------------------------------------------------------
# handler path
# ---------------------------------------------------------------------------


async def test_handler_answers_are_formatted_for_the_model():
    asked: list[list[dict]] = []

    async def handler(questions):
        asked.append(questions)
        return [{"id": "q1", "selected": ["Postgres"], "custom": "prod parity"}]

    set_ask_handler(handler)
    result = await call({"questions": [question()]})

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


async def test_a_multi_select_answer_keeps_every_label():
    async def handler(questions):
        return [{"id": "q1", "selected": ["SQLite", "Postgres"]}]

    set_ask_handler(handler)
    result = await call({"questions": [question(multi=True)]})

    assert not result.is_error
    assert "SQLite, Postgres" in result.content[0].text
    assert result.details["answers"] == [
        {"id": "q1", "selected": ["SQLite", "Postgres"]}
    ]


async def test_a_custom_answer_only_is_reported():
    async def handler(questions):
        return [{"id": "q1", "selected": [], "custom": "use DuckDB"}]

    set_ask_handler(handler)
    result = await call({"questions": [question()]})

    assert "(no selection)" in result.content[0].text
    assert "custom: use DuckDB" in result.content[0].text


async def test_an_empty_answer_list_means_the_user_dismissed():
    async def handler(questions):
        return []

    set_ask_handler(handler)
    result = await call({"questions": [question()]})

    assert not result.is_error
    assert "dismissed" in result.content[0].text.lower()
    assert "conservative" in result.content[0].text.lower()
    assert result.details["available"] is True
    assert result.details["answers"] == []


async def test_partial_answers_mark_the_rest_unanswered():
    async def handler(questions):
        return [{"id": "q1", "selected": ["SQLite"]}]

    set_ask_handler(handler)
    result = await call(
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


async def test_a_handler_exception_becomes_an_error_result():
    async def handler(questions):
        raise RuntimeError("dialog exploded")

    set_ask_handler(handler)
    result = await call({"questions": [question()]})

    assert result.is_error
    assert "dialog exploded" in result.content[0].text


# ---------------------------------------------------------------------------
# question normalization
# ---------------------------------------------------------------------------


async def test_the_handler_receives_normalized_questions():
    captured: list[list[dict]] = []

    async def handler(questions):
        captured.append(questions)
        return [{"id": "q1", "selected": ["A"]}]

    set_ask_handler(handler)
    await call(
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


async def test_multi_defaults_to_false_and_optional_fields_are_dropped():
    captured: list[list[dict]] = []

    async def handler(questions):
        captured.append(questions)
        return [{"id": "q1", "selected": ["A"]}]

    set_ask_handler(handler)
    await call(
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
async def test_invalid_arguments_are_error_results(questions, needle):
    result = await call({"questions": questions})

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

    set_ask_handler(handler)
    runtime = await runtime_factory(stream_fn=stream)
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
