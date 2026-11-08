# 架构清理实施计划(P0/P1/P2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落实 `docs/reviews/2026-11-07-architecture-review.md` 的全部修复项:消除前端层四类重复、纠正两个错位契约(StreamOptions、工具协议)、补齐 GUI 防线(CSP、rpc_start 阻塞、O(n) 扫描)、协议握手与契约测试、异步 IO。

**Architecture:** 不动六层结构与进程模型。所有改动都是"移动契约到中立位置 + 把复制粘贴收敛到单一来源 + 给既有边界上机械门"。Python 侧每任务跑 `uv run pytest -q` + `uv run ruff check`;TS/Rust 侧跑 `pnpm build && pnpm test && pnpm e2e`(在 `desktop/` 下)。

**Tech Stack:** Python 3.11+(stdlib httpx 内核,pytest/ruff)、React+zustand(TS strict)、Tauri 2 Rust 壳、node --test / Playwright。

**Spec:** `docs/reviews/2026-11-07-architecture-review.md`(本计划逐条对应其 §六 整理清单;行号以审查当日 HEAD `dda968f` 为准,偏移 ±5 行内应仍可定位)。

## Global Constraints

- 运行时第三方依赖**只允许 httpx**(pyproject `dependencies` 不得新增条目);CLI extra 仅 prompt-toolkit/rich/textual。
- 分层规则被 `tests/test_layering.py` 钉死:上层 import 下层,`app/` 不 import `zenagent.cli`/argparse/textual/rich/prompt_toolkit。任何任务不得引入反向依赖。
- 线协议输出(`rpc.py` 的 result/event JSONL 形状)除本计划明确新增的字段外**逐字节保持兼容**;desktop e2e 全绿是协议未破的判据。
- 每个任务结束时代码可合并:测试过、ruff 过、单独成一个 commit(格式 `refactor: ...`/`fix: ...`,沿用仓库 conventional commits)。
- 神文件**只许变小**:rpc.py/repl.py/modals.py/task.py 拆分任务中,新文件必须比原文件小,原文件不得再增长。
- 不重命名公共 API(`zenagent.agent`、`zenagent.providers.registry` 导出名不变);旧路径用 re-export 过渡,不一次性打断所有 importer。

## Review Focus

- **协议双写漂移**:改 `rpc.py` 请求类型时忘记同步 `desktop/src/rpc/types.ts` 与 e2e mock——契约测试(Task 8)必须先落地并保持绿灯,之后所有改协议的任务都靠它兜底。测试:`tests/test_rpc_contract.py` 两端方法集断言。
- **`read_only` 再次丢失**:历史上只有 RPC 转发了它。统一装配(Task 9)后用四模式参数化测试钉住每个入口都透传。
- **同进程双前端抢占全局钩子**:`set_ask_handler` 在 Task 10 完成前仍是全局态,期间任何新代码不得再增加对它的调用点。测试:Task 10 后导入 `tools.ask` 不再有可变模块态。
- **会话 JSONL 顺序**:session append 改 to_thread(Task 13)必须逐条 await,乱序=数据损坏。测试:并发 append 100 条后顺序断言(已有 session 测试补并发用例)。
- **CSP 误杀**:style-src 收紧可能打断 React 内联样式与 Vite 注入。预期行为:打包后聊天 Markdown、代码高亮、图标全部正常渲染。验证:`pnpm build` + `pnpm e2e` + 一次 `pnpm tauri dev` 人工冒烟。

---

## Phase P0

### Task 1: `StreamOptions` 迁往 `providers/stream_options.py`

**Files:**
- Create: `zenagent/providers/stream_options.py`
- Modify: `zenagent/providers/openai_completions.py:59-76`(删除定义,改 import + re-export)
- Modify: import 站点 `zenagent/agent/loop.py:51`、`zenagent/providers/anthropic_messages.py:32`、`zenagent/providers/google_generative_ai.py:20`、`zenagent/providers/openai_responses.py:20`、`zenagent/providers/registry.py:36`、`zenagent/app/compact.py:17`、`zenagent/app/btw.py:14`
- Test: `tests/test_layering.py`(追加)

**Interfaces:**
- Produces: `from zenagent.providers.stream_options import StreamOptions`(唯一权威路径);`zenagent.providers.openai_completions.StreamOptions` 仍可用(re-export,identity 相等)。

- [ ] **Step 1: 追加失败测试到 `tests/test_layering.py`**

