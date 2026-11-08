# 前端对齐（P0–P3）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 TUI / REPL / headless / RPC 四个入口共享同一套 app 层能力，并修掉当前已确认的功能缺口：REPL 缺工具与命令、`/compact` 不切会话、`/model` 切模型不完整、多个 CLI flag 静默失效、RPC 协议只有 4 个动词且事件字段有损。

**Architecture:** 以 `zenagent.app`（`Runtime` / `ChatController` / `ModelManager` / `SessionManager` / `CommandRegistry`）为唯一装配层。REPL 与 RPC 都改为 `Runtime.bootstrap(...)` + 共享注册表，REPL 只保留输入/输出与流式渲染；`RuntimeOptions` 承载所有跨前端运行选项（thinking / trace / metrics / auto-compact / api_key / 自定义 tools），前端只负责把 CLI flag 填进去。RPC 在同一个 `Runtime` 上增加审批回传、steer、会话/模型/模式/命令请求，事件投影补字段但保持 v1 消息形状不变（纯增量）。

**Tech Stack:** Python ≥3.11；仅运行时依赖 `httpx`；CLI extras `prompt-toolkit` / `rich` / `textual`；pytest + ruff；离线 fake `StreamFn`。

**Spec:** 没有独立 spec；本计划实现 2026-11-02 的前端差距审计结论，架构依据是 `docs/superpowers/specs/2026-10-30-tui-design.md`（§“`Runtime`（前端无关），REPL/TUI/RPC 共用”）与 `docs/superpowers/plans/2026-10-30-tui.md` Task 8（REPL 迁移到 `Runtime` + `ChatController`，从未提交）。审计证据见本计划“背景”一节。

## 背景（审计结论，供实现者对照）

- 命令面：TUI `CommandRegistry` 45 条；REPL `handle_slash` 42 分支、`SLASH_COMMANDS` 只广告 40 条，缺 `/context` `/export` `/plan-review` `/usage`。
- 工具面：TUI/headless 17–18 个（含 `checkpoint`/`rewind`/`lsp`/`github`）；REPL 14 个（缺这 4 个）；RPC 14–15 个（缺 `checkpoint`/`rewind`/`read_skill`）。
- 已确认缺陷：REPL `/compact` 不切会话（实测 `switch_session` 零调用）；REPL `/model` 用裸 `load_catalog()`（自定义模型不可切、不换 api_key/thinking/session_id）；TUI 忽略 `--thinking/--trace/--metrics/--no-auto-compact/--image`，REPL 忽略 `--thinking/--metrics/--image`；RPC 无 session/MCP/skills/扩展/审批/steer，事件投影丢 `args/result/duration_ms/tool_stats`。

## Global Constraints

- Python ≥3.11；运行时依赖仍只有 `httpx`；不新增第三方依赖。
- `zenagent.app` 不得 import `textual` / `prompt_toolkit` / `rich`（前端无关层）。
- 所有新增用户可见文案走 `zenagent/i18n/{en,zh}.py` 的 `t()`，两边 key 必须同步。
- 测试必须离线：用 fake `StreamFn`，不联网、不依赖本机 `gh`/`pyright`。
- RPC 协议保持向后兼容：v1 的 `prompt/cancel/status/shutdown` 请求与 `result/error/event` 响应形状不变，只允许新增请求类型与事件字段。
- REPL 必须保留：流式文本、工具进度行、运行中 steering、`ask` 终端问答、补全、trace/metrics、主题/语言、`/help`。
- 每个任务结束时 `uv run pytest <相关文件> -q` 绿；每个里程碑结束时 `bash scripts/check.sh` 全绿（基线 1877 passed）。
- 提交信息用 conventional commits；每个任务一个提交。

## Review Focus

- **stdin 所有权**：REPL 迁移后 `ask` handler 与 steering reader 仍只能有一个读 stdin；`ChatController` 接管 run 后不得出现两处 `await read()`。
- **RPC 审批**：`prompt` 运行中到达 `approval` 请求、超时、以及 `cancel` 与审批竞争，都不能让服务器死锁或丢事件。
- **会话切换后的持久化**：`/compact`、`/undo`、`/fork`、`/new`、`/import` 之后，`agent_end` 追加必须写进新文件；旧文件字节不变。
- **命令结果降级**：TUI 的 screen 类命令（picker/浮层）在 REPL 必须有文本降级，不能静默无输出或抛异常。
- **flag 不再静默**：任何前端不支持的 flag 要么实现，要么显式报错退出；不允许 argparse 接受后忽略。
- **RPC 旧客户端**：只发 v1 请求的客户端行为与输出必须与现在一致（字段可增不可改）。

