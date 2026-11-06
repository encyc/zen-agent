# 架构审查批次 1：契约缺陷与分层门禁 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修掉三个会产出错误行为的契约缺陷（两个适配器把被截断的工具调用标成 `tool_use`、子 agent 绕过权限门），并把 `app → cli` 的反向依赖清掉、加一个钉住分层规则的门禁测试。

**Architecture:** 四个互不依赖的小改动。两个适配器只改终局 `stop_reason` 的推导（一行判断 + 一个 fixture）；`task` 工具把父进程的 `before_tool_call` 透传给子 `Agent`；`clipboard` 是纯 stdlib 的 UI 无关代码，从 `cli/tui/` 搬到 `app/`，然后用一个 AST 扫描测试把"`app/` 不 import `cli/`、不 import UI 库"和"层序只向下"钉住。

**Tech Stack:** Python 3.11+、pytest/pytest-asyncio、ruff。无新依赖。

**Spec:** `docs/reviews/2026-11-06-architecture-review.md`（C1、C2、C5 与 I2 的补充证据）

## Global Constraints

- 契约（`docs/reference/contracts.md`）：`StreamFn` 永不抛异常；`stop_reason == "length"` 的工具调用**不执行**但必须合成 is_error 结果（§5）；内核 policy-free；`before_tool_call` 支持 2/3/4 参（`zenagent/agent/loop.py:378-388` 探测 arity）。
- `zenagent/app/` 不得 import `zenagent.cli`，也不得 import `argparse`/`textual`/`rich`/`prompt_toolkit`（`zenagent/app/__init__.py:1-4`）。
- 无新依赖；`ruff check .` 保持 clean；`pytest -q` 在 2191 passed 基础上只增不减。
- 每个任务只 `git add` 该任务 `Files` 列出的路径。工作区已有无关未提交改动（`desktop/`、`docs/superpowers/*`），**禁止 `git add -A`**。
- 交付语义不变：适配器只改 `stop_reason` 的推导，不改事件序列、不改 usage；clipboard 搬家后 `/copy` 与 ctrl+v 的行为必须逐字一致（含 i18n 提示）。

## Review Focus

以下是 spec 隐含、但没有任何需求句子点名的输入/条件，每个都在拥有该代码的任务里加断言：

1. **截断 + 完整外观的调用**：provider 同时给出 `MAX_TOKENS`/`incomplete` 与一个语法完整的 `functionCall` → 必须是 `length`（调用被拒绝执行），不能是 `tool_use`（Tasks 1、2）。
2. **hook 缺失**：`before_tool_call` 为 `None` 时子 agent 照常运行，不能因新增参数报错（Task 3）。
3. **旧签名 hook**：只有 2 个参数的 hook 仍然被调用（arity 探测路径）（Task 3）。
4. **懒 import 逃逸**：`app/` 里函数体内 import `zenagent.cli` 也必须被测试抓到（Task 4）。
5. **剪贴板不可用**：平台没有 `pbpaste`/`xclip`/`wl-paste` 时 `/copy` 返回 `cmdmsg.copy_failed` 而不是抛异常（Task 4 搬家不得改变）。

## File Structure

| File | Task | 职责 |
|---|---|---|
| `zenagent/providers/google_generative_ai.py` | 1 | 终局 `stop_reason`：`length` 不被 `tool_use` 覆盖 |
| `zenagent/providers/fixtures/google-truncated-toolcall.txt` | 1 | `finishReason: MAX_TOKENS` + `functionCall` 回放 |
| `tests/test_google.py` | 1 | 上面那条 fixture 的断言 |
| `zenagent/providers/openai_responses.py` | 2 | `status: incomplete` 一律 `length` |
| `zenagent/providers/fixtures/openai-responses-incomplete-toolcall.txt` | 2 | `incomplete` + function_call 回放 |
| `tests/test_openai_responses.py` | 2 | 上面那条 fixture 的断言 |
| `zenagent/tools/task.py` | 3 | `build_task_tool(before_tool_call=…)` 并透传给子 `Agent` |
| `zenagent/app/runtime.py` | 3 | 把 `gate.before_tool_call` 传给 `build_task_tool` |
| `tests/test_task.py` | 3 | 子 agent 的工具调用必须经过门的断言 |
| `zenagent/app/clipboard.py` | 4 | 新增：从 `cli/tui/clipboard.py` 原样搬来 |
| `zenagent/cli/tui/clipboard.py` | 4 | 删除 |
| `zenagent/app/commands.py`、`zenagent/cli/tui/app_parts/drafts.py` | 4 | 改 import 目标 |
| `tests/test_app_commands.py`、`tests/test_tui_links.py`、`tests/test_tui_controls.py` | 4 | 改 monkeypatch 目标 |
| `tests/test_layering.py` | 4 | 新增：分层与 UI 隔离门禁 |