```python
def test_stream_options_lives_in_neutral_module():
    from zenagent.providers import openai_completions, stream_options
    assert openai_completions.StreamOptions is stream_options.StreamOptions

def test_loop_does_not_import_concrete_adapter():
    import inspect, zenagent.agent.loop as loop
    src = inspect.getsource(loop)
    assert "openai_completions" not in src
```

- [ ] **Step 2: 跑测试确认失败** — Run: `uv run pytest tests/test_layering.py -q`;Expected: FAIL(`stream_options` 模块不存在 / loop 源码含 openai_completions)
- [ ] **Step 3: 建 `stream_options.py`**——把 dataclass 原样搬过去(含 docstring,7 个字段:`api_key/thinking/max_tokens/temperature/session_id/extra_headers/abort`),import `AbortToken` 自 `zenagent.events`。`openai_completions.py` 顶部 `from zenagent.providers.stream_options import StreamOptions` 并保留在 `__all__`。
- [ ] **Step 4: 七处 import 改走新路径**(上列站点);`loop.py` 不得再出现 `openai_completions` 字样。
- [ ] **Step 5: 验证** — Run: `uv run pytest tests/test_layering.py -q && uv run ruff check && uv run pytest -q`;Expected: 全 PASS
- [ ] **Step 6: Commit** — `git commit -m "refactor: move StreamOptions to a provider-neutral module"`

### Task 2: 工具协议抽到 `agent/protocol.py`,解除 loop 延迟 import

**Files:**
- Create: `zenagent/agent/protocol.py`
- Modify: `zenagent/agent/loop.py`(删除 `ToolResultAgent` :82、`AgentTool` :100、`ToolContext` :144 三个定义;顶部 `from zenagent.agent.protocol import ...` 并 re-export;删除 :284-290 的函数内 import,改为模块顶部 `from zenagent.agent.tool_exec import run_tool_batch, truncated_call_outcome`)
- Modify: `zenagent/agent/tool_exec.py:17`(改从 `protocol` import 协议类型)
- Test: `tests/test_layering.py`(追加)

**Interfaces:**
- Produces: `zenagent.agent.protocol` 导出 `AgentTool, ToolContext, ToolResultAgent`;`zenagent.agent.loop` re-export 不断链,`tools/ mcp/ skills extensions app/` 现有 import 全部不动。

- [ ] **Step 1: 追加失败测试**

```python
def test_tool_protocol_lives_outside_loop():
    import inspect, zenagent.agent.loop as loop, zenagent.agent.protocol as proto
    assert "class AgentTool" in inspect.getsource(proto)
    assert "class AgentTool" not in inspect.getsource(loop)

def test_loop_imports_tool_exec_at_module_level():
    import ast, zenagent.agent.loop as loop
    fns = [n for n in ast.walk(ast.parse(inspect.getsource(loop)))
           if isinstance(n, ast.AsyncFunctionDef) and n.name == "agent_loop"]
    body_src = ast.unparse(fns[0])
    assert "from zenagent.agent.tool_exec" not in body_src
```

- [ ] **Step 2: 确认失败** — Run: `uv run pytest tests/test_layering.py -q`;Expected: FAIL
- [ ] **Step 3: 搬三个类到 `protocol.py`**(连带其直接依赖的类型注解;`AgentTool` 字段含 name/description/approval/execution_mode/timeout_s/hidden/default_inactive/run 等,原样搬);loop.py 改 re-export。
- [ ] **Step 4: tool_exec.py 改 import 协议自 `protocol`;loop.py 顶部直 import tool_exec,删注释掉的延迟 import。**
- [ ] **Step 5: 验证** — Run: `uv run pytest -q && uv run ruff check`;Expected: 全 PASS(重点看 tools/mcp/skills 的既有测试)
- [ ] **Step 6: Commit** — `git commit -m "refactor: extract tool protocol from the agent loop"`

### Task 3: usage 聚合四合一

**Files:**
- Modify: `zenagent/cli/rpc/:195-206`(`_usage` 改为调 `usage_total` 后组 dict)
- Modify: `zenagent/cli/render.py:143-158`(`aggregate_usage` 改为 `return usage_total(messages)`)
- Modify: `zenagent/cli/main.py:102-117`(`_usage_and_counts` 改为调 `usage_total`)
- Test: `tests/test_usage.py`(追加)