---

## 里程碑与任务

执行顺序：**P0 → P1 → P2 → P3 → 文档收尾**。P0 在当前结构上先修可独立验证的缺陷；P1 的迁移会把这些修复替换为 app 层实现，但 P0 的集成测试（`repl(...)` 级别）必须继续通过。

### Task 1（P0）：REPL `/compact` 切换会话

**Files:**
- Modify: `zenagent/cli/repl.py`（`handle_slash` 的 `command == "compact"` 分支，约 :557）
- Test: `tests/test_repl_logic.py`

**Interfaces:**
- Consumes: `handle_slash(..., switch_session=...)` 现有参数；`zenagent.app.compact.compact(agent) -> Path`。
- Produces: `/compact` 成功后 REPL 的会话指针指向新 `*-compact.jsonl`（后续 `agent_end` 追加写入新文件）。

- [x] **Step 1: 写失败测试**

```python
async def test_slash_compact_switches_the_repl_to_the_summary_session(
    isolated_sessions, tmp_path, monkeypatch
):
    from zenagent.agent.session import Session
    from zenagent.types import UserMessage

    agent = make_agent()  # 复用文件内 agent 构造 helper；messages 非空
    agent.replace_messages([UserMessage(content="hi", timestamp=1)])
    switched: list[Session] = []
    lines: list[str] = []

    await handle_slash(
        agent, "compact", "", print_fn=lines.append, switch_session=switched.append
    )

    assert switched, "compact must hand the new session to the REPL"
    assert switched[0].path.name.endswith("-compact.jsonl")
    assert switched[0].path.is_file()
```

- [x] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_repl_logic.py::test_slash_compact_switches_the_repl_to_the_summary_session -q`
Expected: FAIL（`switched == []`）

- [x] **Step 3: 实现**

`handle_slash` 的 compact 分支在 `compact(agent)` 成功后调用
`switch_session(Session(path))`（`switch_session is None` 时跳过，与其它命令一致）。

- [x] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_repl_logic.py -q`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add zenagent/cli/repl.py tests/test_repl_logic.py
git commit -m "fix(repl): switch the session after /compact"
```

---

### Task 2（P0）：REPL `/model` 走共享的模型切换逻辑

**Files:**
- Modify: `zenagent/app/models.py`（抽出 `apply_model_switch`，`ModelManager.switch` 调用它）
- Modify: `zenagent/cli/repl.py`（`handle_slash` 的 `command == "model"` 分支；`repl()` 把已应用用户配置的 `catalog` 传给 `handle_slash`）
- Test: `tests/test_repl_logic.py`、`tests/test_app_models.py`（不存在则新建）

**Interfaces:**
- Produces: `apply_model_switch(agent: Agent, catalog: dict[str, ProviderConfig], guard, model_id: str) -> SwitchResult`；`SwitchResult(ok: bool, reason: str | None)`。
- Consumes: `zenagent.app.support.api_key_for` / `session_id_for`；`zenagent.providers.catalog.find_model`。

行为要求：成功时同时更新 `agent.model`、`agent.api_key`、`agent.thinking`（非 reasoning 模型置 `"off"`）、`agent.session_id`、`guard.context_window`；无 key 时返回失败原因而不是切过去。

- [x] **Step 1: 写失败测试**

```python
def test_apply_model_switch_updates_key_thinking_and_session_id():
    # catalog 里两个 provider：a（agent 当前）与 b（有 key）
    # 断言切换后 agent.api_key == b 的 key、agent.thinking == "off"、
    # agent.session_id != 旧值、guard.context_window == 新窗口
    ...

async def test_slash_model_uses_the_user_config_catalog(capsys):
    # repl 级：monkeypatch apply_user_config 返回带自定义 provider 的 catalog，
    # 输入 "/model custom-x" 后 stream 收到的 model.id == "custom-x"
    ...
```

- [x] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_repl_logic.py -q -k model`
Expected: FAIL（自定义模型/无 key 行为不满足）

- [x] **Step 3: 实现**

`app/models.py` 抽出函数并让 `ModelManager.switch` 复用；REPL `repl()` 把 `catalog`
传给 `handle_slash(catalog=...)`，`/model` 分支调用 `apply_model_switch`，
失败时打印 `SwitchResult.reason`。`handle_slash` 新增关键字参数 `catalog=None`，
`None` 时回退到 `load_catalog()`（旧调用方兼容）。

