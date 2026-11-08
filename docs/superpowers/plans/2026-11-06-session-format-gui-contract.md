# Session format → GUI contract, pass 2: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a resumed session reconstruct what the UI showed — stable view ids, assistant metadata, tool durations/diffs, compaction dividers and steering provenance — without changing the provider contract.

**Architecture:** Two complementary changes. The view layer (`app/views.py`) gains identity and metadata that already exists in stored messages. New flat side-channel records (`tool_end`, `compaction`) persist what only lived in the live builder, and `Session.carry_records()` keeps them across the rewrite-style operations (`rewrite`, `fork`, retry, undo, shake, compact). One optional message field (`UserMessage.steering`) crosses the "don't touch `Message.to_json()`" fence under a strict byte-stability rule.

**Tech Stack:** Python 3.11+, dataclasses, JSONL, pytest/pytest-asyncio, ruff. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-06-session-format-gui-contract-design.md`

## Global Constraints

- Normal user-message lines stay byte-identical: `{"role": "user", "content": ..., "timestamp": ...}`. `"steering": true` is emitted **only** when true; never `false`.
- Side-channel records stay flat (`{"type": ..., "timestamp": ..., ...}`), have no `role` key, and are written only through `Session.append_meta` (best-effort; an `OSError` must never abort a run).
- `TOOL_DETAILS_LIMIT = 16_384` serialized characters; `TOOL_DIFF_MAX_LINES = 400`.
- No provider payload changes: adapters never read `steering` or session records.
- Wire changes are additive only: no existing RPC key is renamed, no timestamp is added to message lines.
- Python 3.11+ syntax (`X | None`); no new dependencies; `.venv/bin/ruff check zenagent tests` stays clean.
- Stage only the files named by the task. The working tree already has unrelated uncommitted changes (`scripts/eval.py`, `zenagent/http_client.py`, `desktop/`, a `Runtime.bootstrap` refactor); never `git add -A`.
- Tests for the RPC wire live in `tests/test_rpc_v2.py` (the harness is there); the spec's `tests/test_cli.py` reference is superseded because that file currently has unrelated bootstrap failures.

## Review Focus

These are the inputs/conditions the spec implies but no requirement names; each gets its test in the task that owns the code.

1. **Legacy resume** — a session file with no header, no records and no `steering` key must load and render without errors; `created_at` falls back to mtime and unpaired calls become `interrupted` (Tasks 5 and 2).
2. **Torn tail / unknown records** — an unknown `type` and a half-written final line must be skipped exactly as today, never crash `--resume` (Task 3).
3. **Steering aggregation** — several queued steering lines become one delivered message with one flag; a non-boolean stored value deserializes as `False` (Task 1).
4. **Huge tool details** — a multi-hundred-line diff stays under the serialized cap while scalar details survive (Task 3).
5. **Divider duplication** — two `transcript` fetches after one compaction return the same single `compactions` entry and no divider notice (Task 4).
6. **Abort parity** — a live run that dies mid-tool leaves the card `interrupted`, matching a reload (Task 2).

## File Structure

| File | Task | Responsibility |
|---|---|---|
| `zenagent/types.py` | 1 | `UserMessage.steering` + serialization |
| `zenagent/agent/agent.py` | 1 | mark delivered steering |
| `zenagent/app/views.py` | 1, 2, 3, 4 | view fields, ids, parity, hydration, notice source |
| `zenagent/cli/rpc/` | 1, 4, 5 | event projection + transcript/list wire |
| `zenagent/cli/tui/widgets/tool_card.py`, `zenagent/i18n/{en,zh}.py` | 2 | `interrupted` label |
| `zenagent/app/session_meta.py` | 3 | `tool_end_record` + bounding |
| `zenagent/agent/session.py` | 3, 5 | `ensure_loaded`, `carry_records`, info fields |
| `zenagent/app/chat.py` | 3, 4 | write `tool_end`, compact record |
| `zenagent/app/sessions.py` | 3, 5 | fork carry, `SessionEntry` fields |
| `zenagent/app/shake.py`, `zenagent/app/commands.py` | 3 | shake carry |
| `zenagent/app/compact.py` | 4 | `compaction` record + token count |
| `zenagent/cli/tui/screens/modals.py` | 5 | `updated_at` |
| `docs/guide/sessions.md`, `CHANGELOG.md` | 6 | user-facing docs |

---

### Task 1: Steering provenance end-to-end

**Files:**
- Modify: `zenagent/types.py` (`UserMessage`, `message_from_json`), `zenagent/agent/agent.py` (`_drain_steering`), `zenagent/app/views.py` (`MessageView`, `user_view`, `messages_to_views`, `_message_start`), `zenagent/cli/rpc/` (`_message_text`, `_message_projection`)
- Test: `tests/test_session.py`, `tests/test_app_views.py`, `tests/test_rpc_v2.py`, `tests/test_app_session_meta.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `UserMessage.steering: bool = False`; `MessageView.timestamp: int = 0`, `MessageView.steering: bool = False`; `user_view(text: str, *, timestamp: int = 0, steering: bool = False) -> MessageView`; `serialize_event()` carries `message.steering` only when true. Also fixes `_message_text` so a `UserMessage` with `str` content projects its text (today it yields `""`).

- [ ] **Step 1: Write the failing tests**

`tests/test_session.py` (add `message_from_json` to the existing import block):

```python
def test_plain_user_message_json_has_no_steering_key():
    assert user("hello").to_json() == {
        "role": "user",
        "content": "hello",
        "timestamp": 1,
    }


def test_steering_round_trips_and_only_true_serializes():
    message = UserMessage(content="nudge", timestamp=1, steering=True)
    assert message.to_json()["steering"] is True
    assert message_from_json(message.to_json()) == message

    stored = message_from_json(
        {"role": "user", "content": "x", "timestamp": 1, "steering": "yes"}
    )
    assert stored.steering is False
```

`tests/test_app_views.py`:

```python
def test_steering_and_timestamp_are_projected_onto_user_views():
    views = messages_to_views(
        [
            UserMessage(content="ask", timestamp=10),
            UserMessage(content="nudge", timestamp=11, steering=True),
        ]
    )
    assert [(view.timestamp, view.steering) for view in views] == [
        (10, False),
        (11, True),
    ]
```

`tests/test_rpc_v2.py` (add `AgentEvent`, `serialize_event` to the imports):

```python
def test_serialize_event_carries_user_text_and_steering():
    plain = serialize_event(
        AgentEvent(
            type="message_start",
            message=UserMessage(content="hello", timestamp=1),
        )
    )
    assert plain["message"] == {"role": "user", "text": "hello"}

    steered = serialize_event(
        AgentEvent(
            type="message_start",
            message=UserMessage(content="nudge", timestamp=1, steering=True),
        )
    )
    assert steered["message"]["text"] == "nudge"
    assert steered["message"]["steering"] is True
```