**Interfaces:**
- Consumes: `zenagent.app.views.usage_total(messages: list[Message]) -> Usage`(:434,唯一权威)
- Produces: 三个包装函数**签名与返回形状不变**(rpc→dict、render→Usage、main→tuple[dict,int])。

- [ ] **Step 1: 追加等价性测试**

```python
def test_all_usage_wrappers_match_usage_total(sample_messages):
    from zenagent.app.views import usage_total
    from zenagent.cli.main import _usage_and_counts
    from zenagent.cli.render import aggregate_usage
    total = usage_total(sample_messages)
    assert aggregate_usage(sample_messages) == total
    d, n = _usage_and_counts(sample_messages)
    assert d == {"input": total.input, "output": total.output,
                 "cache_read": total.cache_read, "cache_write": total.cache_write,
                 "cost": total.cost.total} and n == len(sample_messages)
```

(`sample_messages`:两条 AssistantMessage 带非零 usage,复用 test_usage.py 既有 fixture 形态。)
- [ ] **Step 2: 确认当前已相等(基准测试,应 PASS)** — Run: `uv run pytest tests/test_usage.py -q`
- [ ] **Step 3: 三处改为薄适配**(见上);删除各自循环体。
- [ ] **Step 4: 验证** — Run: `uv run pytest tests/test_usage.py tests/test_rpc.py tests/test_render.py -q && uv run pytest -q`;Expected: 全 PASS
- [ ] **Step 5: Commit** — `git commit -m "refactor: single usage aggregator behind three thin adapters"`

### Task 4: slash 命令词表单一来源(注册表)

**Files:**
- Modify: `zenagent/cli/completion.py`(删除静态 `SLASH_COMMANDS` :33-76 与平行 i18n key 用法;新增 `slash_commands(registry) -> dict[str, str]`,name→`t(command.description)`)
- Modify: `zenagent/cli/repl.py:405`(改为 `commands = {**slash_commands(registry), **(extra_commands or {})}`,registry 从调用点传入)
- Test: `tests/test_completion.py`(重写相关用例)

**Interfaces:**
- Consumes: `app.commands.CommandRegistry.names()`(repl.py:759 已在用)
- Produces: `slash_commands(registry)`;`completion` 模块不再持有任何命令名常量。

- [ ] **Step 1: 重写失败测试**:用 `build_default_commands(...)` 造 registry,断言 `set(slash_commands(registry)) == {f"/{c.name}" for c in registry.names()}`;再造一个注册了自定义命令的 registry,断言新命令出现在补全里(这在旧实现下 FAIL)。
- [ ] **Step 2: 确认失败** — Run: `uv run pytest tests/test_completion.py -q`
- [ ] **Step 3: 实现新函数并迁移 repl.py 调用点;`__all__` 中 `SLASH_COMMANDS` 移除。**
- [ ] **Step 4: 验证** — Run: `uv run pytest tests/test_completion.py -q && uv run pytest -q && uv run ruff check`
- [ ] **Step 5: Commit** — `git commit -m "refactor: derive slash-command completion from the live registry"`

### Task 5: GUI 防线——CSP 与 `rpc_start` 阻塞

**Files:**
- Modify: `desktop/src-tauri/tauri.conf.json:26`(`"csp": null` → 严格策略)
- Modify: `desktop/src-tauri/src/rpc.rs:229-254`(`login_shell_env` 加超时线程)
- Test: `desktop/src-tauri/src/rpc.rs` 内 `#[cfg(test)]`

**Interfaces:**
- Produces(Rust): `fn login_shell_env_with(shell: &str, timeout: Duration) -> HashMap<String, String>`;`login_shell_env()` 变成以 3s 超时调它的薄壳。超时/失败 → 返回空 map 并向 stderr 事件发一条 `login shell env unavailable` 提示(沿用现有 `rpc:stderr` 通道)。

CSP 值(逐字):
`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self' ipc: http://ipc.localhost`

- [ ] **Step 1: Rust 失败测试**

```rust
#[test] fn login_env_fast_shell_returns_map() {
    let m = login_shell_env_with("/bin/sh", Duration::from_secs(3));
    assert!(!m.is_empty());
}
#[test] fn login_env_slow_shell_times_out_to_empty() {
    let m = login_shell_env_with("/bin/sleep", Duration::from_millis(200));
    assert!(m.is_empty());
}
```