- [x] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_repl_logic.py tests/test_app_models.py -q`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add zenagent/app/models.py zenagent/cli/repl.py tests/
git commit -m "fix(repl): switch models through the shared model manager logic"
```

---

### Task 3（P0）：REPL 启动 flag 生效（`--thinking` / `--metrics` / `--image`）

**Files:**
- Modify: `zenagent/cli/repl.py`（`repl()` 增加 `thinking`、`images` 参数；Agent 构造使用 `thinking`；启动时校验并挂载图片）
- Modify: `zenagent/cli/main.py`（REPL 调用处补 `thinking=args.thinking`、`metrics=args.metrics`、`images=args.images`）
- Test: `tests/test_repl_logic.py`、`tests/test_cli.py`

**Interfaces:**
- `repl(..., thinking: str | None = None, images: list[Path] | None = None)`。
- 语义：`thinking=None` 时保持“reasoning 模型 high，否则 off”；显式值原样传给 `Agent`。`images` 用 `read_image_argument` 校验，非 vision 模型在启动时报错返回 1；合法图片进入首次 prompt 的待发附件。

- [x] **Step 1: 写失败测试**

```python
async def test_repl_forwards_the_thinking_flag_to_the_agent(isolated_sessions):
    seen: list = []
    async def stream(model_, messages, tools, options):
        seen.append(options.thinking)
        yield ...
    await repl("glm-5.3", stream_fn=stream, input_fn=scripted_input("hi", "/exit"),
               print_fn=lambda *a, **k: None, api_key="k", thinking="low")
    assert seen[0] == "low"

def test_repl_launch_forwards_thinking_metrics_and_images(monkeypatch):
    # monkeypatch zenagent.cli.repl.repl，断言 kwargs 含 thinking/metrics/images
    ...

def test_image_flag_on_a_non_vision_model_fails_before_the_repl(monkeypatch, tmp_path):
    ...
```

- [x] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_repl_logic.py tests/test_cli.py -q -k "thinking or image or metrics"`
Expected: FAIL（参数不存在/未转发）

- [x] **Step 3: 实现**

`repl()` 内：`thinking` 传给 `Agent(thinking=...)`；`images` 在构造 agent 前
`read_image_argument` + `model.supports_vision` 校验，成功后放进 `pending_images`。
`main.py` REPL 调用补三个 kwarg。

- [x] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_repl_logic.py tests/test_cli.py -q`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add zenagent/cli/repl.py zenagent/cli/main.py tests/
git commit -m "fix(cli): honor --thinking/--metrics/--image in the REPL"
```

---

### Task 4（P0）：RPC 事件投影补字段

**Files:**
- Modify: `zenagent/cli/rpc/`（`serialize_event`）
- Test: `tests/test_rpc.py`

**Interfaces:**
- `serialize_event(event) -> dict` 新增（全部有界）：
  - `tool_execution_start`：`args`（`json.dumps` 后截断到 4000 字符）
  - `tool_execution_end`：`result_text`（TextContent 拼接后尾部 4000 字符）、`duration_ms`
  - `agent_end`：`tool_stats`（原样，小字典）
- 既有字段与 v1 行为不变。

- [x] **Step 1: 写失败测试**

```python
def test_serialize_event_carries_tool_args_result_and_duration():
    event = AgentEvent(type="tool_execution_start", tool_name="bash",
                       tool_call_id="c1", args={"command": "echo hi"})
    payload = serialize_event(event)
    assert payload["args"] == {"command": "echo hi"}

    event = AgentEvent(type="tool_execution_end", tool_name="bash",
                       tool_call_id="c1", is_error=False, duration_ms=12,
                       result=ToolResultAgent(...))
    payload = serialize_event(event)
    assert payload["duration_ms"] == 12
    assert "hi" in payload["result_text"]

def test_serialize_event_bounds_large_payloads():
    ...
```

- [x] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_rpc.py -q`
Expected: FAIL（缺字段）

- [x] **Step 3: 实现**

`serialize_event` 增加常量 `_EVENT_TEXT_CHARS = 4000`；`_text_of(result)` 复用现有
partial 逻辑；`args` 用 `json.dumps(..., ensure_ascii=False)` 后截断。

- [x] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_rpc.py -q`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add zenagent/cli/rpc/ tests/test_rpc.py
git commit -m "feat(rpc): carry tool args, results and durations in events"
```

---

### Task 5（P1）：`RuntimeOptions` 承载跨前端运行选项

**Files:**
- Modify: `zenagent/app/runtime.py`（`RuntimeOptions`、`Runtime.bootstrap`、`Runtime.close`、`_recompose`、`auto_compact` 属性）
- Modify: `zenagent/app/chat.py`（`_maybe_auto_compact` 尊重 `runtime.auto_compact`）
- Test: `tests/test_app_runtime.py`