`tests/test_app_session_meta.py`:

```python
async def test_delivered_steering_aggregates_and_marks_the_message(session_runtime):
    agent = session_runtime.agent
    agent.steer("first")
    agent.steer("second")

    (message,) = await agent._drain_steering()

    assert isinstance(message, UserMessage)
    assert message.content == "first\nsecond"
    assert message.steering is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_session.py::test_plain_user_message_json_has_no_steering_key tests/test_session.py::test_steering_round_trips_and_only_true_serializes tests/test_app_views.py::test_steering_and_timestamp_are_projected_onto_user_views tests/test_rpc_v2.py::test_serialize_event_carries_user_text_and_steering tests/test_app_session_meta.py::test_delivered_steering_aggregates_and_marks_the_message -q`

Expected: FAIL — `UserMessage.__init__() got an unexpected keyword argument 'steering'`, and `plain["message"]["text"] == ""`.

- [ ] **Step 3: Implement the field and projection**

`zenagent/types.py`:

```python
@dataclass
class UserMessage:
    content: str | list[TextContent | ImageContent]
    timestamp: int
    #: True when the loop delivered this through the steering channel
    #: (provenance for the file and the UI; adapters never read it).
    steering: bool = False

    def to_json(self) -> dict:
        if isinstance(self.content, str):
            content: str | list[dict] = self.content
        else:
            content = [block.to_json() for block in self.content]
        payload = {"role": "user", "content": content, "timestamp": self.timestamp}
        if self.steering:
            payload["steering"] = True
        return payload
```

In `message_from_json`'s `role == "user"` branch, pass `steering=d.get("steering") is True`.

`zenagent/agent/agent.py` — `_drain_steering`:

```python
return [
    UserMessage(
        content="\n".join(lines),
        timestamp=int(time.time()),
        steering=True,
    )
]
```

`zenagent/app/views.py` — append two fields to `MessageView` (after `thinking_seconds`) and thread them through `user_view` and both call sites:

```python
    #: Epoch seconds of the stored message; 0 for a live row with no
    #: canonical message yet.
    timestamp: int = 0
    #: True when the user message arrived through the steering channel.
    steering: bool = False


def user_view(
    text: str, *, timestamp: int = 0, steering: bool = False
) -> MessageView:
    display = parse_skill_display(text)
    if display is not None:
        return MessageView(
            kind="skill",
            text=display.name,
            skill_args=display.args,
            skill_body=display.body,
            timestamp=timestamp,
            steering=steering,
        )
    return MessageView(kind="user", text=text, timestamp=timestamp, steering=steering)
```

In `messages_to_views`, replace the user branch's `views.append(user_view(text))` with:

```python
views.append(
    user_view(text, timestamp=message.timestamp, steering=message.steering)
)
```

In `TranscriptBuilder._message_start`, replace the user branch's `user_view(_user_text(message))` with:

```python
self.views.append(
    user_view(
        _user_text(message),
        timestamp=message.timestamp,
        steering=message.steering,
    )
)
```

`zenagent/cli/rpc/`:

```python
def _message_text(message) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    return _text_of(message)
```

In `_message_projection`, after the `is_error` block:

```python
    if getattr(message, "steering", False):
        payload["steering"] = True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_session.py tests/test_app_views.py tests/test_rpc_v2.py tests/test_app_session_meta.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/types.py zenagent/agent/agent.py zenagent/app/views.py zenagent/cli/rpc/ tests/test_session.py tests/test_app_views.py tests/test_rpc_v2.py tests/test_app_session_meta.py
git commit -m "feat(session): mark delivered steering messages end-to-end"
```

---

### Task 2: View identity, assistant metadata, parity and interrupted cards

**Files:**
- Modify: `zenagent/app/views.py` (`MessageView`, `ToolCallView`, `messages_to_views`, `TranscriptBuilder`), `zenagent/cli/tui/widgets/tool_card.py`, `zenagent/i18n/en.py`, `zenagent/i18n/zh.py`
- Test: `tests/test_app_views.py`, `tests/test_tui_tool_card.py`

**Interfaces:**
- Consumes: `MessageView.timestamp/steering` from Task 1.
- Produces: `MessageView.id/timestamp/model/provider/usage/duration_ms`; `ToolCallView.id`; `ToolCallView.status` literal gains `"interrupted"`; `messages_to_views()` assigns `v{n}` and marks result-less calls interrupted; `TranscriptBuilder` drops empty assistant rows and marks still-running cards interrupted at `finalize()`.

- [ ] **Step 1: Write the failing tests**

`tests/test_app_views.py`:

```python
def test_view_ids_are_reload_deterministic():
    messages = [
        UserMessage(content="go", timestamp=1),
        AssistantMessage(
            content=[TextContent(text="reading"), ToolCall(id="c1", name="read_file", arguments={})],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="tool_use",
            timestamp=2,
        ),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="read_file",
            content=[TextContent(text="ok")],
            is_error=False,
            timestamp=3,
        ),
    ]
    first = messages_to_views(messages)
    second = messages_to_views(messages)
    assert [view.id for view in first] == ["v1", "v2", "v3"]
    assert [view.id for view in second] == [view.id for view in first]


def test_assistant_metadata_is_projected_into_the_view():
    assistant = AssistantMessage(
        content=[TextContent(text="hi")],
        provider="anthropic",
        model="claude-x",
        usage=Usage(input=3, output=2),
        stop_reason="stop",
        timestamp=7,
        duration_ms=42,
    )
    (view,) = messages_to_views([assistant])
    assert view.id == "v1"
    assert (view.timestamp, view.model, view.provider, view.duration_ms) == (
        7,
        "claude-x",
        "anthropic",
        42,
    )
    assert view.usage is assistant.usage


def test_unpaired_tool_call_is_interrupted():
    assistant = AssistantMessage(
        content=[ToolCall(id="c1", name="bash", arguments={})],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="tool_use",
        timestamp=2,
    )
    (card,) = messages_to_views([assistant])
    assert isinstance(card, ToolCallView)
    assert card.status == "interrupted"


def test_builder_drops_an_empty_assistant_row_at_message_end():
    from zenagent.app.views import TranscriptBuilder
    from zenagent.events import AgentEvent

    empty = AssistantMessage(
        content=[],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="tool_use",
        timestamp=2,
    )
    builder = TranscriptBuilder()
    builder.apply(AgentEvent(type="message_start", message=empty))
    builder.apply(AgentEvent(type="message_end", message=empty))
    assert builder.views == []


def test_builder_keeps_a_thinking_only_row():
    from zenagent.app.views import TranscriptBuilder
    from zenagent.events import AgentEvent

    thinking = AssistantMessage(
        content=[ThinkingContent(thinking="hmm")],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="stop",
        timestamp=2,
    )
    builder = TranscriptBuilder()
    builder.apply(AgentEvent(type="message_start", message=thinking))
    builder.apply(AgentEvent(type="message_end", message=thinking))
    assert [view.thinking for view in builder.views] == ["hmm"]


def test_builder_finalize_drops_empty_row_and_interrupts_running_cards():
    from zenagent.app.views import TranscriptBuilder
    from zenagent.events import AgentEvent

    empty = AssistantMessage(
        content=[],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="pending",
        timestamp=2,
    )
    builder = TranscriptBuilder()
    builder.apply(AgentEvent(type="message_start", message=empty))
    builder.apply(
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="c1",
            tool_name="bash",
            args={"command": "sleep 9"},
        )
    )
    builder.finalize()
    assert [view.id for view in builder.views] == ["v2"]
    assert builder.cards["c1"].status == "interrupted"


def test_builder_ids_keep_positions_when_an_empty_row_is_dropped():
    from zenagent.app.views import TranscriptBuilder
    from zenagent.events import AgentEvent

    empty = AssistantMessage(
        content=[],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="tool_use",
        timestamp=2,
    )
    builder = TranscriptBuilder()
    builder.apply(
        AgentEvent(
            type="message_start",
            message=UserMessage(content="hi", timestamp=1),
        )
    )
    builder.apply(AgentEvent(type="message_start", message=empty))
    builder.apply(AgentEvent(type="message_end", message=empty))
    builder.apply(
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="c1",
            tool_name="bash",
            args={},
        )
    )
    assert [view.id for view in builder.views] == ["v1", "v3"]
```