---

### Task 1: Google 适配器不再把截断的工具调用标成 `tool_use`

**Files:**
- Create: `zenagent/providers/fixtures/google-truncated-toolcall.txt`
- Modify: `zenagent/providers/google_generative_ai.py:209-210`
- Test: `tests/test_google.py`

**Interfaces:**
- Consumes: `tests/test_google.py` 现有 helper `_events(name)`（读 `zenagent/providers/fixtures/<name>` → `g._events_from_chunks`）。
- Produces: 无新符号；终局事件满足 `events[-1].reason == "length"` 且 `events[-1].partial.stop_reason == "length"`，`partial.content` 仍含那个 `ToolCall`（由 loop 负责拒绝执行）。

- [ ] **Step 1: 写 fixture**

`zenagent/providers/fixtures/google-truncated-toolcall.txt`，单行（与 `google-toolcall.txt` 同构，只把 `STOP` 换成 `MAX_TOKENS`）：

```
data: {"candidates":[{"content":{"role":"model","parts":[{"functionCall":{"name":"get_weather","args":{"city":"Beijing"}}}]},"finishReason":"MAX_TOKENS"}],"usageMetadata":{"promptTokenCount":20,"candidatesTokenCount":9}}
```

- [ ] **Step 2: 写失败的测试**

在 `tests/test_google.py` 末尾追加：

```python
async def test_truncated_toolcall_reports_length_not_tool_use():
    events = await _events("google-truncated-toolcall.txt")
    final = events[-1].partial
    assert events[-1].reason == "length"
    assert final.stop_reason == "length"
    assert isinstance(final.content[0], ToolCall)
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/test_google.py::test_truncated_toolcall_reports_length_not_tool_use -v`
Expected: FAIL，`assert 'tool_use' == 'length'`。

- [ ] **Step 4: 改推导逻辑**

把 `zenagent/providers/google_generative_ai.py:209-210` 的两行替换为：

```python
    has_calls = any(isinstance(block, ToolCall) for block in partial.content)
    # ``length`` 必须活下来：loop 靠它拒绝执行被截断的调用（contracts §5），
    # 只有当 provider 自己说 stop 时才把调用解释成 tool_use。
    if stop_reason == "stop" and has_calls:
        partial.stop_reason = "tool_use"
    else:
        partial.stop_reason = stop_reason
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_google.py -v`
Expected: 全 PASS（含原有的 `test_toolcall_fixture_emits_a_complete_tool_call`，它的 fixture 是 `STOP`）。

- [ ] **Step 6: 提交**

```bash
git add tests/test_google.py zenagent/providers/google_generative_ai.py zenagent/providers/fixtures/google-truncated-toolcall.txt
git commit -m "fix(google): keep length when a truncated turn carries tool calls"
```

---

### Task 2: Responses 适配器 `incomplete` 一律 `length`

**Files:**
- Create: `zenagent/providers/fixtures/openai-responses-incomplete-toolcall.txt`
- Modify: `zenagent/providers/openai_responses.py:254-262`
- Test: `tests/test_openai_responses.py`

**Interfaces:**
- Consumes: `tests/test_openai_responses.py` 的 `_events(name)`。
- Produces: 终局 `events[-1].reason == "length"`；`partial.content` 仍含 `ToolCall`。

- [ ] **Step 1: 写 fixture**