**Interfaces:**
- `RuntimeOptions` 新增字段：`api_key: str | None = None`、`trace: Path | str | None = None`、`metrics: Path | None = None`、`auto_compact: bool = True`、`tools: list | None = None`、`mcp_retry_interval_s: float = 10.0`。
- `Runtime` 新增属性：`trace_path: str | None`（已有）、`auto_compact: bool`、`tracer`/`metrics_path` 内部字段。
- 行为：
  - `api_key` 非空时覆盖 catalog 取 key，且不再因缺 key 报错。
  - `trace` 非空 → 建 `TraceRecorder(trace)` 并 subscribe；`close()` 时 `tracer.close()`。
  - `metrics` 非空 → 每次 `agent_end` 后 `append_metrics(...)` 并重置 recorder 重新 subscribe。
  - `tools` 非空 → 跳过 MCP/skills 工具装配，`_recompose` 只保留该列表 + 扩展工具。
  - `auto_compact=False` → `ChatController._maybe_auto_compact` 直接返回。
  - `mcp_retry_interval_s` 传给 `McpManager`。

- [x] **Step 1: 写失败测试**

```python
async def test_runtime_writes_trace_and_metrics(runtime_factory, tmp_path):
    trace, metrics = tmp_path / "t.jsonl", tmp_path / "m.jsonl"
    runtime = await runtime_factory(stream_fn=text_stream(), trace=trace, metrics=metrics)
    await runtime.agent.prompt("hi")
    assert trace.read_text().strip()
    assert metrics.read_text().strip()

async def test_runtime_respects_auto_compact_off(runtime_factory):
    runtime = await runtime_factory(stream_fn=text_stream(), auto_compact=False)
    assert runtime.auto_compact is False

async def test_runtime_api_key_override_skips_the_catalog_key(runtime_factory, monkeypatch):
    ...
```

- [x] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_app_runtime.py -q`
Expected: FAIL（未知字段）

- [x] **Step 3: 实现**

按 Interfaces 改 `RuntimeOptions` 与 `bootstrap`；`Runtime.close` 关闭 tracer；
metrics 在 `persist` 同一个订阅里处理（`agent_end` 后 append 再重建 recorder）；
`_recompose` 在 `self._custom_tools is not None` 时用它代替组合结果。

- [x] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_app_runtime.py tests/test_app_chat.py -q`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add zenagent/app/runtime.py zenagent/app/chat.py tests/
git commit -m "feat(app): carry trace, metrics, auto-compact and api-key options on the runtime"
```

---

### Task 6（P1）：REPL 改用 `Runtime.bootstrap` 装配（命令实现暂不动）

**Files:**
- Modify: `zenagent/cli/repl.py`（删除手装段落，改为 Runtime；`handle_slash` 保持原实现，回调接到 runtime）
- Test: `tests/test_repl_logic.py`（fixture 改 patch `zenagent.app.runtime.load_user_config`）

**Interfaces:**
- Consumes: `Runtime.bootstrap(RuntimeOptions(...), stream_fn=..., gate=..., allow_missing_key=False)`；`Runtime.agent/session/guard/mcp/skills/templates/extension_commands/journal`；`Runtime.reconcile_async()`、`refresh_mcp()`、`switch_session()`、`set_mode()`。
- Produces: `repl()` 行为不变，但工具集/技能/MCP/扩展/会话/journal/artifact 全部来自 Runtime；REPL 获得 `checkpoint`/`rewind`/`lsp`/`github` 工具。

REPL 保留自己的：`_EventPrinter`、`ask_user`、`set_theme`/`set_language`、`keybindings`、completion、`_make_default_reader`。`reload_runtime` 改为 `await runtime.reconcile_async()`；`refresh_mcp` 改为 `await runtime.refresh_mcp()`；`set_mode` 改为 `runtime.set_mode()` + 重新打印；`switch_session` 改为 `await runtime.switch_session(path)`（`handle_slash` 的回调签名不变，内部适配）。

- [x] **Step 1: 写失败测试**

```python
async def test_repl_exposes_the_new_tools(isolated_sessions):
    seen_tools: list[str] = []
    async def stream(model_, messages, tools, options):
        seen_tools.extend(tool.name for tool in tools)
        yield ...
    await repl("glm-5.3", stream_fn=stream, input_fn=scripted_input("hi", "/exit"),
               print_fn=lambda *a, **k: None, api_key="k")
    for name in ("checkpoint", "rewind", "lsp"):
        assert name in seen_tools

