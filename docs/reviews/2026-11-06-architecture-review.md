# 代码架构审查（2026-11-06，`desktop/` 除外）

范围：`zenagent/`、`tests/`、`scripts/`、`docs/`。`desktop/` 是独立的 TypeScript 应用，不在本次范围。

**基线证据**（审查当时）：`uv run pytest -q` → 2191 passed（135s）；`uv run ruff check` → All checks
passed；`git log` 显示非 desktop 最近的改动集中在会话侧信道记录（`32204d1`→`e0d6157`）、RPC 快照
（`cb8ebbd`）、headless 归并（`0dfd3f5`）。

**方法**：按层派出 5 个只读评审员（内核 / tools / app / 前端 / 门禁与文档），各自带契约基线，
回来后逐条复核所有 Critical 与关键 Important。标记 ✅ 的条目由审查者本人读码或跑验证脚本确认。

规模：≈37k 行 Python。最大文件 `cli/repl.py 1054`、`cli/tui/screens/modals.py 1042`、`cli/rpc.py 993`、
`app/commands.py 869`、`tools/task.py 847`、`app/runtime.py 831`、`agent/loop.py 815`。

---

## 总体结论

内核（`types/events/agent/providers`）的**依赖方向是干净的**：全仓 AST 扫描只发现一处跨层反向 import
（`app → cli`）。但**概念不干净**——工具名表、权限档位、bash 语法、app 的记录类型、进程 cwd 都以常量
形式漏进内核。

真正的架构缺口在 `zenagent/app/`：7,085 行、29 个模块、四个前端的唯一汇聚点，**却在
`docs/architecture.md` 里完全不存在**（文档仍写"五层"，且把权限门指向一个 16 行的 shim）。
最近的会话记录改动正压在这个缺口上：记录词汇是 6 个裸字符串散在 3 层 6 个文件里，
`tool_end` 由 UI 控制器写（于是 headless 会话格式不同），内核 `carry_records` 反过来要认识 app 的记录种类。

---

## Critical

### C1. 截断的工具调用会被执行（违反契约 §5）✅

- `providers/google_generative_ai.py:209-210`：`_STOP_REASONS` 正确把 `MAX_TOKENS → "length"`
  （`google_generative_ai.py:37`），但下一行 `partial.stop_reason = "tool_use" if has_calls else stop_reason`
  无条件覆盖。`agent/loop.py:678` 的保护分支 `if final.stop_reason == "length" and calls:` 对该适配器
  因此**永远不可达**，半解析的参数被当作真实调用执行。
- `providers/openai_responses.py:256-262`：同一形态，`status="incomplete"` 且有 calls 时被标成 `tool_use`。

后果：这是全仓唯一"契约违例会产出错误副作用"的地方（模型输出的 JSON 被截断 → 参数残缺 → 仍然执行）。
修法：只在映射结果为 `"stop"` 时才合成 `"tool_use"`；补 `MAX_TOKENS/incomplete + functionCall` 的 fixture。

### C2. 子 agent 完全没有权限门（`task` 是 policy 的逃生舱）✅

`tools/task.py:703-716` 的 `_construct()` 只向 `Agent(...)` 传 10 个参数，**不传 `before_tool_call`**
（`Agent` 支持，见 `agent/agent.py:85`；父进程在 `app/runtime.py:431` 装了 `gate.before_tool_call`）。
`child_allowed(..., zen=True)` 在 zen 模式放行全部工具（`tools/task.py:70-78`），而 `deny` 模式、
灾难命令拦截（`dangerous_command_reason`）、受保护路径全在门里。

后果：zen 会话派生的子 agent 跑 `bash` 时没有任何策略检查；`tool_deny` 配置对所有模式在子 agent 内失效。
修法：`build_task_tool(..., before_tool_call=gate.before_tool_call)` 并透传进 `_construct`。

### C3. 会话记录没有 schema 归属，且跨层泄漏 ✅

`"tool_end"` 写于 `app/session_meta.py:82`、过滤于 `agent/session.py:570`、解码于 `app/views.py:285`；
`"compaction"` 写于 `app/compact.py:162`、内核 `session.py:568` 携带、`cli/rpc.py:497` 解码；
`run_start/run_end/retry/mode` 又在 `session_meta.py:118-156` 内联构造。