`tests/test_tui_tool_card.py`:

```python
def test_interrupted_card_has_a_label():
    text = ToolCard(view(status="interrupted", result_text="")).plain
    assert "interrupted" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_app_views.py tests/test_tui_tool_card.py -q`

Expected: FAIL — views have no `id`, unpaired cards stay `running`, `ToolCard` renders the raw `"interrupted"` string only after the dict entry exists (so this last test may pass trivially; the view tests must fail).

- [ ] **Step 3: Implement identity, metadata and parity**

`zenagent/app/views.py` — `MessageView` (fields may sit after `steering` from Task 1):

```python
    #: Stable row identity inside one append-only session (``v{n}``); a
    #: rewrite/fork/compact starts a new id space.
    id: str = ""
    #: Assistant metadata (empty/None for user and skill rows).
    model: str = ""
    provider: str = ""
    usage: Usage | None = None
    duration_ms: int | None = None
```

`ToolCallView`:

```python
    id: str = ""
    status: Literal["running", "ok", "error", "interrupted"] = "running"
```

`messages_to_views` — track paired results and assign ids at the end:

```python
    paired: set[str] = set()
    ...
        elif isinstance(message, ToolResultMessage):
            card = cards.get(message.tool_call_id)
            if card is None:
                continue
            paired.add(message.tool_call_id)
            card.result_text = _text_of(message.content)
            card.is_error = message.is_error
            card.status = "error" if message.is_error else "ok"

    for call_id, card in cards.items():
        if call_id not in paired and card.status == "running":
            card.status = "interrupted"
    for position, view in enumerate(views, start=1):
        view.id = f"v{position}"
    return views
```

`TranscriptBuilder`:

```python
    def __init__(self) -> None:
        self.views: list[MessageView | ToolCallView] = []
        self.cards: dict[str, ToolCallView] = {}
        self.current: MessageView | None = None
        self._next_id = 1

    def reset(self) -> None:
        self.views.clear()
        self.cards.clear()
        self.current = None
        self._next_id = 1

    def load(self, messages: list[Message]) -> None:
        self.views = messages_to_views(messages)
        self.cards = {
            view.call_id: view for view in self.views if isinstance(view, ToolCallView)
        }
        self.current = None
        self._next_id = len(self.views) + 1

    def _append(self, view: MessageView | ToolCallView) -> None:
        view.id = f"v{self._next_id}"
        self._next_id += 1
        self.views.append(view)
```

Use `_append` for: the user row, the assistant row at `message_start`, the lazily created text row in `_assistant_event`, and the tool card at `tool_execution_start`.

`_message_start` for an assistant also copies metadata:

```python
        elif isinstance(message, AssistantMessage):
            self.current = MessageView(kind="assistant", text="")
            self._apply_metadata(self.current, message)
            self._append(self.current)
```

Add:

```python
    @staticmethod
    def _apply_metadata(view: MessageView, message: AssistantMessage) -> None:
        view.timestamp = message.timestamp
        view.model = message.model
        view.provider = message.provider
        view.usage = message.usage
        view.duration_ms = message.duration_ms
```

`_message_end` drops the row when it ends empty:

```python
    def _message_end(self, message: AssistantMessage) -> None:
        if self.current is None:
            return
        self._finish_thinking(self.current)
        self.current.text = _assistant_text(message)
        self.current.thinking = _assistant_thinking(message) or self.current.thinking
        self._apply_metadata(self.current, message)
        if not self.current.text and not self.current.thinking:
            self.views[:] = [
                view for view in self.views if view is not self.current
            ]
        self.current = None
```

`finalize` drops an empty live row and settles abandoned cards:

```python
    def finalize(self) -> None:
        if self.current is not None:
            view = self.current
            self._finish_thinking(view)
            if not view.text and not view.thinking:
                self.views[:] = [item for item in self.views if item is not view]
            self.current = None
        for card in self.cards.values():
            if card.status == "running":
                card.status = "interrupted"
```

`zenagent/cli/tui/widgets/tool_card.py`:

```python
_STATUS_DOT = {
    "running": ("◐", palette.ACCENT),
    "ok": ("●", palette.MUTED),
    "error": ("●", palette.ERROR),
    "interrupted": ("●", palette.MUTED),
}
```

and add `"interrupted": "tui.tool.status_interrupted"` to `_STATUS_KEYS`.