复制 `zenagent/providers/fixtures/openai-responses-toolcall.txt` 为
`zenagent/providers/fixtures/openai-responses-incomplete-toolcall.txt`，只把最后一行
`"status":"completed"` 改成 `"status":"incomplete"`（其余逐字不变，让该文件与 toolcall fixture 的差异只有这一个字段）。

- [ ] **Step 2: 写失败的测试**

在 `tests/test_openai_responses.py` 末尾追加：

```python
async def test_incomplete_turn_with_calls_reports_length():
    events = await _events("openai-responses-incomplete-toolcall.txt")
    final = events[-1].partial
    assert events[-1].reason == "length"
    assert final.stop_reason == "length"
    assert isinstance(final.content[0], ToolCall)
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/test_openai_responses.py::test_incomplete_turn_with_calls_reports_length -v`
Expected: FAIL，`assert 'tool_use' == 'length'`。

- [ ] **Step 4: 改推导逻辑**

把 `zenagent/providers/openai_responses.py:255-262` 的 `if partial.stop_reason == "pending":` 块替换为：

```python
    if partial.stop_reason == "pending":
        if completed_status == "incomplete":
            # 截断优先于调用：loop 会为每个 call 合成 is_error 结果而不是执行它。
            partial.stop_reason = "length"
        else:
            partial.stop_reason = "tool_use" if has_calls else "stop"
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_openai_responses.py -v`
Expected: 全 PASS。

- [ ] **Step 6: 提交**

```bash
git add tests/test_openai_responses.py zenagent/providers/openai_responses.py zenagent/providers/fixtures/openai-responses-incomplete-toolcall.txt
git commit -m "fix(responses): an incomplete turn stays length even with tool calls"
```

---

### Task 3: 子 agent 继承父进程的权限门

**Files:**
- Modify: `zenagent/tools/task.py`（`build_task_tool` 签名 `504-520`、`execute` 内 `_construct` 约 `702-716`）
- Modify: `zenagent/app/runtime.py:397-424`（`build_task_tool(...)` 调用）
- Test: `tests/test_task.py`

**Interfaces:**
- Consumes: `PermissionGate.before_tool_call(tool_call, args, tool=None) -> dict | None`（`zenagent/app/permissions.py:435`）与 `Agent(before_tool_call=...)`（`zenagent/agent/agent.py:85`）。
- Produces: `build_task_tool(*, …, before_tool_call: Callable | None = None) -> TaskTool`。子 `Agent` 用它做门；`None` 时行为与今天完全一致。

- [ ] **Step 1: 写失败的测试**

在 `tests/test_task.py` 顶部 `from zenagent.types import (...)` 的列表里加入 `ToolResultMessage`，然后在文件末尾追加：

```python
class BashCallingStream:
    """Fake child stream: one ``bash`` call, then a plain text answer."""

    def __init__(self) -> None:
        self.requests: list[list] = []

    async def __call__(self, model, messages, tools, options):
        self.requests.append(list(messages))
        partial = AssistantMessage(
            content=[], provider="p", model="m", usage=Usage(),
            stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        if len(self.requests) == 1:
            call = ToolCall(id="c1", name="bash", arguments={"command": "rm -rf /"})
            partial.content = [call]
            partial.stop_reason = "tool_use"
            yield AssistantEvent(type="toolcall_start", partial=partial, content_index=0, tool_call=call)
            yield AssistantEvent(type="toolcall_end", partial=partial, content_index=0, tool_call=call)
            yield AssistantEvent(type="done", partial=partial, reason="tool_use")
            return
        partial.content = [TextContent("done")]
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")


async def test_child_tool_calls_go_through_the_parent_gate():
    stream = BashCallingStream()
    seen: list[str] = []

    async def gate(tool_call, arguments, tool=None):
        seen.append(tool_call.name)
        return {"block": True, "reason": "denied by policy"}

    tool = build_task_tool(
        model=_model(),
        api_key="k",
        stream_fn=stream,
        base_tools=_tools(),
        mode="zen",
        before_tool_call=gate,
    )

    await tool.execute("t1", {"prompt": "go"})

    assert seen == ["bash"], "the child must consult the parent's gate"
    results = [m for m in stream.requests[1] if isinstance(m, ToolResultMessage)]
    assert results and results[-1].is_error
    assert "denied by policy" in results[-1].content[0].text
    assert "wrote" not in results[-1].content[0].text  # execute never ran
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_task.py::test_child_tool_calls_go_through_the_parent_gate -v`
Expected: FAIL，`TypeError: build_task_tool() got an unexpected keyword argument 'before_tool_call'`。