(`/bin/sleep` 不产生 `env -0` 输出,触发超时分支。)
- [ ] **Step 2: 确认失败** — Run: `cargo test -p zenagent-desktop` (desktop/src-tauri 下);Expected: 编译错/FAIL
- [ ] **Step 3: 实现 `login_shell_env_with`**:`thread::spawn` + `mpsc::channel` + `recv_timeout`;子线程里保留原 `$SHELL -l -i -c 'env -0'` 逻辑(替换 shell 为参数)。`login_shell_env()` 委托之。
- [ ] **Step 4: 写入 CSP;验证** — Run: `cargo test -p zenagent-desktop && pnpm build && pnpm test && pnpm e2e`(均在 desktop/);Expected: 全绿。另做一次 `pnpm tauri dev` 冒烟:聊天 Markdown、工具卡片、图标渲染正常。
- [ ] **Step 5: Commit** — `git commit -m "fix(desktop): strict CSP and non-blocking login-shell env harvest"`

### Task 6: chatStore 流式 O(1) 定位

**Files:**
- Modify: `desktop/src/state/chatStore.ts:298-310`(text_delta/text_update 分支:`nodes.find` 正向全扫 → 从尾部反向查找)
- Test: `desktop/tests/chatStore.test.ts`(追加行为钉)

**Interfaces:** 无签名变化;`currentAssistantId` 已存在(:120),仅改查找方向。

- [ ] **Step 1: 追加测试**:构造 200 个历史节点后对当前 assistant 连发 3 个 `text_delta`,断言 delta 全部落在最后一个 assistant 节点且渲染 revision 递增(行为不变,防回归锚)。
- [ ] **Step 2: 确认 PASS(基准)后改实现**:反向 `for (let i = nodes.length - 1; i >= 0; i--)`,命中即返回;工具节点继续走 `cards` 索引不动。
- [ ] **Step 3: 验证** — Run: `pnpm test && pnpm e2e`(desktop/);Expected: 全绿
- [ ] **Step 4: Commit** — `git commit -m "perf(desktop): locate streaming assistant node from the tail"`

### Task 7: `crash.py` 接线

**Files:**
- Modify: `zenagent/cli/main.py`(`main()` 入口处 `crash.install_excepthook(...)` try/except 包裹,目录用 `project` 的数据目录;取不到就跳过)
- Test: `tests/` 新增 `test_crash_wiring.py`

**Interfaces:**
- Consumes: `zenagent.crash.install_excepthook(directory: Path | None = None) -> Path`(:53,已存在,无人调用)

- [ ] **Step 1: 失败测试**:`monkeypatch zenagent.cli.main._install_crash_hook`,断言 `main(["--version"])`(或最小解析路径)调用了它一次且异常被吞;直接调 `install_excepthook(tmp_path)` 断言返回路径存在。
- [ ] **Step 2: 确认失败 → 在 `main()` 最前加 `_install_crash_hook()`(模块级薄函数,内部 try/except Exception: pass)**
- [ ] **Step 3: 验证** — Run: `uv run pytest tests/test_crash_wiring.py -q && uv run pytest -q`(注意 release-check 的"bare zen 不崩"语义仍成立)
- [ ] **Step 4: Commit** — `git commit -m "fix: install the crash-log excepthook at CLI startup"`

---

## Phase P1

### Task 8: RPC 协议握手 + 双端契约测试

**Files:**
- Modify: `zenagent/cli/rpc/`(模块级 `PROTOCOL_VERSION = 2`;`_handle_line` 分派最前处理 `hello` → result 带 `protocol_version`)
- Modify: `desktop/src/rpc/types.ts`(导出 `PROTOCOL_VERSION = 2` 与 `HelloRequest`/`HelloResult` 类型)、`desktop/src/rpc/client.ts`(`connect` 后先发 `hello`,版本不符 → 抛带双端版本的错误,由 appStore 走现有 `startFailure` 卡片)
- Modify: `desktop/e2e/app.spec.ts` `installBridge`(:96-354)(mock 响应 hello)
- Create: `tests/test_rpc_contract.py`

**Interfaces:**
- Produces: 请求 `{"type":"hello"}` → `{"type":"result","id":...,"protocol_version":2}`;契约测试成为后续所有协议改动的门。

- [ ] **Step 1: 失败契约测试**