后果：`59bb511`、`415f69e` 两个提交都因为"加一个记录类型"被迫改 6~13 个文件；内核每认识一种
harness 记录类型就要改一次 `carry_records`。修法：`app/records.py`（常量 + 类型化构造器 + 谓词），
内核改为 `carry_records(messages, keep: Callable[[dict], bool])`。

### C4. `tool_end` 由 `ChatController` 生产 → headless 会话格式不同

`tool_end_record` 的唯一调用点是 `app/chat.py:611`；其余侧信道记录都由 `Runtime.bootstrap` 的订阅者
写（`app/runtime.py:451-466`）。`zen -p` 不构造 `ChatController`（`cli/main.py` 中 grep 不到），
所以 `-p` 会话写 `run_start/run_end/retry/mode` 而**不写 `tool_end`**：同一格式，内容依前端而异。

修法：把 append 移进 Runtime 订阅者（扩展 `SessionMetaRecorder` 处理 `tool_execution_end`）。

### C5. `app/` 反向 import CLI，且没有任何测试钉住这条规则 ✅

`app/commands.py:631` 在函数体内 `from zenagent.cli.tui.clipboard import write_clipboard_text`
（该模块只用 `shutil`/`subprocess`，是放错了包）。`app/__init__.py:1-4` 明文禁止此方向。因为在函数体内，
AST 级检查也不会响；`tests/` 里没有任何 layering 测试。更糟：`tests/test_app_commands.py:204,215,316,332,356`
与 `tests/test_tui_links.py:17` 把测试钉在这个 CLI 路径上。

修法：搬到 `app/clipboard.py`，改 import 与 6 处测试引用，加 `tests/test_layering.py`。

---

## Important

### 内核生命周期与可观测性

- **I1** `agent/loop.py:800-808` 在 `finally` 里 `yield agent_end`：任何"只消费几个事件就 `aclose()`"的
  嵌入方会得到 `RuntimeError: async generator ignored GeneratorExit`（`docs/architecture.md:60-64`
  把"嵌入"列为 loop 的用途之一）。同处无条件 `state.termination = termination`，被取消的 run 记成
  `"stop"`；`agent/agent.py:276-283` 在 `except BaseException` 里设的 `_error_message` 立刻被
  `finally` 覆盖成 `None` → harness 无法区分用户中止与正常结束。
- **I2** 内核的 policy 漏点（三处同源）：`agent/timeouts.py:36-41` 硬编码工具名→超时表；
  `loop.py:456-465` 用 `approval == "write"` 当并发调度信号；`loop.py:530-547` 在核心里解析 bash
  轮询命令。修法统一：声明放回 `AgentTool` 字段（`timeout_s` / `exclusive` / `poll_like`）。
- **I3** `loop.py:746-751` 把 `details`（文档写明"只给 UI，永不进模型"）当控制通道读 `subagent_cost`；
  且 `AgentState.subagent_spend` 活在整个进程，第 N 轮的 `max_cost` 已包含前几轮子 agent 开销。
- **I4** `agent/agent.py:249-261` 的 `_dispatch` 把所有监听器异常吞成 stderr `print`，包括唯一的
  转录写入者（`app/runtime.py:450-459` 的 `persist` 订阅者）→ 磁盘满会静默丢消息，run 继续。
- **I5** `loop.py:533-537` + `803-807` 用文本前缀识别 loop-guard 合成消息，用户在提示里带
  `[tool-loop guard]` 前缀时会被从 `state.messages` 删掉（文件里仍有、上下文里消失）；
  `app/views.py:315` 又单独复刻一次同样的前缀判断。

### 子 agent 生命周期

- **I6** `Runtime.close()` 释放 jobs 却**从不释放 task manager**（`close_task_manager` 零生产调用者），
  `TaskManager` 没有 `kill`，`[task].timeout_s` 默认 900s → 逃逸的后台子 agent 无处可停。
- **I7** `tools/task.py:280-390` 的 `TaskManager` 是 `JobManager` 的手抄兄弟（同款
  start/wait/close/_abandon/_monitor/_retire + 重复的 `Unknown*Error`）；`tools/wait.py:200` 靠
  `job_id.startswith("task_")` 区分两个 id 空间；`_inflight_task_ids` 在 `app/chat.py:48`、
  `app/sessions.py:38`、`app/subagent_tracker.py:139` 抄了三份。
- **I8** spend 只在父模型 `wait` 时结算（`tools/wait.py:159-168`）→ k 个并行异步子 agent 各自拿满
  预算；模型从不 `wait` 时记得 0。