`zenagent/i18n/en.py`: `"tui.tool.status_interrupted": "interrupted",` next to the other tool statuses.
`zenagent/i18n/zh.py`: `"tui.tool.status_interrupted": "已中断",`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_app_views.py tests/test_tui_tool_card.py tests/test_i18n_tui.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/views.py zenagent/cli/tui/widgets/tool_card.py zenagent/i18n/en.py zenagent/i18n/zh.py tests/test_app_views.py tests/test_tui_tool_card.py
git commit -m "feat(views): stable ids, assistant metadata and interrupted cards"
```

---

### Task 3: `tool_end` records, hydration and rewrite carry

**Files:**
- Modify: `zenagent/app/session_meta.py`, `zenagent/agent/session.py`, `zenagent/app/chat.py`, `zenagent/app/sessions.py`, `zenagent/app/shake.py`, `zenagent/app/commands.py`
- Test: `tests/test_app_session_meta.py`, `tests/test_app_views.py`, `tests/test_session.py`, `tests/test_app_sessions.py`, `tests/test_app_chat.py`, `tests/test_app_commands.py`

**Interfaces:**
- Consumes: `TranscriptBuilder`/`ToolCallView` (Task 2), `Session.records`/`header` (existing).
- Produces: `TOOL_DETAILS_LIMIT`, `TOOL_DIFF_MAX_LINES`; `tool_end_record(call_id: str, tool_name: str, duration_ms: int | None, is_error: bool, details: dict | None) -> dict`; `Session.ensure_loaded() -> None`; `Session.carry_records(messages: list[Message], *, tool_end: bool = True) -> list[dict]`; `messages_to_views(messages, records=None)`; `TranscriptBuilder.load(messages, records=None)`; `shake(agent, kinds, derived_from="", *, source: Session | None = None)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_app_session_meta.py` (add `tool_end_record`, constants to the import):

```python
def test_tool_end_record_shape():
    record = tool_end_record(
        "c1", "edit_file", 812, False, {"diff": "+x", "exit_code": 0}
    )
    assert record == {
        "type": "tool_end",
        "call_id": "c1",
        "tool_name": "edit_file",
        "duration_ms": 812,
        "is_error": False,
        "details": {"diff": "+x", "exit_code": 0},
    }


def test_tool_end_record_truncates_a_long_diff():
    diff = "\n".join(f"+line {index}" for index in range(TOOL_DIFF_MAX_LINES + 50))
    record = tool_end_record(
        "c1", "edit_file", 1, False, {"diff": diff, "exit_code": 0}
    )
    details = record["details"]
    assert details["diff"].count("\n") == TOOL_DIFF_MAX_LINES - 1
    assert details["diff_truncated"] is True
    assert details["exit_code"] == 0


def test_tool_end_record_caps_serialized_details():
    details = {"blob": "x" * (TOOL_DETAILS_LIMIT * 2), "exit_code": 0}
    record = tool_end_record("c1", "bash", 1, False, details)
    assert len(json.dumps(record["details"], ensure_ascii=False)) <= TOOL_DETAILS_LIMIT
    assert record["details"]["details_truncated"] is True


def test_tool_end_record_omits_empty_details():
    assert tool_end_record("c1", "bash", None, False, None) == {
        "type": "tool_end",
        "call_id": "c1",
        "tool_name": "bash",
        "is_error": False,
    }
```

`tests/test_app_views.py`:

```python
def test_load_hydrates_cards_from_tool_end_records():
    from zenagent.app.views import TranscriptBuilder

    messages = [
        UserMessage(content="go", timestamp=1),
        AssistantMessage(
            content=[ToolCall(id="c1", name="edit_file", arguments={})],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="tool_use",
            timestamp=2,
        ),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="edit_file",
            content=[TextContent(text="edited")],
            is_error=False,
            timestamp=3,
        ),
    ]
    records = [
        {
            "type": "tool_end",
            "call_id": "c1",
            "duration_ms": 812,
            "details": {"diff": "+x", "exit_code": 0},
        }
    ]
    builder = TranscriptBuilder()
    builder.load(messages, records)
    card = builder.cards["c1"]
    assert (card.status, card.duration_ms, card.diff) == ("ok", 812, "+x")
    assert card.details["exit_code"] == 0


def test_orphan_and_unknown_records_are_ignored():
    from zenagent.app.views import TranscriptBuilder

    messages = [
        AssistantMessage(
            content=[ToolCall(id="c1", name="bash", arguments={})],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="tool_use",
            timestamp=2,
        ),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="bash",
            content=[TextContent(text="ok")],
            is_error=False,
            timestamp=3,
        ),
    ]
    builder = TranscriptBuilder()
    builder.load(
        messages,
        [
            {"type": "tool_end", "call_id": "gone", "duration_ms": 1},
            {"type": "future_record"},
        ],
    )
    assert builder.cards["c1"].duration_ms is None


def test_last_tool_end_record_wins():
    from zenagent.app.views import TranscriptBuilder

    messages = [
        AssistantMessage(
            content=[ToolCall(id="c1", name="bash", arguments={})],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="tool_use",
            timestamp=2,
        ),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="bash",
            content=[TextContent(text="ok")],
            is_error=False,
            timestamp=3,
        ),
    ]
    builder = TranscriptBuilder()
    builder.load(
        messages,
        [
            {"type": "tool_end", "call_id": "c1", "duration_ms": 1},
            {"type": "tool_end", "call_id": "c1", "duration_ms": 2},
        ],
    )
    assert builder.cards["c1"].duration_ms == 2
```

`tests/test_session.py` (add `ToolCall` and the new helpers):

```python
def assistant_call(call_id: str = "c1") -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCall(id=call_id, name="read_file", arguments={})],
        provider="test",
        model="m",
        usage=Usage(),
        stop_reason="tool_use",
        timestamp=2,
    )


def test_records_survive_load(isolated_sessions):
    session = Session(isolated_sessions / "s.jsonl")
    session.append(user("hi"))
    session.append_meta({"type": "tool_end", "call_id": "c1", "duration_ms": 5})
    session.append_meta({"type": "compaction", "tokensBefore": 10})

    loaded = Session(session.path)
    loaded.load()

    assert [record["type"] for record in loaded.records] == [
        "session",
        "tool_end",
        "compaction",
    ]


def test_carry_records_keeps_live_calls_and_compactions(isolated_sessions):
    source = Session(isolated_sessions / "source.jsonl")
    messages = [user("go"), assistant_call("c1")]
    for message in messages:
        source.append(message)
    source.append_meta({"type": "tool_end", "call_id": "c1", "duration_ms": 5})
    source.append_meta({"type": "tool_end", "call_id": "gone", "duration_ms": 6})
    source.append_meta({"type": "compaction", "tokensBefore": 10})
    source.append_meta({"type": "run_start"})

    carried = source.carry_records(messages)

    assert [(record["type"], record.get("call_id")) for record in carried] == [
        ("tool_end", "c1"),
        ("compaction", None),
    ]


def test_carry_records_can_skip_tool_end(isolated_sessions):
    source = Session(isolated_sessions / "source.jsonl")
    messages = [user("go"), assistant_call("c1")]
    for message in messages:
        source.append(message)
    source.append_meta({"type": "tool_end", "call_id": "c1", "duration_ms": 5})
    source.append_meta({"type": "compaction", "tokensBefore": 10})

    carried = source.carry_records(messages, tool_end=False)

    assert [record["type"] for record in carried] == ["compaction"]


def test_rewrite_carries_records_for_kept_calls(isolated_sessions):
    source = Session(isolated_sessions / "source.jsonl")
    messages = [user("go"), assistant_call("c1")]
    for message in messages:
        source.append(message)
    source.append_meta({"type": "tool_end", "call_id": "c1", "duration_ms": 5})
    source.append_meta({"type": "compaction", "tokensBefore": 10})

    reloaded = Session(source.rewrite(messages))
    reloaded.load()

    assert [record["type"] for record in reloaded.records] == [
        "session",
        "tool_end",
        "compaction",
    ]