async def test_repl_resume_uses_the_runtime_session(isolated_sessions):
    ...
```

- [x] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_repl_logic.py -q -k "new_tools or resume"`
Expected: FAIL（缺工具）

- [x] **Step 3: 实现**

替换 `repl()` 内约 :954–:1250 的手装逻辑；`handle_slash` 的 `mcp_manager`、
`skill_list`、`template_list`、`extension_commands`、`reload_runtime`、`refresh_mcp`、
`set_mode`、`switch_session`、`restore` 等参数全部由 runtime 派生；删除不再使用的
imports（`McpManager`、`discover_skills`、`discover_templates`、`load_extensions`、
`build_todo_tool`、`build_task_tool`、`build_artifact_sink`、`journal_for`、
`attach_journal`、`ContextGuard` 等）。`handle_slash` 的 `guard` 参数传 `runtime.guard`。

- [x] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_repl_logic.py tests/test_cli.py -q`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add zenagent/cli/repl.py tests/
git commit -m "refactor(repl): assemble the REPL on Runtime"
```

---

### Task 7（P1）：REPL 命令分发切到共享 `CommandRegistry`

**Files:**
- Modify: `zenagent/cli/repl.py`（删除 `handle_slash` 与命令分支；新增 `render_command_result(result, ...)` 文本降级）
- Modify: `zenagent/cli/completion.py`（命令表可由外部注入：`ReplCompleter(commands=...)`、`help_text(commands)`；默认仍用静态表以兼容旧测试）
- Modify: `zenagent/cli/main.py`（无）
- Test: `tests/test_repl_logic.py`、`tests/test_repl_commands.py`（新建）、`tests/test_templates.py`、`tests/test_extensions.py`、`tests/test_project_scoping.py`、`tests/test_completion.py`

**Interfaces:**
- `repl()` 内构建 `AppServices(runtime, chat, ModelManager(runtime), SessionManager(runtime))` 与 `build_default_commands(services)`。
- `render_command_result(result: CommandResult, *, name: str, print_fn, models, set_theme, set_language, keybindings, session) -> str | None`：返回 `"exit"` 表示退出；应用 `model/appearance/language/message/quit`，`screen` 走文本降级：
  - `model-picker` → 打印当前模型与用法；`mode-picker` → 当前模式 + 词表；`thinking-picker` → 当前档位 + 词表；`theme-picker`/`language-picker` → 当前值 + 词表
  - `session-picker` / `session-picker-all` → 会话列表（含 `*` 当前标记与 `[项目]`）
  - `hotkeys` → REPL 自己的键位表（`keybindings`），忽略 TUI 文案
  - `todo-edit` → todo 文本列表；`settings` → 设置行；`plan-review` → 最近 assistant 文本 + “/mode full 批准”提示
  - `usage-dashboard` / `context` / `display-reset` / `search-settings` / `mcp-add` → 只打印 message（无 message 时给一行提示）
- `/help` 用 registry 的命令名+描述 + 模板列表；`/doctor` 特例：REPL 直接 `await run_doctor(print_fn=...)`（保留旧行为）。
- 优先级：模板 → skill 提交 → registry 内建 → 扩展命令 → unknown。
- 删除 `SLASH_COMMANDS` 作为 REPL 唯一真源：REPL 用 registry 生成补全/帮助表；`completion.SLASH_COMMANDS` 保留给未注入 mapping 的调用方。

- [x] **Step 1: 写失败测试**

```python
async def test_every_registry_command_is_available_in_the_repl(services):
    registry = build_default_commands(services)
    for command in registry.names():
        assert await repl_dispatch_is_known(command.name), command.name

async def test_repl_renders_picker_screens_as_text(capsys):
    result = CommandResult(screen="session-picker")
    out = render_command_result(result, name="sessions", print_fn=print, ...)
    assert "session" in capsys.readouterr().out

async def test_repl_help_lists_every_registry_command(services):
    ...
```