- [ ] **Step 3: 实现透传**

`zenagent/tools/task.py` 的 `build_task_tool` 签名增加一个关键字（放在 `limits` 之后）：

```python
    before_tool_call: Callable | None = None,
```

并在 `_construct()` 里的 `Agent(...)` 调用中、`artifact_sink=artifact_sink,` 之后加一行：

```python
                before_tool_call=before_tool_call,
```

`build_task_tool` 的 docstring 末尾补一句：`before_tool_call` 是父进程的 harness 门，子 agent 的工具调用必须经过它（`None` 表示不设门）；子 agent 没有用户通道，所以门在子 agent 里只会 allow/refuse，不会提问。

- [ ] **Step 4: 接上运行时**

`zenagent/app/runtime.py` 的 `build_task_tool(` 调用里，`artifact_sink=...` 之后加：

```python
            before_tool_call=gate.before_tool_call,
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_task.py tests/test_app_runtime.py tests/test_permissions.py -v`
Expected: 全 PASS（`test_child_only_gets_read_only_tools_and_never_task_itself` 等旧用例不受影响）。

- [ ] **Step 6: 提交**

```bash
git add tests/test_task.py zenagent/tools/task.py zenagent/app/runtime.py
git commit -m "fix(task): route child tool calls through the parent permission gate"
```

---

### Task 4: clipboard 搬到 app 层，并加分层门禁

**Files:**
- Create: `zenagent/app/clipboard.py`、`tests/test_layering.py`
- Delete: `zenagent/cli/tui/clipboard.py`
- Modify: `zenagent/app/commands.py:631,643,650`、`zenagent/cli/tui/app_parts/drafts.py:120`
- Modify tests: `tests/test_app_commands.py:204,215,316,332,356`、`tests/test_tui_links.py:17`、`tests/test_tui_controls.py:320,335`

**Interfaces:**
- Consumes: 现有实现（`read_clipboard_text() -> str`、`write_clipboard_text(text: str) -> bool`，只用 `shutil`/`subprocess`）。
- Produces: `zenagent.app.clipboard.read_clipboard_text()` / `write_clipboard_text(text)`；`tests/test_layering.py` 的
  `LAYERS: dict[str, int]` 与两个测试函数 `test_layers_import_downward_only()`、`test_app_is_ui_and_cli_free()`。

- [ ] **Step 1: 写门禁测试（先失败）**

创建 `tests/test_layering.py`：

```python
"""Layering gates: imports point downward and ``app/`` stays UI-free."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "zenagent"

#: Lower rank may not import a higher one.
LAYERS = {"cli": 0, "app": 1, "tools": 2, "agent": 3, "providers": 4}

#: What ``zenagent/app/__init__.py`` promises: no UI library, no CLI package.
FORBIDDEN_IN_APP = ("argparse", "rich", "textual", "prompt_toolkit")
FORBIDDEN_PREFIX_IN_APP = "zenagent.cli"


def _imports(path: Path) -> list[str]:
    """Every module name imported anywhere in ``path`` (incl. function bodies)."""
    tree = ast.parse(path.read_text())
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
    return found


def _package(module: str) -> str | None:
    parts = module.split(".")
    if parts[0] != "zenagent" or len(parts) < 2:
        return None
    return parts[1] if parts[1] in LAYERS else None


def test_layers_import_downward_only():
    violations = []
    for path in ROOT.rglob("*.py"):
        parts = path.relative_to(ROOT).parts
        if len(parts) < 2 or parts[0] not in LAYERS:
            continue
        source = parts[0]
        for module in _imports(path):
            target = _package(module)
            if target is not None and LAYERS[target] < LAYERS[source]:
                violations.append(f"{path.relative_to(ROOT)}: {module}")
    assert violations == []


def test_app_is_ui_and_cli_free():
    violations = []
    for path in (ROOT / "app").rglob("*.py"):
        for module in _imports(path):
            root = module.split(".")[0]
            if root in FORBIDDEN_IN_APP or module.startswith(FORBIDDEN_PREFIX_IN_APP):
                violations.append(f"{path.relative_to(ROOT)}: {module}")
    assert violations == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_layering.py -v`