- **I9** `app/runtime.py:568-576` 的自定义工具分支提前 `return`，`tool_registry.active()` 仍为空 →
  `task` 派生的子 agent 工具集为 `[]`，静默成功（`run_headless(tools=...)` 是公开入口）。

### 前端的重复实现（4 个前端 × app 层的"决策"）

- **I10** 用量聚合 4 份：`app/views.py:391 usage_total`（正牌）、`cli/render.py:143 aggregate_usage`、
  `cli/rpc.py:158 _usage`、`cli/main.py:103 _usage_and_counts`；后三者已把 `Cost` 压成 float。
- **I11** Runtime 装配（`RuntimeOptions` + `GuardPolicy` + `build_permission_gate` + `PermissionBroker`）
  在 `cli/main.py:605`、`cli/repl.py:466`、`cli/rpc.py:838` 各写一遍；`bootstrap` 收到 gate 时仍先
  构造一个 `GuardPolicy`（`app/runtime.py:319-338`）。分叉已发生：只有 RPC 传 `read_only=`。
- **I12** RPC `_status_result`（`cli/rpc.py:428-465`）手抄 `RunStatus` 31 个字段中的 18 个，漏
  `git_branch/git_staged/git_dirty/git_untracked` 与整个 `ContextReport`；没有任何测试钉住清单。
- **I13** REPL 不用共享 reducer：自己 subscribe + `render.AssistantLiveRenderer`（`repl.py:751-753`），
  于是 `views.RunStatus`（23 字段）与 `render.status_line` 两套状态行并行维护。
- **I14** 斜杠命令优先级四命名空间各前端自判：REPL 让模板盖过内置（`repl.py:1013`），TUI 内置优先
  （`tui/app_parts/drafts.py:45`）；REPL 小写化命令名、TUI 不 → `/HELP` 行为不同；RPC 没有 extension 桥。
- **I15** 会话切换的副作用（`carry_task_dir` / `todo_tool.bind_session` / `guard.last_usage`）在 8 个
  调用点各记各的；`/shake`、`/handoff`、`SessionManager.undo` 直接赋值 `runtime.session` 绕过
  `Runtime.switch_session`（`commands.py:718,800`、`sessions.py:241`）。
- **I16** 审批提问两套：`app/permissions.py:231 render_approval_prompt` 与
  `tui/screens/modals.py:514-598` 各自从 `Verdict` 推导风险行，i18n 键重复两份
  （`en.py:148-151` 与 `en.py:455-457` 逐字相同）。
- **I17** headless 自己实现自动压缩（`cli/main.py:240-243` 调 `app/compact.maybe_auto_compact`，
  唯一调用者），不走 `chat.compact_now` → 压缩后 `runtime.session` 仍指向旧文件。
- **I18** RPC `status` 之外还有 `_session`（`rpc.py:660-740`）重写 new/fork/resume/undo/delete/
  compact/retry/export 八个操作，绕过 `CommandRegistry`，所以同一动作两个返回契约。

### God 模块

- **I19** `Runtime`：`__init__` 42 个 `self.*`，`bootstrap` 195 行装配脚本（catalog→model→key→policy→gate
  →session→jobs→MCP→skills→templates→extensions→guard→agent→task 接线→订阅→持久化→mode）。
- **I20** `CommandRegistry`（869 行 / 48 命令）越权：`_shake`、`_handoff` 自己 `compact()` 并直接
  赋值 `runtime.session`；`AppServices` 只有 4 个字段，handler 却伸进 `runtime.agent`（8 次）、
  `runtime.extra_dirs`（5 次）、`runtime.journal/mcp/memory_store`。
- **I21** `tools/task.py`（847）一个文件里塞 schema + 子能力策略 + 调度看门狗 + 转录持久化 +
  envelope 展示 + 任务管理器；`agent/session.py`（653）同时管格式、会话库（list/latest/titles/pins
  侧车）与展示（`brief`、标题推导）；`agent/loop.py`（815）除轮次机外还持有工具调度与 guard 启发式。
- **I22** `tools/` 没有共享构造助手：`_text` **9 份逐字相同**（`fs/bash/search/wait/ask/memory/lsp/
  github/web/tools`），`_clip` 4 份，超时夹紧 3 份；截断提示有 5 套说法（内核 40KB / bash 30KB /
  read_file 5000 行 / fetch / skills），"统一预算"只在字节层成立。