- [x] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_repl_commands.py -q`
Expected: FAIL（模块不存在）

- [x] **Step 3: 实现**

删除 `handle_slash` 与约 600 行命令分支；实现 `render_command_result`；
`repl()` 的输入循环改为 `result = await commands.dispatch(name, argument)` 后渲染；
扩展命令与模板/skill 保持原优先级。改写 `tests/test_repl_logic.py`：保留
`split_input`/循环/进度/steering 测试，命令行为断言迁移到 `tests/test_repl_commands.py`
（dispatch + render），会话类行为保留 `repl(...)` 集成测试。

- [x] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_repl_logic.py tests/test_repl_commands.py tests/test_completion.py tests/test_templates.py tests/test_extensions.py tests/test_project_scoping.py -q`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add zenagent/cli/repl.py zenagent/cli/completion.py tests/
git commit -m "refactor(repl): dispatch slash commands through the shared registry"
```

---

### Task 8（P1）：REPL 运行循环切到 `ChatController`

**Files:**
- Modify: `zenagent/cli/repl.py`（run 循环、状态行、图片、自动压缩、retry）
- Test: `tests/test_repl_logic.py`

**Interfaces:**
- `repl()` 内 `chat = ChatController(runtime)`；run 提交 `chat.submit(text)`，运行中输入 `chat.steer(line)`，中断 `chat.abort()`。
- `/retry` 由 registry 的 `_retry` 直接调用 `chat.retry()`（内部起 run）；REPL 循环不再特判。
- `/compact` 走 `chat.compact_now()`（切会话、carry todos、reload）；REPL 不再自己 `compact()`。
- `/image` / `/images` 走 `chat.attach_image` / `chat.attachments`；REPL 删除 `pending_images`。
- 自动压缩由 `ChatController` 负责（Task 5 的 `auto_compact` 开关生效）；REPL 删除 `maybe_auto_compact` 调用。
- 状态行：run 结束后用 `aggregate_usage(runtime.agent.messages)` 打印，保持现有格式。
- `metrics`/`trace` 由 Runtime 负责（Task 5），REPL 删除 `MetricsRecorder`/`TraceRecorder` 代码。

- [x] **Step 1: 写失败测试**

```python
async def test_repl_retry_command_reruns_through_the_controller(isolated_sessions):
    ...  # /retry 后 stream 收到第二次请求，transcript 不重复 user 消息

async def test_repl_compact_command_switches_and_continues_in_the_new_file(isolated_sessions):
    ...  # /compact 后再输入一轮；断言新 compact 文件含压缩摘要 + 新消息，旧文件字节不变

async def test_repl_auto_compact_off_does_not_compact(isolated_sessions):
    ...  # auto_compact=False + 人为把 guard.last_usage 抬高；运行后不出现 *-compact.jsonl
```

- [x] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_repl_logic.py -q -k "compact or retry"`
Expected: FAIL

- [x] **Step 3: 实现**

按 Interfaces 改循环；`reap()` 改为 `await chat.wait_for_idle()`（未运行时立即返回）；
运行结束打印状态行；删除 `pending_images`、`metrics_recorder`、`tracer`、`run_task` 相关旧逻辑。

- [x] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_repl_logic.py tests/test_cli.py -q`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add zenagent/cli/repl.py tests/
git commit -m "refactor(repl): drive runs through ChatController"
```

---

### Task 9（P2）：RPC 服务器 v2 核心（审批 / steer / 模型 / 模式 / 工具）

**Files:**
- Modify: `zenagent/cli/rpc/`
- Test: `tests/test_rpc.py`、`tests/test_rpc_v2.py`（新建）

**Interfaces:**
- `RpcServer(agent=None, read_line=None, write_line=None, *, runtime=None, chat=None, models=None, sessions=None, commands=None, approval_timeout_s=120.0)`；`agent` 属性优先取 `runtime.agent`。不传 runtime 时保持 v1 能力（旧测试兼容）。
- 新请求（响应统一 `{"type":"result","id":...,"ok":true,...}` / `{"type":"error",...}`）：
  - `{"type":"steer","text":...}` → `chat.steer`（无 chat 时 `agent.steer`）
  - `{"type":"tools"}` → `[{"name","description","approval"}]`
  - `{"type":"model","model_id":...}` → `models.switch`；失败 `ok:false, reason`
  - `{"type":"mode","mode":...}` → `runtime.set_mode`；失败 `ok:false, reason`
  - `{"type":"usage"}` → 与 result 相同的 usage 字典
  - `{"type":"approval","request_id":..., "decision":"allow"|"always"|"deny"}` → 解析挂起审批
- 审批：production 用 `PermissionBroker.wrap(build_permission_gate(interactive=False, ...))` + `broker.set_confirm(...)`；confirm 发送
  `{"type":"event","event":{"type":"approval_request","request_id","tool_name","args","verdict"}}` 并等待对应 future（超时 deny，取消时 deny）。
- `prompt` 有 runtime 时走 `chat.submit` + `chat.wait_for_idle`；无 runtime 时维持 `agent.prompt`。

- [x] **Step 1: 写失败测试**