```python
import re, pathlib
RPC = pathlib.Path("zenagent/cli/rpc/").read_text()
TS = pathlib.Path("desktop/src/rpc/types.ts").read_text()

def test_request_kinds_match_ts_union():
    py = set(re.findall(r'kind == "([a-z_]+)"', RPC))
    ts = set(re.findall(r"type:\s*'([a-z_]+)'", TS))
    assert {"hello"} <= py
    assert py == ts, f"drift: py-ts={py-ts} ts-py={ts-py}"

def test_protocol_version_pinned_both_sides():
    assert "PROTOCOL_VERSION = 2" in RPC
    assert "PROTOCOL_VERSION = 2" in TS
```

- [ ] **Step 2: 确认失败 → 实现 Python `hello` 分支与 TS 常量/握手 → e2e mock 加 hello 响应**
- [ ] **Step 3: 验证** — Run: `uv run pytest tests/test_rpc_contract.py tests/test_rpc_v2.py -q && uv run pytest -q`;desktop/ 下 `pnpm test && pnpm e2e`;Expected: 全绿
- [ ] **Step 4: Commit** — `git commit -m "feat(rpc): protocol version handshake + two-sided contract test"`

### Task 9: 统一前端装配 `app/bootstrap.py`

**Files:**
- Create: `zenagent/app/bootstrap.py`
- Modify: `zenagent/cli/main.py`(TUI 内联装配 :639-668 与 headless :188-236 的装配段)、`zenagent/cli/repl.py:461-654`(装配段)、`zenagent/cli/rpc/:1029-1104`(装配段)
- Modify: `zenagent/cli/tui/run.py`(改为调 `prepare()` 的 `run_tui`,消灭 main.py 内联副本)
- Test: 新增 `tests/test_bootstrap.py`

**Interfaces:**
- Produces: `@dataclass PreparedFrontend: runtime: Runtime; chat: ChatController; commands: CommandRegistry; broker: PermissionBroker`;`def prepare(mode: Literal["repl","tui","rpc","headless"], *, cwd: Path, read_only: bool = False, allow_missing_key: bool = False, cli_options: RuntimeOptions | None = None) -> PreparedFrontend`
- 内部顺序固定:broker → RuntimeOptions(mode 差异只进 options)→ `Runtime.bootstrap` → ChatController → AppServices → `build_default_commands`。**交互回调(`set_confirm`/`set_ask_handler`,Task 10 前仍在)由各前端在 prepare 返回后自行挂,不进 prepare。**

- [ ] **Step 1: 失败测试(重点:read_only 四模式全透传)**

```python
@pytest.mark.parametrize("mode", ["repl","tui","rpc","headless"])
def test_read_only_forwarded_in_every_mode(mode, tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(Runtime, "bootstrap",
                        lambda cls, **kw: seen.update(kw) or _fake_runtime())
    prepare(mode, cwd=tmp_path, read_only=True)
    assert seen["read_only"] is True
```

(`_fake_runtime` 给出最小可用替身;ChatController/AppServices 同法 monkeypatch。)
- [ ] **Step 2: 确认失败 → 实现 `prepare()` → 四个调用点迁移(各自 diff 保持行为等价:TUI 传 `allow_missing_key=True` 等,逐条对照现装配段)**
- [ ] **Step 3: 验证** — Run: `uv run pytest -q && uv run ruff check`;REPL/TUI/RPC 三个模式各手工冒烟一条 `-p "hi"`/`zen rpc`/TUI 启动
- [ ] **Step 4: Commit** — `git commit -m "refactor: one frontend bootstrap for repl/tui/rpc/headless"`

### Task 10: 全局钩子依赖注入化

**Files:**
- Modify: `zenagent/tools/ask.py`(删除 `_handler`/`set_ask_handler`/`get_ask_handler` :64-78;`build_ask_tool(handler: AskHandler)` 注入)、`zenagent/tools/task.py`(`set_subagent_observer` :208 同法,observer 进 TaskManager 构造参数)
- Modify: `zenagent/app/runtime.py`(装配点 :604-622 传 handler:REPL/TUI 传 broker 系回调,RPC 传 server.ask;observer 传 subagent tracker)
- Modify: 三个前端的 `set_ask_handler(...)` 调用点(`repl.py:942/1038`、`rpc.py:1104/1108`、`tui/app.py:392/399`)删除
- Test: `tests/test_ask.py`、`tests/test_task_tool.py` 追加

