# Memory Manager UI (/memory) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/memory` becomes a manager for the project memory library: a TUI modal panel with full CRUD, and equivalent text forms in the REPL (index / full text / rm / `$EDITOR` add+edit).

**Architecture:** The shared command layer (`app/commands.py`) parses `/memory` arguments and either returns store-backed messages or a `screen` intent. The TUI maps those screen names to new Textual modals (`MemoryManager`, `MemoryEditor`) that write via `MemoryStore` with `source="ui"`; the REPL renders the same screen names as text, handing add/edit to `$VISUAL`/`$EDITOR`. All writes go through `MemoryStore`, so `journal.jsonl` stays complete.

**Tech Stack:** Python stdlib (agent kernel), Textual v8 (TUI modals, existing `DialogScreen`/`Confirm`/`TodoEditor` patterns), pytest + Textual pilot.

**Spec:** `docs/superpowers/specs/2026-11-03-memory-manager-ui-design.md`

## Global Constraints

- Every write goes through `MemoryStore`; UI-created/edited entries use `source="ui"` (the model uses `"agent"`, `tools/memory.py:126`). No direct file unlinking, ever (spec anti-goal).
- Two frontends, one surface: every new screen name gets a REPL text form (`cli/repl.py` "every screen gets a text form" convention).
- All new user-facing strings go into **both** `zenagent/i18n/en.py` and `zenagent/i18n/zh.py`, `t("key", **kwargs)` / `{placeholder}` style.
- `zenagent/agent/` is the stdlib-only kernel: `memory_store.py` must not gain dependencies.
- TUI code follows the existing `DialogScreen` chrome and key-binding idioms (`TodoEditor`, `Confirm` at `cli/tui/screens/modals.py`); no new widget libraries.
- Out of scope (spec non-goals): model-driven "organize" trigger, in-panel search, global `~/memory/`, automatic memory summarization.
- Run tests with `uv run pytest`.

## Review Focus

1. `$VISUAL`/`$EDITOR` unset during `/memory add` → the REPL prints a clear message and writes nothing (test in Task 3).
2. Edited text without a leading `title:` line → the whole text is the body; store coerces the title to `(untitled)` — never a crash (test in Task 1).
3. `/memory edit <bad-id>` (malformed or missing) → a message, never a screen (test in Task 2).
4. `/memory add 标题 带空格` → the full remainder is the title prefill, not just the first token (test in Task 2).
5. A note written by the model while the panel is open → the panel is allowed to show a stale list until the next action, but every action must re-read from disk (no cached entry text; test in Task 4).

---

### Task 1: `split_title_body` parser in the memory kernel

**Files:**
- Modify: `zenagent/agent/memory_store.py` (module-level helper + `__all__`)
- Test: `tests/test_memory_store.py`

**Interfaces:**
- Consumes: `_parse_frontmatter(text)` (exists, `memory_store.py:88`).
- Produces: `split_title_body(text: str) -> tuple[str, str]` — `("title", "body")`; Tasks 3 uses it to parse `$EDITOR` output.

- [ ] **Step 1: Write the failing tests** in `tests/test_memory_store.py`

```python
def test_split_title_body_separates_title_and_body():
    assert split_title_body("title: 选型\n\n正文第一行\n第二行") == ("选型", "正文第一行\n第二行")

def test_split_title_body_without_title_line_returns_whole_text():
    assert split_title_body("没有标题行\n\n正文") == ("", "没有标题行\n\n正文")

def test_split_title_body_title_only_yields_empty_body():
    assert split_title_body("title: 只有标题") == ("只有标题", "")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -q tests/test_memory_store.py -k split_title_body`
Expected: FAIL (`ImportError: cannot import name 'split_title_body'`)

- [ ] **Step 3: Implement in `zenagent/agent/memory_store.py`**

Module-level function next to `_parse_frontmatter`, exported in `__all__`:

```python
def split_title_body(text: str) -> tuple[str, str]:
    """`title: X\\n\\nbody` → ("X", "body"); no leading title line → ("", text)."""
    fields, body = _parse_frontmatter(text)
    return fields.get("title", ""), body
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest -q tests/test_memory_store.py`
Expected: PASS (all, including existing tests)

- [ ] **Step 5: Commit**

```bash
git add zenagent/agent/memory_store.py tests/test_memory_store.py
git commit -m "feat(memory): split_title_body helper for editor handoff"
```