Expected: `test_layers_import_downward_only` 与 `test_app_is_ui_and_cli_free` 都 FAIL，各报一条
`app/commands.py: zenagent.cli.tui.clipboard`。

- [ ] **Step 3: 搬家**

```bash
git mv zenagent/cli/tui/clipboard.py zenagent/app/clipboard.py
```

把 `zenagent/app/clipboard.py` 的模块 docstring 首行改为 `"""Best-effort clipboard text reader (stdlib only, no deps).`，
其余内容逐字保留（`__all__` 不变）。

- [ ] **Step 4: 改生产 import**

- `zenagent/app/commands.py`：删掉 `_copy` 里的 `from zenagent.cli.tui.clipboard import write_clipboard_text`，
  在文件顶部的 app import 区加 `from zenagent.app.clipboard import write_clipboard_text`。
- `zenagent/cli/tui/app_parts/drafts.py`：把 `from zenagent.cli.tui.clipboard import read_clipboard_text` 改为
  `from zenagent.app.clipboard import read_clipboard_text`。

- [ ] **Step 5: 改测试的 monkeypatch 目标**

- `tests/test_app_commands.py`：两处 `from zenagent.cli.tui import clipboard`（`204`、`215`）→ `from zenagent.app import clipboard`；
  三处字符串 `"zenagent.cli.tui.clipboard.write_clipboard_text"`（`316`、`332`、`356`）→ `"zenagent.app.clipboard.write_clipboard_text"`。
- `tests/test_tui_links.py:17`：同样的字符串替换。
- `tests/test_tui_controls.py`：两处 `from zenagent.cli.tui import clipboard` → `from zenagent.app import clipboard`。

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/test_layering.py tests/test_app_commands.py tests/test_tui_links.py tests/test_tui_controls.py -v`
Expected: 全 PASS；`tests/test_layering.py` 两条用例通过。

- [ ] **Step 7: 全量门禁**

Run: `bash scripts/check.sh`
Expected: `uv run ruff check .` → All checks passed；`uv run pytest -q` → 2191 + 3 条新用例全 PASS。

- [ ] **Step 8: 提交**

```bash
git add tests/test_layering.py zenagent/app/clipboard.py zenagent/app/commands.py zenagent/cli/tui/app_parts/drafts.py tests/test_app_commands.py tests/test_tui_links.py tests/test_tui_controls.py
git add -u zenagent/cli/tui/clipboard.py  # stage the deletion left by `git mv`
git commit -m "refactor(app): move clipboard out of the tui and gate the layering rule"
```

---

## Self-Review

- **Spec coverage**：C1 → Task 1、C2 → Task 2（同形态的两处）、C3（子 agent 门）→ Task 3、C5 + 分层门禁 → Task 4。C3/C4（记录 schema）与 I 系列留给批次 2/3，报告里已列顺序。
- **测试有效性**：三条新断言都在改代码前写下并确认失败；Task 4 的门禁测试在搬 clipboard **之前**就会因真实违规而红，不是事后补的装饰。
- **接口一致性**：Task 3 的 `before_tool_call: Callable | None = None` 与 `PermissionGate.before_tool_call(tool_call, args, tool=None)` 的 3 参形态匹配（loop 的 arity 探测 `_hook_accepts(hook, 3)`）；Task 4 的 `LAYERS` 排名 `cli(0) > app(1) > tools(2) > agent(3) > providers(4)` 与 `docs/architecture.md` 的现状一致（除 `app` 缺失，另见报告）。
- **风险**：Task 3 只给子 agent 加了门（allow/refuse），不放宽任何现有判定；Task 4 是纯搬家，`app/commands.py` 改模块级 import 后 `/copy` 不再依赖 TUI 包。