**Interfaces:**
- Produces: `build_ask_tool(handler)`;`TaskManager(observer=...)`。模块级可变态归零——同进程可并存两个不同 handler 的 Agent。

- [ ] **Step 1: 失败测试**

```python
def test_two_agents_can_use_different_ask_handlers():
    t1 = build_ask_tool(handler=lambda q: {"a": "1"})
    t2 = build_ask_tool(handler=lambda q: {"a": "2"})
    assert run_tool_once(t1)["a"] == "1" and run_tool_once(t2)["a"] == "2"

def test_ask_without_handler_errors_not_hangs():
    t = build_ask_tool(handler=None)
    result = run_tool_once(t)
    assert result.is_error and "no ask handler" in result.text.lower()
```

- [ ] **Step 2: 确认失败 → 实现 DI;全局函数删除(import 它们的测试同步改)**
- [ ] **Step 3: 验证** — Run: `uv run pytest -q && uv run ruff check`;REPL/TUI/RPC 各冒烟一次 ask 流程(如 `/model` 触发确认)
- [ ] **Step 4: Commit** — `git commit -m "refactor: inject ask/subagent-observer handlers instead of module globals"`

### Task 11: `ToolCallView` 显式判别字段

**Files:**
- Modify: `zenagent/app/views.py`(各 view dataclass 增加字面 `kind` 字段或在 `to_wire` 输出注入 `"kind"`)
- Modify: `desktop/src/rpc/types.ts`(同步)、`desktop/src/state/chatStore.ts:158-160`(viewToNode 用 `view.kind`,删 `'kind' in view` 启发式)、`desktop/e2e/app.spec.ts` mock
- Test: `tests/test_rpc_contract.py`(追加 kind 集合断言)+ `desktop/tests/chatStore.test.ts`

**Interfaces:**
- Produces: 每个工具视图 wire 对象含 `kind: "diff" | "terminal" | ...`(以 views.py 实际变体为准,实现时枚举);契约测试钉住 Python kind 集合 == TS kind 集合。

- [ ] **Step 1: 失败测试(契约文件追加)**:从 `views.py` 源码正则抽 kind 字面量,与 types.ts 抽取结果断言相等且非空。
- [ ] **Step 2: Python 侧注入 kind → 契约测试过 → TS types/chatStore/mock 同步改 → chatStore 行为测试全绿**
- [ ] **Step 3: 验证** — Run: `uv run pytest -q`;desktop/ `pnpm test && pnpm e2e`
- [ ] **Step 4: Commit** — `git commit -m "refactor(rpc): explicit kind discriminator on tool views"`

### Task 12: `tools/task.py` 拆出子代理运行时

**Files:**
- Create: `zenagent/agent/subagents_runtime.py`(搬 `TaskManager` :280、超时看门狗 :573、子 Agent 构造 :710、transcript 保留策略)
- Modify: `zenagent/tools/task.py`(只留 AgentTool 面 + 薄委托;目标 <300 行)
- Modify: import 站点(grep `from zenagent.tools.task import`,重点 `app/runtime.py`、`agent/subagents.py`)
- Test: 迁移 `tests/test_task_tool.py` 中 TaskManager 直测用例 → 新 `tests/test_subagents_runtime.py`

**Interfaces:**
- Produces: `zenagent.agent.subagents_runtime.TaskManager`(签名不变);`tools/task.py` 仅依赖 protocol(Task 2 产物)。

- [ ] **Step 1: 先搬测试文件并在新路径跑绿(字符级搬移,先证行为)→ 确认旧路径 import 失败**
- [ ] **Step 2: 移动实现;tools/task.py 保留工具描述、参数校验、结果格式化**
- [ ] **Step 3: 验证** — Run: `uv run pytest -q && uv run ruff check && wc -l zenagent/tools/task.py`;Expected: 全绿且 task.py <300
- [ ] **Step 4: Commit** — `git commit -m "refactor: split subagent runtime out of the task tool"`

### Task 13: 事件循环异步 IO

**Files:**
- Modify: `zenagent/http_client.py`(新增 `get_shared_async_client() -> httpx.AsyncClient`,惰性进程级单例)
- Modify: 四适配器的 per-call client(`openai_completions.py:639`+`:650`、`anthropic_messages.py:542`、`google_generative_ai.py:242`、`openai_responses.py:292`)改用共享 client,删 `aclose()`
- Modify: `zenagent/tools/fs.py:172`(`await asyncio.to_thread(path.read_bytes)`)、`zenagent/agent/session.py:399`(append 块体 `await asyncio.to_thread(...)`,保持逐条 await)
- Test: `tests/test_session.py` 追加并发顺序用例;`tests/test_http_client.py`(若已有则追加)