### 注册与装配

- **I23** 两份"内置工具清单"：`tools/__init__.py:3` 说"新工具加这里"，但 `runtime._recompose`
  （`app/runtime.py:580-597`）又追加 checkpoint/github/lsp/memory×2/todo/task/mcp/skills。
  `ALL_TOOLS` 只有 11 个，实际 20+。`build_task_tool(base_tools=list(ALL_TOOLS))` 是缺 8 个工具的
  过期回退；工具在 prompt 里的顺序取决于你选了哪张表。
- **I24** `agent/registry.py:105-112` `register` 后注册覆盖先注册，MCP/扩展最后 reconcile →
  MCP server 的同名工具会静默顶掉 `bash`/`read_file`，而扩展**命令**那条路径是会 warn 的。

---

## Minor（选定清单）

- `app/__init__.py:7-21` 的 façade 让 import `app.modes` 也要拉起 357 个模块（含 `mcp.manager`、全部
  `tools.*`）；前端应直接 import 具体模块。
- `cli/permissions.py` 只是 16 行 shim，仍被 `tests/test_cli.py:14`、`tests/test_permissions.py:7` 使用。
- `app/support.py:38 system_prompt` 零生产调用者；`app/retry.py` docstring 描述的双前端契约已过期。
- `agent/subagents.py:214` 的 `begin()` 用 `write_text("")` 截断自己的 JSONL，与"只追加"的纪律不一致；
  `SubagentStore._prune` 与 `ArtifactStore._prune` 同一策略写了两遍。
- `tools/fs.py:273-275` 用正则重解析内核的截断提示，`save_artifact` 追加的尾巴会让锚定匹配失效
  （目前靠 32KB < 40KB 的算术巧合兜住）。
- `cli/main.py:281-472` 一个扁平 45 flag 解析器，`-p` 下至少 9 个 flag 被静默忽略
  （`--format/--since/-o/--report/--report-out/--all-projects/--no-completion/--no-tui/--theme/--lang`）；
  `cli/rpc.py:924-971` 是第二份 24 flag 解析器，22 个重复。
- `cli/main.py:522` `zen evals` 从 `Path(__file__).parents[2]/"scripts"/"eval.py"` 加载，wheel 里没有
  `scripts/`；`build_parser` 的 help 仍写"subcommand: doctor, sessions"（实有 6 个）。
- `cli/completion.py:33-78` 的 `SLASH_COMMANDS`（43 条）是 48 条注册表的过期副本。
- 键位声明四处（`cli/keybindings.py`、`tui/app.py BINDINGS`、`APP_CHORDS`、i18n `cmdmsg.hotkeys_text`）。
- `providers/openai_completions.py:59` 的 `StreamOptions` 被 anthropic/google/responses/registry/
  `app/compact`/`app/btw` 以及**内核** `agent/loop.py:46` import → 新增适配器必须先 import OpenAI 模块。
- `providers/registry.py:55` 的 `_REGISTRY` 可变全局，`register_stream_fn` 只有测试在调（补丁不还原会污染
  后续用例）；同类还有 `agent/memory_store.py:61 _LAST_TS`、`tools/task.py:391 _task_manager`。
- `agent/memory_store.py` 的 store 在内核，`tools/todo.py` 的 store 却在工具里（两种约定）。
- `tools/task.py:735-741` 异步分发的 `on_update` 是死参（`loop.py:330` 在 `execute` 返回后立刻关闭
  forwarder）。
- `agent/loop.py:480` 与 `:348` 对同名工具分别"后者胜"和"前者胜"，同名时调度决策与执行对象不一致。

---

## 文档漂移（`docs/` 仍是"宪法"，已 15 处失实；高优先级 6 条）