```

`tests/test_app_sessions.py`:

```python
async def test_fork_carries_tool_end_records_for_copied_calls(session_runtime):
    from zenagent.agent.session import Session
    from zenagent.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage, Usage, UserMessage

    runtime = session_runtime
    manager = SessionManager(runtime)
    runtime.session.append(UserMessage(content="go", timestamp=1))
    runtime.session.append(
        AssistantMessage(
            content=[ToolCall(id="c1", name="read_file", arguments={})],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="tool_use",
            timestamp=2,
        )
    )
    runtime.session.append(
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="read_file",
            content=[TextContent(text="ok")],
            is_error=False,
            timestamp=3,
        )
    )
    runtime.session.append_meta(
        {"type": "tool_end", "call_id": "c1", "duration_ms": 9}
    )

    await manager.fork()

    reloaded = Session(manager.current())
    reloaded.load()
    assert [record["type"] for record in reloaded.records] == [
        "session",
        "tool_end",
    ]
```

`tests/test_app_chat.py` (the test imports its helpers locally, matching the file's style):

```python
async def test_tool_completion_writes_a_tool_end_record(runtime_factory):
    from zenagent.agent.loop import ToolResultAgent
    from zenagent.agent.session import Session
    from zenagent.events import AgentEvent

    runtime = await runtime_factory()
    chat = ChatController(runtime)
    chat._on_event(
        AgentEvent(
            type="tool_execution_end",
            tool_call_id="c1",
            tool_name="bash",
            result=ToolResultAgent(
                content=[TextContent(text="done")], details={"exit_code": 0}
            ),
            is_error=False,
            duration_ms=12,
        )
    )

    session = Session(runtime.session.path)
    session.load()
    record = [item for item in session.records if item["type"] == "tool_end"][-1]
    assert record["call_id"] == "c1"
    assert record["duration_ms"] == 12
    assert record["details"] == {"exit_code": 0}