**Interfaces:**
- Produces: `get_shared_async_client()`;session append 顺序语义不变。

- [ ] **Step 1: 失败测试**

```python
async def test_concurrent_appends_preserve_order(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    await asyncio.gather(*(s.append(Msg(i)) for i in range(100)))
    lines = (tmp_path / "s.jsonl").read_text().splitlines()
    assert [json.loads(l)["i"] for l in lines] == list(range(100))

def test_shared_async_client_is_singleton():
    assert get_shared_async_client() is get_shared_async_client()
```

- [ ] **Step 2: 确认失败 → 逐点实现(Session.append 若为同步方法,先确认调用链再定 to_thread 边界,不许把同步 API 改成阻塞调用)**
- [ ] **Step 3: 验证** — Run: `uv run pytest -q && uv run ruff check`
- [ ] **Step 4: Commit** — `git commit -m "perf: shared httpx client and off-loop file IO"`

---

## Phase P2(卫生,可在 P0/P1 后任意排期)

### Task 14: 门面导出与过期注释清理

- [ ] `zenagent/__init__.py` 增加 re-export:`Agent, AgentConfig, AgentState, AgentTool, agent_loop, StreamOptions, register_stream_fn`(+`__version__`);测试:`test_layering.py` 追加 import 可用性断言。
- [ ] 删 `zenagent/prompts/__init__.py:88-89` 的"避免 import cycle"注释(providers.limits 不 import prompts,理由不成立),延迟 import 保留(启动减重)。
- [ ] 删 `zenagent/app/commands.py:196` 的重复惰性 `session_title` import(:10 已模块级导入)。
- [ ] 验证 `uv run pytest -q && uv run ruff check` → Commit `chore: facade exports and stale comment cleanup`

### Task 15: LOC 预算机械门

- [ ] Create `scripts/check_architecture.py`:规则=新 Python 文件 ≤800 行;allowlist(JSON 内嵌或 `docs/architecture/allowlist.json`)登记现有大文件及其当前行数(只许降);TS 新文件 ≤800、`desktop/src/ui/Sidebar.tsx`、`state/appStore.ts` 等入 allowlist。超限 exit 1 并打印超标文件。
- [ ] 挂进 `scripts/release-check.sh` 与 `.github/workflows/ci.yml`(跟 ruff 同级一步)。
- [ ] 测试:对临时文件树跑单测(标准库 subprocess 自举,不引第三方);验证 CI 本地 `python3 scripts/check_architecture.py` exit 0。
- [ ] Commit `chore: LOC budget gate with explicit debt allowlist`

### Task 16(可选,最后做): 神文件机械拆分

- `cli/tui/screens/modals.py` → `screens/modals/` 包,一类一文件(13 类 + `preview_diff` 归 textutil);`tests/test_tui_screen_registry.py` 必须保持绿。
- `zenagent/cli/rpc/` → `cli/rpc/` 包:`codec.py`(serialize_event 及投影)、`server.py`(RpcServer)、`cli.py`(argparse main);`zenagent/cli/rpc/` 变薄 re-export。契约测试(Task 8)与 `test_rpc_v2.py` 全程绿。
- `cli/repl.py` 拆出 `cli/repl_render.py`(`_EventPrinter`、screen 文本渲染 :94-280)与 `cli/repl_io.py`(单 stdin 协议 :764-995),并消灭 :579 与 :726 的重复 reload 块。
- 每个子步独立 commit;完成后跑 Task 15 门,从 allowlist 删除已达标条目。
- Commit `refactor(cli): mechanical god-file splits`

## Self-Review 记录

- 覆盖:review §六 P0-1..7 → Task 1/2/3/4/5/6/7;P1-8..13 → Task 8..13;P2-14..16 → Task 14..16。无缺项。
- 类型一致性:`prepare()`/`build_ask_tool(handler)`/`login_shell_env_with(shell, timeout)`/`get_shared_async_client()` 在产出与消费任务的 Interfaces 里同名同签名。
- Review Focus 五条各有归属任务与测试(8/9/10/13/5)。
- 比例:步骤只含签名、测试断言与验证命令,无实现正文转写。