```python
async def test_steer_request_reaches_the_running_turn(...)
async def test_approval_request_round_trip(...)  # 发 approval_request → 回 approval allow → 工具执行
async def test_approval_timeout_denies(...)
async def test_tools_request_lists_model_visible_tools(...)
async def test_model_and_mode_requests(...)
```

- [x] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_rpc_v2.py -q`
Expected: FAIL（未知 type）

- [x] **Step 3: 实现**

按 Interfaces 改 `RpcServer`；`_run()` 改为 `Runtime.bootstrap` + broker + 管理器；
`serialize_event` 使用 Task 4 的扩展字段；`_send` 加锁不需要（单事件循环）。

- [x] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_rpc.py tests/test_rpc_v2.py -q`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add zenagent/cli/rpc/ tests/
git commit -m "feat(rpc): protocol v2 with approvals, steering and runtime control"
```

---

### Task 10（P2）：RPC 会话/命令请求与 CLI flag

**Files:**
- Modify: `zenagent/cli/rpc/`（`session`、`command` 请求；`main()` 参数）
- Test: `tests/test_rpc_v2.py`、`tests/test_cli.py`

**Interfaces:**
- `{"type":"session","op":"current"|"list"|"new"|"fork"|"resume"|"import"|"undo"|"delete"|"compact"|"retry","path"?,"scope"?}`
  - `current` → `{"path","name"}`；`list` → `entries(scope=...)` 投影；`new/fork/resume/import` 走 `SessionManager` / `runtime.switch_session`；`undo` 走 `SessionManager.undo` + `chat.reload_transcript(drop_pending=True)`；`delete` 走 `SessionManager.delete`；`compact` 走 `chat.compact_now`；`retry` 走 `chat.retry`（返回重跑文本，事件继续流）
- `{"type":"command","name":...,"argument":...}` → `commands.dispatch`；应用 `result.model`（`models.switch`）与 `result.quit`（回包后关闭）；返回 `{"message","screen","show_help","reset_transcript","quit"}`
- `zen rpc` flags：`-m/--model`、`--yes`、`--mode`、`--read-only`、`--thinking`、`--max-turns`、`--max-cost`、`--retries`、`--resume`、`--no-mcp`、`--mcp-file`、`--mcp-server`、`--no-skills`、`--no-context-files`、`--trace`、`--metrics`、`--cwd`、`--allow-dangerous`、`--allow-outside`、`--allow-destructive`、`--allow-protected`。
- 无 key/未知模型：stderr 一行 + exit 2（与 CLI 一致）。

- [x] **Step 1: 写失败测试**

```python
async def test_session_new_and_compact_requests(...)
async def test_command_request_runs_slash_commands(...)
def test_rpc_flags_forward_to_runtime_options(monkeypatch, tmp_path):
    # monkeypatch zenagent.app.runtime.Runtime.bootstrap 捕获 RuntimeOptions
    ...
```

- [x] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_rpc_v2.py -q -k "session or command or flags"`
Expected: FAIL

- [x] **Step 3: 实现**

按 Interfaces 实现；`_run()` 组装 `RuntimeOptions`；错误统一回包。

- [x] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_rpc.py tests/test_rpc_v2.py tests/test_cli.py -q`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add zenagent/cli/rpc/ tests/
git commit -m "feat(rpc): session and command requests, full CLI flag set"
```

---

### Task 11（P3）：TUI 启动 flag 全部生效

**Files:**
- Modify: `zenagent/cli/main.py`（TUI 分支：`RuntimeOptions` 补 `thinking/trace/metrics/auto_compact`；图片校验后传给 `ZenAgentApp`）
- Modify: `zenagent/cli/tui/app.py`（`__init__` 增加 `images: list[ImageContent] | None`，mount 时 `chat.attach_image`）
- Test: `tests/test_cli.py`、`tests/test_tui_attachments.py`

**Interfaces:**
- TUI 与 headless/REPL 一致：`--thinking` 传给 `RuntimeOptions.thinking`；`--trace`/`--metrics` 传给 Runtime；`--no-auto-compact` 传给 `auto_compact=False`；`--image` 校验 vision 后作为初始附件。
- `/trace` 命令显示 `runtime.trace_path`（Runtime 已在 Task 5 设置）。

- [x] **Step 1: 写失败测试**

```python
def test_tui_forwards_thinking_trace_metrics_and_auto_compact(monkeypatch):
    ...  # 捕获 RuntimeOptions

def test_tui_launch_attaches_image_flags(monkeypatch, tmp_path):
    ...  # 捕获 ZenAgentApp kwargs，断言 images 非空

async def test_tui_app_attaches_initial_images(...)
```

