"""The ``ask`` tool: the model asks the user a structured question.

The kernel cannot answer a question for the user, and a headless run has nobody
to ask. A module-level handler registry lets the active frontend volunteer (the
TUI registers ``ZenAgentApp.request_ask`` while mounted; REPL/headless may do
the same later), while every other run degrades to a non-error result that
tells the model to proceed with the most conservative option instead of
blocking forever.

The tool itself is a stateless, read-tier builtin (it changes no files and no
session state); all UI work lives in the frontend handler. Answers come back as
``details["answers"]`` for logs/tests and as a compact ``User answers:`` block
for the model.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.types import TextContent

__all__ = [
    "ASK",
    "MAX_OPTIONS",
    "MAX_QUESTIONS",
    "MIN_OPTIONS",
    "AskHandler",
    "get_ask_handler",
    "set_ask_handler",
]

#: A frontend callback: normalized questions in, answers out.
#:
#: Each answer is ``{"id": str, "selected": [label, ...], "custom"?: str}``.
#: An empty list means "the user dismissed the question without answering";
#: a shorter list means the remaining questions were not answered.
AskHandler = Callable[[list[dict]], Awaitable[list[dict]] | list[dict]]

#: Question/option bounds the model must respect (omp's ask uses the same shape).
MAX_QUESTIONS = 5
MIN_OPTIONS = 2
MAX_OPTIONS = 6

#: What the model sees when no frontend owns the interactive surface.
_NO_UI_TEXT = (
    "No interactive UI is available to answer this question. Continue with the "
    "most conservative option, and state the assumption you made in your answer."
)

#: What the model sees when the user closed the dialog without answering.
_DISMISSED_TEXT = (
    "The user dismissed the question without answering. Continue with the most "
    "conservative option, and state the assumption you made in your answer."
)

_handler: AskHandler | None = None


def set_ask_handler(handler: AskHandler | None) -> None:
    """Install (or clear) the frontend that owns the interactive surface.

    Exactly one frontend per process answers ``ask`` calls: the TUI sets this in
    ``on_mount`` and clears it in ``on_unmount``, so a later headless call sees
    the graceful no-UI degrade instead of a stale dialog that can never appear.
    """
    global _handler
    _handler = handler


def get_ask_handler() -> AskHandler | None:
    """The currently installed frontend handler, if any."""
    return _handler


def _text(text: str, *, is_error: bool = False, **details) -> ToolResultAgent:
    return ToolResultAgent(
        content=[TextContent(text=text)], details=details, is_error=is_error
    )


def _validate_questions(raw: object) -> tuple[list[dict], str | None]:
    """Normalize the model's questions, or return ``([], problem)``.

    Validation happens before the handler is consulted: a malformed call must
    not pop a dialog the user cannot sensibly answer, and the model retries.
    """
    if not isinstance(raw, list):
        return [], "questions must be an array of question objects"
    if not raw:
        return [], "provide at least 1 question"
    if len(raw) > MAX_QUESTIONS:
        return [], f"at most {MAX_QUESTIONS} questions per call (got {len(raw)})"

    questions: list[dict] = []
    seen_ids: set[str] = set()
    for position, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            return [], f"question {position} must be an object"
        raw_id = item.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            return [], f'question {position} needs a non-empty "id"'
        question_id = raw_id.strip()
        if question_id in seen_ids:
            return [], f'duplicate question id "{question_id}"'
        seen_ids.add(question_id)

        raw_text = item.get("question")
        if not isinstance(raw_text, str) or not raw_text.strip():
            return [], f'question "{question_id}" needs non-empty "question" text'

        raw_header = item.get("header")
        if raw_header is not None and not isinstance(raw_header, str):
            return [], f'question "{question_id}": "header" must be a string'

        multi = item.get("multi", False)
        if not isinstance(multi, bool):
            return [], f'question "{question_id}": "multi" must be a boolean'

        raw_options = item.get("options")
        if not isinstance(raw_options, list):
            return [], f'question "{question_id}" needs an "options" array'
        if not MIN_OPTIONS <= len(raw_options) <= MAX_OPTIONS:
            return [], (
                f'question "{question_id}" needs {MIN_OPTIONS}-{MAX_OPTIONS} '
                f"options (got {len(raw_options)})"
            )

        options: list[dict] = []
        labels: list[str] = []
        for option_position, raw_option in enumerate(raw_options, start=1):
            if not isinstance(raw_option, dict):
                return [], (
                    f'question "{question_id}" option {option_position} must be '
                    'an object with a "label"'
                )
            raw_label = raw_option.get("label")
            if not isinstance(raw_label, str) or not raw_label.strip():
                return [], (
                    f'question "{question_id}" option {option_position} needs a '
                    'non-empty "label"'
                )
            label = raw_label.strip()
            if label in labels:
                return [], (
                    f'question "{question_id}" has duplicate option label "{label}"'
                )
            labels.append(label)
            raw_description = raw_option.get("description")
            if raw_description is not None and not isinstance(raw_description, str):
                return [], (
                    f'question "{question_id}" option "{label}": "description" '
                    "must be a string"
                )
            option = {"label": label}
            if isinstance(raw_description, str) and raw_description.strip():
                option["description"] = " ".join(raw_description.split())
            options.append(option)

        raw_recommended = item.get("recommended")
        if raw_recommended is not None:
            if (
                not isinstance(raw_recommended, str)
                or raw_recommended.strip() not in labels
            ):
                return [], (
                    f'question "{question_id}": "recommended" must match one of '
                    f'the option labels ({", ".join(labels)})'
                )
            raw_recommended = raw_recommended.strip()

        question: dict = {
            "id": question_id,
            "question": " ".join(raw_text.split()),
            "options": options,
            "multi": multi,
        }
        if isinstance(raw_header, str) and raw_header.strip():
            question["header"] = " ".join(raw_header.split())
        if raw_recommended:
            question["recommended"] = raw_recommended
        questions.append(question)
    return questions, None


def _normalize_answers(raw: object, questions: list[dict]) -> list[dict]:
    """Keep valid, known answers in question order (drops UI junk)."""
    if not isinstance(raw, list):
        return []
    wanted = {question["id"] for question in questions}
    by_id: dict[str, dict] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        question_id = entry.get("id")
        if not isinstance(question_id, str) or question_id not in wanted:
            continue
        raw_selected = entry.get("selected")
        selected = (
            [
                label.strip()
                for label in raw_selected
                if isinstance(label, str) and label.strip()
            ]
            if isinstance(raw_selected, list)
            else []
        )
        answer: dict = {"id": question_id, "selected": selected}
        raw_custom = entry.get("custom")
        if isinstance(raw_custom, str) and raw_custom.strip():
            answer["custom"] = raw_custom.strip()
        by_id[question_id] = answer
    return [by_id[question["id"]] for question in questions if question["id"] in by_id]


def _format_answers(questions: list[dict], answers: list[dict]) -> str:
    """Compact block the model reads, one line per question."""
    by_id = {answer["id"]: answer for answer in answers}
    lines = ["User answers:"]
    for question in questions:
        label = question.get("header") or question["id"]
        answer = by_id.get(question["id"])
        if answer is None:
            lines.append(f"- {label}: (no answer)")
            continue
        selected = (
            ", ".join(answer["selected"]) if answer["selected"] else "(no selection)"
        )
        line = f"- {label}: {selected}"
        custom = answer.get("custom")
        if custom:
            line += f"; custom: {custom}"
        lines.append(line)
    return "\n".join(lines)


async def _ask(tool_call_id: str, args: dict, abort=None, on_update=None) -> ToolResultAgent:
    questions, problem = _validate_questions(args.get("questions"))
    if problem is not None:
        return _text(f"invalid ask arguments: {problem}", is_error=True)

    handler = get_ask_handler()
    if handler is None:
        return _text(_NO_UI_TEXT, available=False)
    try:
        outcome = handler(questions)
        # Accept a plain sync callable too (a REPL reading stdin may not be async).
        raw_answers = await outcome if inspect.isawaitable(outcome) else outcome
    except Exception as exc:  # noqa: BLE001 - a UI failure must not kill the run
        return _text(
            f"the interactive UI failed to answer: {type(exc).__name__}: {exc}",
            is_error=True,
            available=True,
        )

    answers = _normalize_answers(raw_answers, questions)
    if not answers:
        return _text(_DISMISSED_TEXT, available=True, answers=[])
    return _text(
        _format_answers(questions, answers), available=True, answers=answers
    )


ASK = AgentTool(
    name="ask",
    description=(
        "Ask the user a structured question and wait for the answer. Use it "
        "only when the decision genuinely belongs to the user (an irreversible "
        "choice, a preference with no defensible default) or when you are "
        "blocked on information only they have; do not ask things you can find "
        "out with the other tools. Send 1-5 questions, each with 2-6 options; "
        "mark the obvious default as recommended. Without an interactive UI the "
        "call returns a note telling you to proceed with the most conservative "
        "option instead of blocking."
    ),
    parameters={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_QUESTIONS,
                "description": (
                    "1-5 questions. Each one is {id, question, header?, "
                    "options: [{label, description?}] (2-6 options), multi?, "
                    "recommended?}; recommended names one option label."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "question": {"type": "string"},
                        "header": {"type": "string"},
                        "options": {
                            "type": "array",
                            "minItems": MIN_OPTIONS,
                            "maxItems": MAX_OPTIONS,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["label"],
                            },
                        },
                        "multi": {"type": "boolean"},
                        "recommended": {"type": "string"},
                    },
                    "required": ["id", "question", "options"],
                },
            }
        },
        "required": ["questions"],
    },
    execute=_ask,
    approval="read",
)