---

### Task 2: `/memory` command layer + i18n copy + completion entry

**Files:**
- Modify: `zenagent/app/commands.py` (`CommandResult.target` field, `_memory` handler, register in `build_default_commands`)
- Modify: `zenagent/cli/completion.py` (`SLASH_COMMANDS`, near the `/todo` line)
- Modify: `zenagent/i18n/en.py`, `zenagent/i18n/zh.py`
- Test: `tests/test_app_commands.py`

**Interfaces:**
- Consumes: `MemoryStore.read_entry/delete_entry/list_entries` (exist); `services.runtime.memory_store` (always set, `app/runtime.py:560`).
- Produces:
  - `CommandResult.target: str | None` — screen payload: `"memory-edit"` carries the entry id, `"memory-add"` the title prefill.
  - Screens: bare → `screen="memory-manager"`; `add` → `screen="memory-add"`; `edit <id>` → `screen="memory-edit"`. Tasks 3/4 dispatch on these names.
  - i18n keys `cmd.repl.memory`, `cmdmsg.memory_*` (exact copy below).

- [ ] **Step 1: Write the failing tests** in `tests/test_app_commands.py` (add a helper next to `services_with_answer`)

```python
async def memory_services(runtime_factory, tmp_path):
    """AppServices whose memory store points at an empty tmp library."""
    from zenagent.agent.memory_store import MemoryStore
    from zenagent.app.chat import ChatController
    from zenagent.app.commands import AppServices
    from zenagent.app.models import ModelManager
    from zenagent.app.sessions import SessionManager

    runtime = await runtime_factory()
    runtime.memory_store = MemoryStore(tmp_path)
    return AppServices(runtime=runtime, chat=ChatController(runtime),
                       models=ModelManager(runtime), sessions=SessionManager(runtime))


async def test_memory_bare_returns_manager_screen(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    result = await build_default_commands(services).dispatch("memory", "")
    assert result.screen == "memory-manager"


async def test_memory_add_keeps_spaced_title_prefill(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    result = await build_default_commands(services).dispatch("memory", "add 选型 原则")
    assert result.screen == "memory-add"
    assert result.target == "选型 原则"


async def test_memory_edit_missing_entry_is_a_message_not_a_screen(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    result = await build_default_commands(services).dispatch("memory", "edit beef")
    assert result.screen is None
    assert "beef" in result.message


async def test_memory_edit_existing_returns_screen_with_id(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    entry = services.runtime.memory_store.write_entry(title="T", body="B", source="test")
    result = await build_default_commands(services).dispatch("memory", f"edit {entry.id}")
    assert result.screen == "memory-edit"
    assert result.target == entry.id


async def test_memory_rm_deletes_and_reports_journal(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    entry = services.runtime.memory_store.write_entry(title="T", body="B", source="test")
    result = await build_default_commands(services).dispatch("memory", f"rm {entry.id}")
    assert "deleted" in result.message
    assert services.runtime.memory_store.read_entry(entry.id) is None


async def test_memory_id_shows_full_entry(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    entry = services.runtime.memory_store.write_entry(title="标题", body="正文", source="test")
    result = await build_default_commands(services).dispatch("memory", entry.id)
    assert "标题" in result.message and "正文" in result.message


async def test_memory_unknown_argument_shows_usage(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    result = await build_default_commands(services).dispatch("memory", "nonsense")
    assert "usage" in result.message
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -q tests/test_app_commands.py -k memory`
Expected: FAIL (`unknown command` path — `dispatch("memory", ...)` returns a message, not the screens)

- [ ] **Step 3: Implement**

1. `CommandResult` gains a field after `model`:

```python
    #: Screen payload: "memory-edit" carries the entry id, "memory-add" the title prefill.
    target: str | None = None
```

2. Handler near `_todo` (commands.py:529), registered in the `build_default_commands` factory list next to `_todo`:

```python
@_command("memory")
async def _memory(services: AppServices, argument: str) -> CommandResult:
    """Manage project memory notes: panel in the TUI, text forms in the REPL."""
    store = services.runtime.memory_store
    argument = argument.strip()
    parts = argument.split()
    if not parts:
        return CommandResult(screen="memory-manager")
    head, rest = parts[0], parts[1:]
    if head == "add":
        return CommandResult(screen="memory-add", target=argument.partition("add")[2].strip())
    if head == "edit" and rest:
        entry = store.read_entry(rest[0]) if store else None
        if entry is None:
            return CommandResult(message=t("cmdmsg.memory_not_found", id=rest[0]))
        return CommandResult(screen="memory-edit", target=entry.id)
    if head == "rm" and rest:
        if store and store.delete_entry(rest[0], reason="ui delete (/memory rm)"):
            return CommandResult(message=t("cmdmsg.memory_deleted", id=rest[0]))
        return CommandResult(message=t("cmdmsg.memory_not_found", id=rest[0]))
    entry = store.read_entry(head) if store else None
    if entry is None:
        return CommandResult(message=t("cmdmsg.memory_usage"))
    header = t("cmdmsg.memory_header", id=entry.id, title=entry.title,
               updated=entry.updated, source=entry.source)
    return CommandResult(message=f"{header}\n\n{entry.body}")
```

3. `SLASH_COMMANDS` in `cli/completion.py`, next to `/todo`: `"/memory": "cmd.memory.description",` — and add that key to the table's i18n (it is what `/help` renders).

4. i18n — exact copy, same keys in both files:

`zenagent/i18n/en.py`:
```python
"cmd.memory.description": "browse and edit project memory notes",
"cmdmsg.memory_empty": "no memory notes yet — /memory add creates one",
"cmdmsg.memory_usage": "usage: /memory | /memory <id> | /memory add [title] | /memory edit <id> | /memory rm <id>",
"cmdmsg.memory_not_found": "no memory entry {id}",
"cmdmsg.memory_deleted": "deleted {id} ({title}) — recorded in journal",
"cmdmsg.memory_saved": "saved memory {id} ({title})",
"cmdmsg.memory_header": "{id} · {title} · updated {updated} · source {source}",
"repl.no_editor": "$VISUAL/$EDITOR is not set; cannot open the editor",
"tui.dialog.memory_manager": "project memory ({count})",
"tui.dialog.memory_hint": "a add · e edit · x delete · esc close",
"tui.dialog.memory_editor": "memory entry",
"tui.dialog.memory_editor_hint": "ctrl+s save · esc cancel",
"tui.dialog.memory_delete_confirm": "delete {id} ({title})?",
"tui.app.memory_save_failed": "cannot save memory note: {error}",
```

`zenagent/i18n/zh.py`:
```python
"cmd.memory.description": "查看与编辑项目记忆库",
"cmdmsg.memory_empty": "记忆库为空 — 用 /memory add 创建第一条",
"cmdmsg.memory_usage": "用法：/memory | /memory <id> | /memory add [标题] | /memory edit <id> | /memory rm <id>",
"cmdmsg.memory_not_found": "没有 id 为 {id} 的记忆条目",
"cmdmsg.memory_deleted": "已删除 {id}（{title}）— journal 已记录",
"cmdmsg.memory_saved": "已保存记忆 {id}（{title}）",
"cmdmsg.memory_header": "{id} · {title} · 更新于 {updated} · 来源 {source}",
"repl.no_editor": "未设置 $VISUAL/$EDITOR，无法打开编辑器",
"tui.dialog.memory_manager": "项目记忆库（{count}）",
"tui.dialog.memory_hint": "a 新增 · e 编辑 · x 删除 · esc 关闭",
"tui.dialog.memory_editor": "记忆条目",
"tui.dialog.memory_editor_hint": "ctrl+s 保存 · esc 取消",
"tui.dialog.memory_delete_confirm": "删除 {id}（{title}）？",
"tui.app.memory_save_failed": "记忆条目保存失败：{error}",
```