| 位置 | 声称 | 现实 |
|---|---|---|
| `docs/architecture.md:3,5-21` | "五层"，图里没有 `app/` | `app/` 29 模块 / 7085 行，四个前端的唯一装配路径（`Runtime.bootstrap`） |
| `architecture.md:43`、`internals/providers.md:22` | `providers/retry.py` | 不存在；真实为 `zenagent/retry.py`，且它 import providers → 层级在其**之上** |
| `architecture.md:99`、`reference/file-map.md:46`、`internals/guardrails.md:19` | 权限门在 `cli/permissions.py` | 实现早已搬到 `app/permissions.py`，前者是 16 行 shim |
| `reference/file-map.md:39-52` | CLI 表 12 行，`app/` 只有 1 行（compact） | `app/` 是改动最频繁的层，整张表缺失 |
| `internals/tools-internals.md:96-105` | `task` 同步单发、只读工具集、不落 session | 已支持 `async`+`model`、继承父工具集、写 `tasks/<stem>/<call_id>.jsonl` |
| `docs/testing.md:23,89` | "约 697 条 / 697 passed" | 152 个测试模块 / 2031 个测试函数 / 2191 passed |
| `reference/contracts.md:34` | "loop 里没有任何 try/except 包裹工具执行" | `loop.py:311-330` 有 |
| `reference/contracts.md:52` | `--resume` 只读、不改文件 | `app/runtime.py:466` 会 append `mode` 记录 |
| `contracts.md:38`、`reference/events.md:27` | `StopReason` 五种、`agent_end` 两字段 | 还有 `"pending"`；`agent_end` 另带 `turns`/`termination` |
| `internals/persistence.md:5,9-16` | 一行一消息、`Session` 五个方法 | 有 `type:"session"` 头（`RECORD_VERSION=1`）、侧信道记录、titles/pins 侧车 |
| `feature-map.md:67,68,80` | `widgets/status_bar.py`、`widgets/chat_log.py`、`widgets/clipboard.py` | 实际是 `status_band.py`、`transcript.py`+`rows.py`、`cli/tui/clipboard.py` |

## 门禁缺口

`scripts/check.sh` = sync + ruff + pytest（CI 完全一致，无覆盖率阈值、无文档检查）。以下只有散文、没有测试：

- **分层规则**（`architecture.md:23`、`app/__init__.py:3-4`）：无任何 import 扫描测试 → C5 得以存活。
- **RPC stdout 只走协议**（`contracts.md:80`）：`RpcServer` 用注入的 `read_line/write_line`，测试全用假 I/O，
  没有子进程测试碰 fd 1。
- **`architecture/reference/internals` 三类文档**：唯一被机器钉住的是 `docs/guide/commands.md` 与 README
  （`tests/test_docs_commands.py`）→ 上表 15 处漂移无人拦。
- **记录 schema 前向兼容**：v1 有测试，`RECORD_VERSION=2` 无。
- `publish.yml` 的 release 作业只跑 `release-check.sh --skip-build`，不在 release ref 上跑测试。

可抄的现成范式：`tests/test_prompts_sources.py:44`、`tests/test_tui_theme_css.py:19`（rglob + 断言）。

## 测试覆盖（按模块，节选）

覆盖良好：`app/runtime|commands|journal|permissions|chat`、`cli/rpc`、`cli/tui/**`（39 个文件 / 312 用例）、
`tools/task`、`agent/subagents`。
薄弱：`app/btw.py`（0 测试）、`app/models.py`/`app/git.py`（薄）、`cli/tui/app_parts/*`（只经整体测）、
`scripts/eval.py`（596 行无测）。

---

## 建议的修复顺序

1. **一天内**：C1（两个 `stop_reason` 覆盖 bug + fixture）、C2（子 agent 权限门）、C5（clipboard 搬家 +
   `tests/test_layering.py`）。
2. **一个 PR**：C3 + C4 —— `app/records.py` 记录常量/构造器/谓词，`carry_records(keep=...)`，`tool_end`
   生产者移到 Runtime 订阅者。
3. **一个 PR（纯搬运）**：`tools/_result.py`（`text_result`/`clip`/`clamp_float`）替换 9 份 `_text`；
   `agent/tool_exec.py` + `agent/guard.py` 从 `loop.py` 抽出；`agent/session.py` 拆"格式"与"会话库"。
4. **一个 PR**：`RuntimeOptions.from_namespace(args)` + `Runtime.bootstrap` 独占 gate/broker 构造；
   `RunStatus.to_wire()` 让 RPC/TUI/REPL 共享；RPC 的 session 操作改走 `CommandRegistry`。
5. **文档 + 门禁**：`architecture.md` 增 `app/` 层与"记录由谁生产"的规则；`file-map.md` 补 `app/` 表；
   `tools-internals.md` 重写 task 段；加 `tests/test_docs_paths.py`（断言文档里所有 `zenagent/*.py` 存在）
   与 `tests/test_layering.py`。

批次 1 的可执行计划见 `docs/superpowers/plans/2026-11-06-arch-review-batch1.md`。