```

`tests/test_app_commands.py`:

```python
async def test_shake_tools_does_not_carry_tool_end_records(services):
    from zenagent.agent.session import Session
    from zenagent.types import AssistantMessage, ToolCall, ToolResultMessage, TextContent, Usage, UserMessage

    runtime = services.runtime
    messages = [
        UserMessage(content="go", timestamp=1),
        AssistantMessage(
            content=[ToolCall(id="c1", name="bash", arguments={})],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="tool_use",
            timestamp=2,
        ),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="bash",
            content=[TextContent(text="lots of output")],
            is_error=False,
            timestamp=3,
        ),
    ]
    runtime.agent.replace_messages(messages)
    for message in messages:
        runtime.session.append(message)
    runtime.session.append_meta(
        {"type": "tool_end", "call_id": "c1", "details": {"diff": "+x"}}
    )

    await build_default_commands(services).dispatch("shake", "tools")

    reloaded = Session(runtime.session.path)
    reloaded.load()
    assert not [item for item in reloaded.records if item["type"] == "tool_end"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_app_session_meta.py tests/test_app_views.py tests/test_session.py tests/test_app_sessions.py tests/test_app_chat.py tests/test_app_commands.py -q`

Expected: FAIL — `tool_end_record` is undefined, `carry_records`/`ensure_loaded` do not exist, `load()` takes one argument.

- [ ] **Step 3: Implement records, hydration and carry**

`zenagent/app/session_meta.py`:

```python
import json

TOOL_DETAILS_LIMIT = 16_384
TOOL_DIFF_MAX_LINES = 400


def _json_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _bound_details(details: dict | None) -> dict:
    data = dict(details or {})
    diff = data.get("diff")
    if isinstance(diff, str):
        lines = diff.splitlines()
        if len(lines) > TOOL_DIFF_MAX_LINES:
            data["diff"] = "\n".join(lines[:TOOL_DIFF_MAX_LINES])
            data["diff_truncated"] = True
    while _json_size(data) > TOOL_DETAILS_LIMIT and data:
        if "diff" in data:
            data.pop("diff")
            data["diff_truncated"] = True
            continue
        key = max(
            (
                name
                for name in data
                if name not in ("diff_truncated", "details_truncated")
            ),
            key=lambda name: _json_size(data[name]),
            default=None,
        )
        if key is None:
            break
        data.pop(key)
        data["details_truncated"] = True
    return data


def tool_end_record(
    call_id: str,
    tool_name: str,
    duration_ms: int | None,
    is_error: bool,
    details: dict | None,
) -> dict:
    """One ``tool_end`` record; ``append_meta`` stamps the timestamp."""
    record = {
        "type": "tool_end",
        "call_id": call_id,
        "tool_name": tool_name,
        "is_error": bool(is_error),
    }
    if duration_ms is not None:
        record["duration_ms"] = int(duration_ms)
    bounded = _bound_details(details)
    if bounded:
        record["details"] = bounded
    return record
```

Add both names to `__all__` in that module.

`zenagent/agent/session.py` — imports gain `AssistantMessage`, `ToolCall`:

```python
    def ensure_loaded(self) -> None:
        """Read records/header from disk once, like the messages cache."""
        if self._messages is None:
            self.load()

    def carry_records(
        self, messages: list[Message], *, tool_end: bool = True
    ) -> list[dict]:
        """Records that still describe ``messages`` after a rewrite.

        ``tool_end`` records follow the call ids still present; ``compaction``
        records always travel. Run boundaries, retries and mode changes are
        dropped: a truncated file must not claim events whose messages are gone.
        """
        self.ensure_loaded()
        call_ids = {
            block.id
            for message in messages
            if isinstance(message, AssistantMessage)
            for block in message.content
            if isinstance(block, ToolCall)
        }
        carried: list[dict] = []
        for record in self.records:
            kind = record.get("type")
            if kind == "compaction":
                carried.append(record)
            elif (
                tool_end
                and kind == "tool_end"
                and record.get("call_id") in call_ids
            ):
                carried.append(record)
        return carried
```

`rewrite` appends the carried records after the kept messages (the call is
already `self.load()`-ed by `carry_records` when needed):

```python
        for record in self.carry_records(messages):
            rewritten.append_meta(record)
```

`fork_at` does the same with its `forked` session and the `prefix` it copied:

```python
        for record in self.carry_records(prefix):
            forked.append_meta(record)
```

`SessionManager.fork` (`zenagent/app/sessions.py`) does the same with the source session:

```python
        source_session = Session(source)
        messages = source_session.load()
        target = Session(unique_session_path("fork"), derived_from=source.stem)
        for message in messages:
            target.append(message)
        for record in source_session.carry_records(messages):
            target.append_meta(record)
```

`zenagent/app/chat.py` — in `_on_event`, inside the existing `tool_execution_end` branch, write the record from the live session before the HUD bookkeeping:

```python
            if event.tool_call_id:
                self.runtime.session.append_meta(
                    tool_end_record(
                        event.tool_call_id,
                        event.tool_name or "",
                        event.duration_ms,
                        bool(event.is_error),
                        details,
                    )
                )
```

Import `tool_end_record` from `zenagent.app.session_meta`.

`reload_transcript`:

```python
        session = getattr(self.runtime, "session", None)
        if session is not None:
            session.ensure_loaded()
        records = list(getattr(session, "records", []) or [])
        self._builder.load(self.runtime.agent.messages, records)
```

`zenagent/app/shake.py` — add `source: Session | None = None` (keyword) and carry after the messages:

```python
    if source is not None:
        for record in source.carry_records(
            new_messages, tool_end="tools" not in kinds
        ):
            session.append_meta(record)
```

`zenagent/app/commands.py` `_shake` passes the source session:

```python
        changed, session = shake(
            services.runtime.agent,
            kinds,
            derived_from=services.runtime.session.path.stem,
            source=services.runtime.session,
        )
```

`zenagent/app/views.py` hydration:

```python
def _apply_tool_records(
    views: list[MessageView | ToolCallView], records: list[dict] | None
) -> None:
    cards = {
        view.call_id: view for view in views if isinstance(view, ToolCallView)
    }
    for record in records or ():
        if record.get("type") != "tool_end":
            continue
        card = cards.get(record.get("call_id"))
        if card is None:
            continue
        duration = record.get("duration_ms")
        if isinstance(duration, int):
            card.duration_ms = duration
        details = record.get("details")
        if isinstance(details, dict):
            card.details = dict(details)
            diff = details.get("diff")
            card.diff = diff if isinstance(diff, str) and diff else None


def messages_to_views(
    messages: list[Message], records: list[dict] | None = None
) -> list[MessageView | ToolCallView]:
```

Call `_apply_tool_records(views, records)` after pairing and interrupted marking, before assigning ids. `TranscriptBuilder.load(self, messages, records=None)` forwards `records`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_app_session_meta.py tests/test_app_views.py tests/test_session.py tests/test_app_sessions.py tests/test_app_chat.py tests/test_app_commands.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/session_meta.py zenagent/agent/session.py zenagent/app/chat.py zenagent/app/sessions.py zenagent/app/shake.py zenagent/app/commands.py zenagent/app/views.py tests/test_app_session_meta.py tests/test_app_views.py tests/test_session.py tests/test_app_sessions.py tests/test_app_chat.py tests/test_app_commands.py
git commit -m "feat(session): persist tool completion as tool_end records"
```

---

### Task 4: `compaction` record and one divider on the wire

**Files:**
- Modify: `zenagent/app/compact.py`, `zenagent/app/chat.py`, `zenagent/app/views.py` (`TranscriptNotice`), `zenagent/cli/rpc/` (`_transcript_result`)
- Test: `tests/test_compact.py`, `tests/test_app_chat.py`, `tests/test_rpc_v2.py`

**Interfaces:**
- Consumes: `Session.carry_records` (Task 3), `TranscriptBuilder.load` (Task 3).
- Produces: `compact(agent, stream_fn=None, derived_from="", *, reason="manual", tokens_before=None, carry_from=None) -> Path`; `TranscriptNotice.source: str = ""`; `transcript` result gains `compactions: list[dict]` (oldest first) and drops notices whose `source == "compaction"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_compact.py`:

```python
async def test_compact_writes_the_compaction_record_before_the_summary(isolated_sessions):
    agent = agent_with_history(summary_stream("摘要"))

    path = await compact(
        agent, derived_from="parent", reason="manual", tokens_before=1234
    )

    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line.get("type") for line in lines[:2]] == ["session", "compaction"]
    assert lines[1]["tokensBefore"] == 1234
    assert lines[1]["derivedFrom"] == "parent"
    assert lines[1]["reason"] == "manual"
    assert lines[2]["role"] == "user"


async def test_compact_defaults_tokens_before_to_the_last_usage(isolated_sessions):
    agent = agent_with_history(summary_stream("摘要"))
    agent.messages[-1].usage = Usage(input=900, cache_read=100)

    path = await compact(agent)

    record = next(
        item
        for item in (json.loads(line) for line in path.read_text().splitlines())
        if item.get("type") == "compaction"
    )
    assert record["tokensBefore"] == 1000
    assert record["reason"] == "manual"
```

`tests/test_app_chat.py` (add `text_stream` from `conftest`, and `Session`):

```python
async def test_compact_now_sources_the_divider_and_persists_tokens(runtime_factory):
    from conftest import text_stream
    from zenagent.agent.session import Session

    runtime = await runtime_factory(stream_fn=text_stream("summary"))
    runtime.agent.replace_messages(
        [
            UserMessage(content="long chat", timestamp=1),
            AssistantMessage(
                content=[TextContent(text="done")],
                provider="p",
                model="m",
                usage=Usage(input=500),
                stop_reason="stop",
                timestamp=2,
            ),
        ]
    )
    chat = ChatController(runtime)

    notice = await chat.compact_now()

    assert notice
    assert chat.notices_snapshot[-1].source == "compaction"
    session = Session(runtime.session.path)
    session.load()
    record = next(item for item in session.records if item["type"] == "compaction")
    assert record["reason"] == "manual"
    assert record["tokensBefore"] == 500
```

`tests/test_rpc_v2.py`:

```python
async def test_transcript_exposes_one_compaction_across_refetches(runtime_factory):
    runtime, server, writes, queue = await build(runtime_factory)
    runtime.agent.replace_messages(
        [
            UserMessage(content="long chat", timestamp=1),
            AssistantMessage(
                content=[TextContent(text="done")],
                provider="p",
                model="m",
                usage=Usage(input=500),
                stop_reason="stop",
                timestamp=2,
            ),
        ]
    )
    await server.chat.compact_now()

    await run_lines(
        server,
        queue,
        [
            {"type": "transcript", "id": "t1"},
            {"type": "transcript", "id": "t2"},
        ],
    )
    payloads = {message["id"]: message for message in decode(writes)}

    assert [record["type"] for record in payloads["t1"]["compactions"]] == [
        "compaction"
    ]
    assert payloads["t1"]["notices"] == []
    assert payloads["t2"]["compactions"] == payloads["t1"]["compactions"]
    assert payloads["t1"]["transcript"][0]["id"] == "v1"


async def test_transcript_views_carry_ids_and_assistant_metadata(runtime_factory):
    runtime, server, writes, queue = await build(runtime_factory)
    runtime.agent.replace_messages(
        [
            AssistantMessage(
                content=[TextContent(text="hi")],
                provider="p",
                model="deepseek-chat",
                usage=Usage(input=3, output=2),
                stop_reason="stop",
                timestamp=7,
                duration_ms=42,
            )
        ]
    )
    server.chat.reload_transcript()

    await run_lines(server, queue, [{"type": "transcript", "id": "t"}])

    view = decode(writes)[0]["transcript"][0]
    assert view["id"] == "v1"
    assert view["model"] == "deepseek-chat"
    assert view["duration_ms"] == 42
    assert view["timestamp"] == 7
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_compact.py::test_compact_writes_the_compaction_record_before_the_summary tests/test_compact.py::test_compact_defaults_tokens_before_to_the_last_usage tests/test_app_chat.py::test_compact_now_sources_the_divider_and_persists_tokens tests/test_rpc_v2.py::test_transcript_exposes_one_compaction_across_refetches tests/test_rpc_v2.py::test_transcript_views_carry_ids_and_assistant_metadata -q`

Expected: FAIL — no `reason`/`tokens_before` parameters, no `TranscriptNotice.source`, no `compactions` key.

- [ ] **Step 3: Implement the record and wire**

`zenagent/app/compact.py`:

```python
async def compact(
    agent: Agent,
    stream_fn: StreamFn | None = None,
    derived_from: str = "",
    *,
    reason: str = "manual",
    tokens_before: int | None = None,
    carry_from: Session | None = None,
) -> Path:
```

Before building the request (while `agent.messages` is still the old transcript):

```python
    if tokens_before is None:
        usage = _last_usage(agent)
        tokens_before = usage.input + usage.cache_read if usage is not None else 0
```

After the summary message is built and the new session created:

```python
    session = Session(unique_session_path("compact"), derived_from=derived_from)
    if carry_from is not None:
        for record in carry_from.carry_records([message]):
            session.append_meta(record)
    session.append_meta(
        {
            "type": "compaction",
            "tokensBefore": int(tokens_before),
            "derivedFrom": derived_from,
            "reason": reason,
        }
    )
    session.append(message)
```

Carried records come first so the `compactions` array stays oldest-first; the new record still precedes the summary, as the spec requires.

`maybe_auto_compact` passes `reason="auto"`:

```python
        path = await compact(agent, stream_fn, reason="auto")
```

`zenagent/app/chat.py` `compact_now`:

```python
        path = await compact(
            self.runtime.agent,
            derived_from=old_path.stem,
            reason="auto" if force else "manual",
            tokens_before=before,
            carry_from=Session(old_path),
        )
```

Import `Session` from `zenagent.agent.session`. The divider notice becomes:

```python
        self._notices.append(
            TranscriptNotice("divider", text, source="compaction")
        )
```

`zenagent/app/views.py`:

```python
@dataclass(frozen=True)
class TranscriptNotice:
    kind: Literal["divider", "info"]
    text: str
    #: Where the notice came from; ``"compaction"`` notices are persisted as
    #: records and excluded from the RPC snapshot to avoid a double divider.
    source: str = ""
```

`zenagent/cli/rpc/` `_transcript_result`:

```python
        views: list = []
        notices: list = []
        compactions: list[dict] = []
        if self.chat is not None:
            session = getattr(self.runtime, "session", None)
            if session is not None:
                session.ensure_loaded()
                compactions = [
                    record
                    for record in session.records
                    if record.get("type") == "compaction"
                ]
            views = [asdict(view) for view in self.chat.transcript]
            notices = [
                asdict(notice)
                for notice in self.chat.notices_snapshot
                if notice.source != "compaction"
            ]
        return {
            "type": "result",
            "id": request_id,
            "ok": True,
            "transcript": views,
            "notices": notices,
            "compactions": compactions,
        }
```

Guard `self.runtime` with `getattr` as shown (an agent-only server has no runtime).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_compact.py tests/test_app_chat.py tests/test_rpc_v2.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/compact.py zenagent/app/chat.py zenagent/app/views.py zenagent/cli/rpc/ tests/test_compact.py tests/test_app_chat.py tests/test_rpc_v2.py
git commit -m "feat(session): persist compaction records and expose them on transcript"
```

---

### Task 5: Session-list creation, update, model and lineage

**Files:**
- Modify: `zenagent/agent/session.py` (`SessionInfo`, `read_info`), `zenagent/app/sessions.py` (`SessionEntry`, `entries`), `zenagent/cli/tui/screens/modals.py`, `zenagent/cli/rpc/` (`_session_op` list)
- Test: `tests/test_session.py`, `tests/test_app_sessions.py`, `tests/test_tui_sessions.py`, `tests/test_project_scoping.py`, `tests/test_rpc_v2.py`

**Interfaces:**
- Consumes: `Session.header`/`records` (existing).
- Produces: `SessionInfo.created_at: float`, `model: str`, `derived_from: str`; `SessionEntry.updated_at` (renamed from `created`) plus `created_at`, `model`, `derived_from`; `session list` entries add `created_at`, `updated_at`, `model`, `derived_from`.

- [ ] **Step 1: Write the failing tests**

`tests/test_session.py`:

```python
def test_read_info_uses_header_creation_model_and_lineage(isolated_sessions):
    session = Session(isolated_sessions / "s.jsonl", derived_from="parent")
    session.append(user("hi"))
    session.append(assistant("hello"))

    info = session.read_info(titles={})

    assert info.created_at == session.header["createdAt"]
    assert info.model == "m"
    assert info.derived_from == "parent"
    assert info.mtime == session.path.stat().st_mtime


def test_read_info_falls_back_to_mtime_without_a_header(isolated_sessions):
    path = isolated_sessions / "legacy.jsonl"
    path.write_text(json.dumps(user("hi").to_json()) + "\n", encoding="utf-8")

    info = Session(path).read_info(titles={})

    assert info.created_at == info.mtime
    assert info.model == ""
    assert info.derived_from == ""
```

`tests/test_app_sessions.py`:

```python
async def test_entries_expose_creation_update_model_and_lineage(session_runtime):
    runtime = session_runtime
    manager = SessionManager(runtime)
    runtime.session.append(UserMessage(content="hello", timestamp=1))
    runtime.session.append(
        AssistantMessage(
            content=[TextContent(text="hi")],
            provider="p",
            model="deepseek-chat",
            usage=Usage(),
            stop_reason="stop",
            timestamp=2,
        )
    )
    entry = next(item for item in manager.entries() if item.path == manager.current())

    assert entry.created_at > 0
    assert entry.updated_at == entry.path.stat().st_mtime
    assert entry.model == "deepseek-chat"
    assert entry.derived_from == ""

    forked = await manager.fork()
    forked_entry = next(item for item in manager.entries() if item.path == forked)
    assert forked_entry.derived_from == entry.path.stem
```

`tests/test_tui_sessions.py` and `tests/test_project_scoping.py`: change the `SessionEntry(..., created=1.0, ...)` constructor keyword to `updated_at=1.0`.

`tests/test_rpc_v2.py`:

```python
async def test_session_list_carries_created_updated_model_and_lineage(runtime_factory):
    runtime, server, writes, queue = await build(runtime_factory)
    runtime.session.append(UserMessage(content="hello", timestamp=1))
    runtime.session.append(
        AssistantMessage(
            content=[TextContent(text="hi")],
            provider="p",
            model="deepseek-chat",
            usage=Usage(),
            stop_reason="stop",
            timestamp=2,
        )
    )

    await run_lines(server, queue, [{"type": "session", "id": "l", "op": "list"}])

    entry = decode(writes)[0]["sessions"][0]
    assert entry["created_at"] > 0
    assert entry["updated_at"] == Path(entry["path"]).stat().st_mtime
    assert entry["model"] == "deepseek-chat"
    assert entry["derived_from"] == ""
```

Add `from pathlib import Path` to `tests/test_rpc_v2.py` if it is not already imported.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_session.py tests/test_app_sessions.py tests/test_tui_sessions.py tests/test_project_scoping.py tests/test_rpc_v2.py::test_session_list_carries_created_updated_model_and_lineage -q`

Expected: FAIL — `SessionInfo` has no `created_at`/`model`/`derived_from`, `SessionEntry` has no `updated_at`.

- [ ] **Step 3: Implement the list fields**

`zenagent/agent/session.py`:

```python
@dataclass
class SessionInfo:
    path: Path
    mtime: float
    messages: int
    title: str
    project: str = ""
    #: Header ``createdAt``, or mtime for legacy files without a header.
    created_at: float = 0.0
    #: Provider model id of the last assistant message; empty when none.
    model: str = ""
    #: Header ``derivedFrom``; empty for an original session.
    derived_from: str = ""
```

`read_info` (import `AssistantMessage`):

```python
        messages = self.load()
        ...
        mtime = self.path.stat().st_mtime
        created_at = mtime
        if self.header is not None:
            raw = self.header.get("createdAt")
            if isinstance(raw, (int, float)):
                created_at = float(raw)
        model = next(
            (
                message.model
                for message in reversed(messages)
                if isinstance(message, AssistantMessage)
            ),
            "",
        )
        derived_from = ""
        if self.header is not None:
            raw = self.header.get("derivedFrom")
            if isinstance(raw, str):
                derived_from = raw
        return SessionInfo(
            path=self.path,
            mtime=mtime,
            messages=len(messages),
            title=overrides.get(self.path.name) or _title_from_messages(messages),
            created_at=created_at,
            model=model,
            derived_from=derived_from,
        )
```

`zenagent/app/sessions.py`:

```python
@dataclass
class SessionEntry:
    path: Path
    updated_at: float
    messages: int
    size: int
    created_at: float = 0.0
    model: str = ""
    derived_from: str = ""
    current: bool = False
    title: str = ""
    project: str = ""
    pinned: bool = False
```

`entries()` maps `updated_at=info.mtime`, `created_at=info.created_at`, `model=info.model`, `derived_from=info.derived_from`.

`zenagent/cli/tui/screens/modals.py:123`: `time.localtime(entry.updated_at)`.

`zenagent/cli/rpc/` list entries add:

```python
                        "created_at": entry.created_at,
                        "updated_at": entry.updated_at,
                        "model": entry.model,
                        "derived_from": entry.derived_from,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_session.py tests/test_app_sessions.py tests/test_tui_sessions.py tests/test_project_scoping.py tests/test_rpc_v2.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/agent/session.py zenagent/app/sessions.py zenagent/cli/tui/screens/modals.py zenagent/cli/rpc/ tests/test_session.py tests/test_app_sessions.py tests/test_tui_sessions.py tests/test_project_scoping.py tests/test_rpc_v2.py
git commit -m "feat(session): list creation time, last model and lineage"
```

---

### Task 6: Document the contract

**Files:**
- Modify: `docs/guide/sessions.md` (side-channel-records section), `CHANGELOG.md` (`Unreleased`)

**Interfaces:**
- Consumes: every task above.
- Produces: user-facing documentation and a changelog entry; no code.

- [ ] **Step 1: Update `docs/guide/sessions.md`**

In the existing "旁路记录（side-channel records）" list, add:

```markdown
- `tool_end`：每次工具调用结束写一条——`call_id`、`tool_name`、`duration_ms`、
  `is_error`、`details`（`details` 序列化上限 16 KiB；`diff` 先按 400 行截断，
  仍超限则先丢 `diff`，再逐个丢最大字段，并置 `diff_truncated`/`details_truncated`）。
  重写/分叉/撤销时只携带“保留消息里仍有该 `call_id`”的记录；`/shake tools`
  不携带（它本就是要丢掉这些负载）。
- `compaction`：每次 `/compact`（含自动压缩）在新文件、摘要之前写一条——
  `tokensBefore`、`derivedFrom`、`reason`（`auto` | `manual`）。RPC `transcript`
  结果里的 `compactions` 数组按文件顺序返回，GUI 用它画一条且仅一条分隔线；
  带 `source="compaction"` 的 notice 不出现在快照里。
- steering：通过 steer 通道送达的用户消息带 `"steering": true`（普通消息不写这个键）；
  多条 steer 会聚合成一条消息，标记按“送达的消息”计。
- RPC 视图 id（`v1`、`v2`…）是加载时按顺序生成的，不写入文件；同一文件重复加载
  得到同样的 id。`messages_to_views`/live builder 对无文本、无 thinking 的 assistant
  行直接丢弃；没有结果的工具调用显示为 `interrupted`。
```

Also extend the session-list paragraph: list entries expose `created_at`（header）,
`updated_at`（mtime）, `model`（last assistant）, `derived_from`.

- [ ] **Step 2: Update `CHANGELOG.md`**

Under `## Unreleased` → `### Added`, add one entry:

```markdown
- **Session files reconstruct the GUI, not just the conversation.** View snapshots
  now carry stable `v{n}` ids and the assistant's stored timestamp/model/provider/
  usage/duration; `tool_end` records keep tool durations and bounded details
  (including diffs) across resume and rewrite/fork/undo; `compaction` records
  expose a single divider through `transcript.compactions`; user messages
  delivered by steering carry `"steering": true`; and `session list` reports
  `created_at`, `updated_at`, `model` and `derived_from`.
```

- [ ] **Step 3: Verify**

Run: `.venv/bin/python -m pytest tests/test_session.py tests/test_app_views.py tests/test_app_session_meta.py tests/test_app_chat.py tests/test_app_sessions.py tests/test_compact.py tests/test_rpc_v2.py tests/test_i18n_tui.py -q`

Expected: PASS.

Run: `.venv/bin/ruff check zenagent tests`

Expected: no findings.

- [ ] **Step 4: Commit**

```bash
git add docs/guide/sessions.md CHANGELOG.md
git commit -m "docs: session-to-GUI contract for view ids, tool_end and compaction"
```