(`cmdmsg.memory_saved`, `repl.no_editor` and the `tui.*` keys land now but are consumed by Tasks 3–4; adding them here keeps one i18n edit.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest -q tests/test_app_commands.py tests/test_completion.py tests/test_i18n.py tests/test_i18n_app.py`
Expected: PASS (completion/help render from the same table, so the new entry needs no extra test)

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/commands.py zenagent/cli/completion.py zenagent/i18n/en.py zenagent/i18n/zh.py tests/test_app_commands.py
git commit -m "feat(commands): /memory command layer with screens, text args and i18n"
```

---

### Task 3: REPL text forms + `$EDITOR` handoff

**Files:**
- Create: `zenagent/cli/editor.py`
- Modify: `zenagent/cli/repl.py` (two render helpers + three screen branches in `render_command_result`, next to the `"todo-edit"` branch at repl.py:208)
- Test: `tests/test_repl_commands.py`

**Interfaces:**
- Consumes: `split_title_body` (Task 1), screen names + `target` (Task 2), `cmdmsg.memory_*` / `repl.no_editor` keys (Task 2).
- Produces: `run_editor(initial: str) -> str | None` in `zenagent/cli/editor.py` — `$VISUAL`/`$EDITOR` subprocess handoff; `None` means "no editor configured or editor failed".

- [ ] **Step 1: Write the failing tests** in `tests/test_repl_commands.py` (reuse the existing `_render(services, result, lines)` helper and the `memory_services` pattern — import it from `tests.test_app_commands` or duplicate the 10-line helper, whichever fits the file's local style)

```python
async def test_memory_manager_screen_renders_index_lines(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    entry = services.runtime.memory_store.write_entry(title="选型", body="B", source="test")
    lines: list[str] = []
    _render(services, CommandResult(screen="memory-manager"), lines)
    assert any(entry.id in line and "选型" in line for line in lines)


async def test_memory_manager_screen_empty_library_prints_empty_message(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    lines: list[str] = []
    _render(services, CommandResult(screen="memory-manager"), lines)
    assert any("no memory notes" in line for line in lines)


async def test_memory_add_screen_runs_editor_and_saves_ui_entry(runtime_factory, tmp_path, monkeypatch):
    services = await memory_services(runtime_factory, tmp_path)
    monkeypatch.setattr(repl_module, "run_editor",
                        lambda initial: "title: 新条目\n\n正文内容")
    lines: list[str] = []
    _render(services, CommandResult(screen="memory-add", target="新条目"), lines)
    saved = services.runtime.memory_store.list_entries()
    assert len(saved) == 1 and saved[0].title == "新条目" and saved[0].source == "ui"
    assert saved[0].body == "正文内容"


async def test_memory_add_screen_without_editor_writes_nothing(runtime_factory, tmp_path, monkeypatch):
    services = await memory_services(runtime_factory, tmp_path)
    monkeypatch.setattr(repl_module, "run_editor", lambda initial: None)
    lines: list[str] = []
    _render(services, CommandResult(screen="memory-add", target=""), lines)
    assert services.runtime.memory_store.list_entries() == []
    assert any("$VISUAL" in line for line in lines)


async def test_memory_edit_screen_updates_existing_entry(runtime_factory, tmp_path, monkeypatch):
    services = await memory_services(runtime_factory, tmp_path)
    entry = services.runtime.memory_store.write_entry(title="旧", body="旧正文", source="agent")
    monkeypatch.setattr(repl_module, "run_editor",
                        lambda initial: f"title: 新标题\n\n新正文")
    lines: list[str] = []
    _render(services, CommandResult(screen="memory-edit", target=entry.id), lines)
    updated = services.runtime.memory_store.read_entry(entry.id)
    assert updated.title == "新标题" and updated.body == "新正文"
    assert updated.source == "agent"  # update keeps the original source


def test_run_editor_roundtrip_through_env_editor(tmp_path, monkeypatch):
    script = tmp_path / "fake-editor.sh"
    script.write_text('#!/bin/sh\nprintf "appended" >> "$1"\n')
    script.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(script))
    monkeypatch.delenv("VISUAL", raising=False)
    from zenagent.cli.editor import run_editor
    assert run_editor("title: t\n\n").endswith("appended")


def test_run_editor_without_env_returns_none(monkeypatch):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    from zenagent.cli.editor import run_editor
    assert run_editor("x") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -q tests/test_repl_commands.py -k memory`
Expected: FAIL (`memory-manager` falls through `render_command_result`'s screen chain; `repl_module.run_editor` attribute missing)

- [ ] **Step 3: Implement**

1. `zenagent/cli/editor.py`:

```python
"""``$VISUAL``/``$EDITOR`` handoff for line-based frontends (the REPL).

The TUI has its own suspend-based editor (``app.py``) because a fullscreen
app must suspend first; the REPL has no screen to suspend, so a plain
subprocess between prompts is enough. Split out so tests can drive it
without a terminal.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

__all__ = ["run_editor"]


def run_editor(initial: str) -> str | None:
    """Open ``initial`` in the user's editor; return the edited text or None."""
    raw = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not raw or not raw.strip():
        return None
    try:
        command = shlex.split(raw)
    except ValueError:
        return None
    if not command:
        return None
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(initial)
        name = handle.name
    path = Path(name)
    try:
        if subprocess.call([*command, str(path)]) != 0:
            return None
        return path.read_text(encoding="utf-8")
    finally:
        path.unlink(missing_ok=True)
```

2. `cli/repl.py` — import `run_editor` and `split_title_body`; two helpers next to `_render_todo` (repl.py:118):

```python
def _render_memory_index(services, print_fn: PrintFn) -> None:
    store = services.runtime.memory_store
    entries = store.list_entries() if store else []
    if not entries:
        print_fn(t("cmdmsg.memory_empty"))
        return
    for entry in entries:
        print_fn(f"{entry.id}  {entry.title}  {entry.updated}")


def _memory_editor_flow(services, entry_id: str | None, title_prefill: str, print_fn: PrintFn) -> None:
    store = services.runtime.memory_store
    entry = store.read_entry(entry_id) if (store and entry_id) else None
    if entry_id and entry is None:
        print_fn(t("cmdmsg.memory_not_found", id=entry_id))
        return
    initial = f"title: {entry.title if entry else title_prefill}\n\n{entry.body if entry else ''}"
    text = run_editor(initial)
    if text is None:
        print_fn(t("repl.no_editor"))
        return
    title, body = split_title_body(text)
    saved = (
        store.update_entry(entry.id, title=title, body=body, reason="ui edit")
        if entry
        else store.write_entry(title=title, body=body, source="ui", reason="ui edit")
    )
    print_fn(t("cmdmsg.memory_saved", id=saved.id, title=saved.title))
```

3. Branches in `render_command_result`, right after the `"todo-edit"` branch:

```python
    elif screen == "memory-manager":
        _render_memory_index(services, print_fn)
    elif screen == "memory-add":
        _memory_editor_flow(services, None, result.target or "", print_fn)
    elif screen == "memory-edit":
        _memory_editor_flow(services, result.target, "", print_fn)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest -q tests/test_repl_commands.py tests/test_memory_store.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add zenagent/cli/editor.py zenagent/cli/repl.py tests/test_repl_commands.py
git commit -m "feat(repl): /memory text forms with $EDITOR add/edit handoff"
```

---

### Task 4: TUI `MemoryManager` + `MemoryEditor` screens

**Files:**
- Create: `zenagent/cli/tui/screens/memory_manager.py`
- Modify: `zenagent/cli/tui/app.py` (three dispatch branches next to `"todo-edit"` at app.py:1602; `open_memory_manager` / `open_memory_editor` methods next to `open_todo_editor` at app.py:1326)
- Test: `tests/test_tui_memory.py` (new; follow `tests/test_tui_todo.py`'s `ZenAgentApp(fake_runtime)` + pilot style)

**Interfaces:**
- Consumes: `MemoryStore` (read/write via `self.runtime.memory_store`), `DialogScreen`/`Confirm` (`modals.py`), `open_menu(kind, factory, callback)` (app.py:949), i18n keys from Task 2.
- Produces:
  - `MemoryEditor(store: MemoryStore, entry: MemoryEntry | None, *, title_prefill: str = "")` — modal; on save writes via `store` and dismisses with the saved `MemoryEntry`; on cancel dismisses `None`.
  - `MemoryManager(store: MemoryStore)` — modal list; `a` add / `e` edit / `x` delete (Confirm) / `esc` close.
  - `ZenAgentApp.open_memory_manager()`, `ZenAgentApp.open_memory_editor(entry_id=None, *, title_prefill="")`.

- [ ] **Step 1: Write the failing tests** in `tests/test_tui_memory.py`

```python
"""Memory manager/editor modals: list, add/edit via MemoryEditor, delete via Confirm."""

from textual.widgets import Input, OptionList, TextArea

from zenagent.agent.memory_store import MemoryStore
from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.screens.memory_manager import MemoryEditor, MemoryManager


def seeded_store(tmp_path, count=2):
    store = MemoryStore(tmp_path)
    for index in range(count):
        store.write_entry(title=f"条目{index}", body=f"正文{index}", source="test")
    return store


async def test_dispatch_memory_opens_manager_listing_entries(fake_runtime, tmp_path):
    app = ZenAgentApp(fake_runtime)
    app.runtime.memory_store = seeded_store(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("memory", "")
        await pilot.pause()
        manager = app.query_one(MemoryManager)
        options = list(manager.query_one("#memory-list", OptionList).options)
        assert len(options) == 2
        ids = {entry.id for entry in app.runtime.memory_store.list_entries()}
        assert all(any(entry_id in str(option.prompt) for entry_id in ids) for option in options)


async def test_editor_saves_new_entry_with_ui_source(fake_runtime, tmp_path):
    app = ZenAgentApp(fake_runtime)
    store = MemoryStore(tmp_path)
    app.runtime.memory_store = store
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_memory_editor(title_prefill="标题X")
        await pilot.pause()
        app.query_one("#memory-title", Input).value = "标题X"
        app.query_one("#memory-body", TextArea).text = "正文Y"
        await pilot.press("ctrl+s")
        await pilot.pause()
        saved = store.list_entries()
        assert len(saved) == 1
        assert (saved[0].title, saved[0].body, saved[0].source) == ("标题X", "正文Y", "ui")


async def test_editor_escape_cancels_without_writing(fake_runtime, tmp_path):
    app = ZenAgentApp(fake_runtime)
    store = MemoryStore(tmp_path)
    app.runtime.memory_store = store
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_memory_editor(title_prefill="X")
        await pilot.pause()
        app.query_one("#memory-title", Input).value = "不该保存"
        await pilot.press("escape")
        await pilot.pause()
        assert store.list_entries() == []


async def test_manager_delete_confirms_then_removes(fake_runtime, tmp_path):
    app = ZenAgentApp(fake_runtime)
    store = seeded_store(tmp_path, count=1)
    app.runtime.memory_store = store
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_memory_manager()
        await pilot.pause()
        entry = store.list_entries()[0]
        await pilot.press("x")          # Confirm pops up
        await pilot.pause()
        await pilot.press("y")          # confirm
        await pilot.pause()
        assert store.read_entry(entry.id) is None
        assert list(app.query_one(MemoryManager).query_one("#memory-list", OptionList).options) == []


async def test_manager_refresh_reflects_external_writes(fake_runtime, tmp_path):
    app = ZenAgentApp(fake_runtime)
    store = MemoryStore(tmp_path)
    app.runtime.memory_store = store
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_memory_manager()
        await pilot.pause()
        store.write_entry(title="外部写入", body="B", source="agent")  # model wrote underneath
        app.query_one(MemoryManager)._refresh()
        await pilot.pause()
        options = list(app.query_one(MemoryManager).query_one("#memory-list", OptionList).options)
        assert len(options) == 1 and "外部写入" in str(options[0].prompt)
```

(The assertion values and widget ids above are what's pinned; the test bodies may be adjusted to the Textual API details discovered while implementing.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -q tests/test_tui_memory.py`
Expected: FAIL (`ModuleNotFoundError: zenagent.cli.tui.screens.memory_manager`)

- [ ] **Step 3: Implement `zenagent/cli/tui/screens/memory_manager.py`**

Follow `TodoEditor`'s structure (`modals.py:887`): `DialogScreen` subclass, `#dialog`/`#dialog-title`/`#dialog-body`/`#dialog-hint` chrome, `t()` for all copy.

```python
class MemoryEditor(DialogScreen["MemoryEntry | None"]):
    """Title+body editor; the write goes through the store on save."""

    BINDINGS = [("ctrl+s", "save", "Save"), ("escape", "cancel", "Cancel")]

    def __init__(self, store, entry=None, *, title_prefill: str = "") -> None: ...
    # compose: #dialog-title t("tui.dialog.memory_editor");
    #   Input(value=entry.title or title_prefill, id="memory-title");
    #   TextArea(text=entry.body or "", id="memory-body");
    #   #dialog-hint t("tui.dialog.memory_editor_hint")
    # action_save: title = Input.value, body = TextArea.text;
    #   try: saved = store.update_entry(entry.id, title=..., body=..., reason="ui edit")
    #        if entry else store.write_entry(title=..., body=..., source="ui", reason="ui edit")
    #        self.dismiss(saved)
    #   except OSError as exc: self.notify(t("tui.app.memory_save_failed", error=exc), timeout=3)
    # action_cancel: self.dismiss(None)


class MemoryManager(DialogScreen[None]):
    """Project memory panel: list + a/e/x; every action re-reads the store."""

    BINDINGS = [("a", "add", "Add"), ("e", "edit", "Edit"),
                ("x", "delete", "Delete"), ("escape", "close", "Close")]

    def __init__(self, store) -> None: ...   # keep store; entries list for highlight mapping
    # compose: #dialog-title t("tui.dialog.memory_manager", count=len(store.list_entries()));
    #   OptionList(id="memory-list"); #dialog-hint t("tui.dialog.memory_hint")
    # on_mount: self._refresh(); focus "#memory-list"
    # _refresh(): clear options; rows = store.list_entries();
    #   option prompt f"{entry.id}  {entry.title}  {entry.updated}"; empty → single
    #   disabled Option with t("cmdmsg.memory_empty")
    # _selected() -> MemoryEntry | None  (highlight index → self._entries)
    # action_add: self.app.push_screen(MemoryEditor(self.store), self._saved)
    # action_edit: entry = self._selected(); if entry: push MemoryEditor(self.store, entry, self._saved)
    # _saved(self, saved): if saved is not None: self._refresh()
    # action_delete: entry = self._selected(); if entry:
    #   self.app.push_screen(Confirm(t("tui.dialog.memory_delete_confirm", id=entry.id, title=entry.title)),
    #                        lambda ok: ok and self._delete(entry))
    # _delete(self, entry): store.delete_entry(entry.id, reason="ui delete"); self._refresh()
    # action_close: self.dismiss(None)
```

(Comments describe behavior; bodies are the implementer's, but the class/signature/binding/id set above is pinned — tests query `#memory-title`, `#memory-body`, `#memory-list` and press `ctrl+s`/`escape`/`x`/`y`.)

- [ ] **Step 4: Implement app wiring in `zenagent/cli/tui/app.py`**

Next to `open_todo_editor` (app.py:1326):

```python
def open_memory_manager(self) -> None:
    from zenagent.cli.tui.screens.memory_manager import MemoryManager

    store = self.runtime.memory_store
    if store is None:
        return
    self.open_menu("memory-manager", lambda: MemoryManager(store), lambda _result: None)

def open_memory_editor(self, entry_id: str | None = None, *, title_prefill: str = "") -> None:
    from zenagent.cli.tui.screens.memory_manager import MemoryEditor

    store = self.runtime.memory_store
    if store is None:
        return
    entry = store.read_entry(entry_id) if entry_id else None

    def saved(result) -> None:
        if result is not None:
            self.notify(t("cmdmsg.memory_saved", id=result.id, title=result.title), timeout=3)

    self.open_menu(
        "memory-editor",
        lambda: MemoryEditor(store, entry, title_prefill=title_prefill),
        saved,
    )
```

Dispatch branches in `_dispatch_command`, next to the `"todo-edit"` branch (app.py:1602):

```python
        elif result.screen == "memory-manager":
            self.open_memory_manager()
        elif result.screen == "memory-add":
            self.open_memory_editor(title_prefill=result.target or "")
        elif result.screen == "memory-edit":
            self.open_memory_editor(result.target)
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest -q tests/test_tui_memory.py tests/test_tui_todo.py`
Expected: PASS (`test_tui_todo` guards the shared modal chrome against regressions)

- [ ] **Step 6: Commit**

```bash
git add zenagent/cli/tui/screens/memory_manager.py zenagent/cli/tui/app.py tests/test_tui_memory.py
git commit -m "feat(tui): memory manager panel and entry editor modals"
```

---

### Task 5: Docs + full suite

**Files:**
- Modify: `README.md:97` (`**47 commands**` → `**48 commands**`)
- Modify: `docs/feature-map.md:97` (append `/memory` to the slash-command list)
- Modify: `docs/guide/commands.md` (add `| \`/memory\` | 查看与编辑项目记忆库（TUI 面板 / REPL 文本） |` next to the `/todo` row under 诊断与工具)

**Interfaces:**
- Consumes: everything above is already merged.

- [ ] **Step 1: Make the three doc edits** (exact locations above)

- [ ] **Step 2: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS (0 failed)

- [ ] **Step 3: Commit**

```bash
git add README.md docs/feature-map.md docs/guide/commands.md
git commit -m "docs: /memory manager command in README, feature map and command guide"
```