- [x] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_cli.py tests/test_tui_attachments.py -q -k "flag or initial"`
Expected: FAIL

- [x] **Step 3: 实现**

`main.py` TUI 分支补参数与图片校验（复用 headless 的校验逻辑，抽
`_read_images(model, paths) -> list[ImageContent]` 供两处使用）；`ZenAgentApp`
接收 `images` 并在 `on_mount` 调 `chat.attach_image`。

- [x] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_cli.py tests/test_tui_attachments.py -q`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add zenagent/cli/main.py zenagent/cli/tui/app.py tests/
git commit -m "fix(tui): honor --thinking/--trace/--metrics/--no-auto-compact/--image"
```

---

### Task 12（收尾）：文档、CHANGELOG、计划收尾与全量门禁

**Files:**
- Modify: `docs/guide/cli.md`（flag 生效矩阵、REPL 命令清单）
- Modify: `docs/guide/rpc-sdk.md`（v2 请求/事件表、审批往返示例）
- Modify: `docs/guide/tui.md`（启动 flag、`/trace`）
- Modify: `docs/feature-map.md`（前端能力对照表；删除“`/plan-review` 未做”的过期行；REPL 命令清单）
- Modify: `docs/internals/tools-internals.md`（RPC 现在有 JSONL 会话与 todo sidecar）
- Modify: `docs/internals/context-compaction.md`（`/compact` 在所有前端切会话）
- Modify: `examples/embed.py`（docstring：`ChatController` 现在确实被 TUI 与 REPL 共用）
- Modify: `CHANGELOG.md`（Unreleased 段落）
- Modify: 本计划（勾选完成项 + 收尾证据）

- [x] **Step 1: 更新文档与 CHANGELOG**

按 Files 列表逐项修改；命令表/flag 表与代码逐条核对（`/help`、`zen rpc --help`、
`zen --help`）。

- [x] **Step 2: 全量门禁**

Run: `bash scripts/check.sh`
Expected: `All checks passed!` + `1877+ passed`

- [x] **Step 3: 提交**

```bash
git add docs examples CHANGELOG.md
git commit -m "docs: record the frontend alignment and the RPC v2 protocol"
```

---

## 执行记录（2026-11-02）

- 分支 `feat/frontend-alignment`，基线 `8284ba0`（v0.6.2），`bash scripts/check.sh` 基线 1877 passed。
- 任务全部完成；关键结果：
  - P0：`/compact` 切会话（实测 + 回归测试）、`/model` 走 `apply_model_switch`、REPL
    `--thinking/--metrics/--image`、RPC 事件补 `args/result_text/duration_ms/tool_stats`。
  - P1：REPL 迁到 `Runtime` + `ChatController` + 共享 `CommandRegistry`；工具集与 TUI 对齐；
    `test_repl_logic.py` + 新增 `test_repl_commands.py` 覆盖命令/文本降级/会话切换。
  - P2：RPC v2（审批往返、steer、tools/model/mode/usage、session、command、完整 flag 集）；
    v1 请求形状不变，`tests/test_rpc.py` 原样通过，新增 `tests/test_rpc_v2.py`。
  - P3：TUI `--thinking/--trace/--metrics/--no-auto-compact/--image` 生效；`/trace` 显示路径。
- 门禁：`bash scripts/check.sh` → lint 全绿 + `1894 passed`（最终提交 `8f2f8b0` 之后）。
- 过程 Ruling：Task 7+8 合并实现（注册表的 retry/compact 拥有 run，拆开会留下坏中间态）；
  Task 9–11 因暂存合并在同一提交落地，各自测试套件独立通过。

## 完成标准

- `zen --help` 里 TUI/REPL/RPC 支持的 flag 都真实生效；不支持的组合显式报错。
- REPL 的 `/help` 与补全表覆盖 `CommandRegistry` 全部命令；四个新命令（`/context` `/export` `/plan-review` `/usage`）可用。
- REPL 工具集包含 `checkpoint`/`rewind`/`lsp`/`github`（`gh` 存在时）。
- `/compact` 后新消息写入 `*-compact.jsonl`；`/model` 能切自定义模型并换 key/thinking/session id。
- RPC v2：审批往返、steer、会话操作、命令请求、事件含 `args/result_text/duration_ms/tool_stats`；v1 客户端行为不变。
- TUI：`--thinking/--trace/--metrics/--no-auto-compact/--image` 生效。
- `bash scripts/check.sh` 全绿；工作树干净。
