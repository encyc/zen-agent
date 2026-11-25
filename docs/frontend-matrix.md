# 四端能力矩阵

> **维护型文档**：回答「哪个前端有哪些能力」。与 [feature-map.md](feature-map.md) 互补——那篇回答
> 「功能在哪实现、怎么测」，本篇回答「tui / gui / repl / headless 各自有什么」。
>
> - 最近盘点：2026-11-17（GUI v0.13.11，协议 v3；同日 TUI 对齐 Phase A–E 合入：
>   `/secrets` `/plugins` `/limits` `/runs` `/worktrees` 与 goal 详情面板）
> - 图例：`●` 完整 · `◐` 部分（文本降级 / 仅启动期 / 子集）· `—` 无
> - 维护规则见文末。

## 架构拓扑

四端不是四个平级 UI，而是两种接入方式：

```
headless ──┐                                 one-shot，非交互 PermissionGate
REPL ──────┼── app/bootstrap.prepare() ──► app 层（Runtime/ChatController/CommandRegistry）
TUI ───────┘        （进程内直连，无 RPC）        │
GUI(Tauri) ── zen rpc sidecar(JSONL) ──► RpcHost(多项目) ──► RpcServer(slot 级)
                                           协议 v4
```

- **headless**：`cli/main.py::run_headless`，一次 prompt 进、流式文本出、退出码出。`--json`
  回单对象（`ok/text/usage/messages/error/termination`），被 `tests/test_cli.py` 钉住。
- **REPL**：`cli/repl.py::repl`，进程内直连，共享 `CommandRegistry`（`app/commands.py::build_default_commands`，
  自述 "shared by REPL, TUI and future frontends"）。
- **TUI**：`cli/tui/`（Textual），同样进程内直连；审批走 `broker.set_confirm`，ask 走
  `runtime.ask_handler`。
- **GUI**：`desktop/`，后端是 `zen rpc` 子进程（`desktop/src-tauri/src/rpc.rs`）。RPC 服务端两层：
  slot 级 `cli/rpc/server.py::RpcServer`（单会话协议），宿主级 `cli/rpc/host.py::RpcHost`
  （多项目路由 + runs/terminal）。

## 能力矩阵

| 能力域 | TUI | GUI | REPL | headless | 备注 |
|---|:-:|:-:|:-:|:-:|---|
| 多轮对话 + 流式 | ● | ● | ● | ◐ | headless 单轮输入 |
| steer（运行中插话） | ● | ● | ● | — | |
| follow_up 通道 | ● | ● | — | — | REPL 唯一缺的输入 lane |
| 队列管理（排队/晋升/取回） | ● | ● | — | — | |
| 工具审批 | ● | ● | ◐ | ◐ | REPL 是 y/n 行协议；headless 只能 `--yes` 或被拒 |
| ask 问答 | ● | ● | ● | — | |
| 共享 slash 命令（约 49 个） | ● | ● | ● | — | GUI 经 `command` wire op |
| 模板 `/name` · `/skill:` | ● | ◐ | ● | — | headless 强制关 templates |
| session 切换/管理 | ● | ● | ◐ | ◐ | REPL picker 只列不切；headless 仅 `--resume` |
| undo / compact | ● | ● | ● | ◐ | headless 仅运行后 auto-compact |
| retry（回合级） | ● | ◐ | ● | — | GUI 有 `session.retry` op 未接 UI |
| 模型/mode/thinking 切换 | ● | ● | ● | ◐ | headless 启动 flag 固定 |
| usage / cost | ● | ◐ | ● | ◐ | GUI 从 status 附带读 |
| goal | ● | ● | ● | — | TUI 另有 GoalHud 点击的详情面板 |
| todo | ● | ● | ◐ | — | REPL 只读列表 |
| memory 管理 | ● | ● | ● | ◐ | headless 只读入上下文 |
| skills / plugins / secrets 管理面 | ● | ● | ◐ | ◐ | TUI 有 `/secrets` `/plugins`（+`/skills`）；REPL 文本列表 |
| mcp 管理 | ● | ● | ◐ | ◐ | REPL 无 add 向导渲染 |
| limits 编辑 | ● | ● | ◐ | ◐ | `/limits` 面板/文本列表；headless 仅启动 flag |
| fs 文件浏览 | — | ● | — | — | |
| git worktree 总览 | ● | ● | ◐ | — | TUI `/worktrees` 只读屏 + REPL 文本 |
| 内置 terminal | — | ● | — | — | TUI/REPL 本身就是终端，by design 不做 |
| subagent 查看 | ● | ● | ◐ | — | REPL 仅 `/jobs` 列表 |
| runs 历史账本 | ● | **—** | ◐ | — | TUI `/runs`（四端第一个视图）；GUI 待接同一 op |
| 多项目驻留 | — | ● | — | — | TUI/REPL 单进程单 cwd |

## RPC op 骨架（GUI 能力的来源）

slot 级 kind（`RpcServer`）：`hello` `prompt` `cancel` `status` `shutdown` `steer` `follow_up`
`tools` `models` `commands` `transcript` `subagent_transcript` `model` `mode` `usage` `approval`
`ask` `session` `command` `fs_list` `fs_read` `fs_index` `memory` `git_worktrees` `secrets`
`skills` `prefs`/`set_pref` `limits` `plugins` `mcp` `project`

宿主级 kind（`RpcHost`）：`project` `terminal` `runs`

二级 action（按域）：

| 域 | actions |
|---|---|
| `session` | current · list · new · fork · resume · import · undo · delete · compact · retry · export · promote · dequeue · interrupt_pending · clear_continuations · clear_pending · clear_todos |
| `runs` | list · get |
| `project`(host) | open/switch · close · relocate |
| `terminal` | start · write · resize · close |
| `memory` | list · create · update · delete |
| `skills` / `mcp` | list · rescan/read/toggle / add/remove/test |
| `secrets` / `limits` | list · set · clear/test / get · set · reset |
| `plugins` | list · install · uninstall · enable · disable · configure |

契约钉在 `desktop/src/rpc/types.ts::REQUEST_KINDS` 与 `tests/test_rpc_contract.py`。

## 缺口分级

### P0 · 服务端硬缺口（任何端想做都没有 wire 面）

1. **`app/metrics.py::MetricsSession` 零暴露**——只写本地文件，无读取 op。
2. **`app/rewind.py` checkpoint 无 wire 面**——纯模型侧工具。
3. **`app/slash_args.py` 的 argument_values 未暴露**——GUI/REPL 命令参数补全只能硬编码。

### P1 · 端级缺口（app 层/RPC 已就绪，端没接）

1. runs 视图：TUI 已做（`/runs`，四端第一个）；GUI 待接同一 `runs` op。
2. GUI 未接的现成 op：`session.retry`、`usage`、`tools`。
3. REPL：follow_up、队列管理、session picker 只列不切、todo 只读、mcp 向导无渲染、settings 只读
   （`/secrets` `/plugins` `/limits` `/runs` `/worktrees` 已有文本形式）。

### P2 · by design 不补

headless 的审批/steer/follow_up/slash 缺失是 one-shot 契约（无 `--yes` 时写操作直接拒，
`app/permissions.py` 的非交互 gate）。需要交互自动化的出口是 `zen rpc`，不是给 headless 加交互。
terminal 面板对 TUI/REPL 无意义。

## 各端独有能力（向其他端对齐时的候选）

- **TUI**：多 lane 输入细节（双 Esc 回滚、alt+up 取回队尾）、subagent 换装视图、后台 job 日志浮层、
  work strip、状态带 segment/preset 系统、可拖宽侧栏（8 section）、context breakdown 屏、
  命令/skill/参数三合一 hint 条、ctrl+g 外部编辑器、剪贴板路径附图、`/shake` `/btw` `/handoff`
  `/urls` `/copy` `/open` `/add-dir`（命令本身 REPL 共享，呈现是 TUI 独有）。
- **GUI**：terminal/files/review 面板、minimap、应用内更新、多项目驻留、
  ⌘K 命令面板、transcript 挂载窗口分页。
- **REPL**：legacy 主题名兼容、`mcp retry` 特判（小件）。
- **headless**：`--json` 单对象契约与退出码语义（CI/evals 入口，`scripts/eval.py` 镜像此契约）。

## 维护规则

1. **新增功能时加一行**：功能进了 app 层或任一端，矩阵加行/更新格；顺手更新「最近盘点」日期。
2. **以代码为准**：格子的证据是符号名（文件 + 函数/类），不贴行号——与全 docs 约定一致。
3. **大盘点节奏**：建议每次 minor 发版前对照 `RpcServer`/`RpcHost` dispatch 链和
   `build_default_commands` 重扫一遍。
4. **缺口消化后降级**：P0/P1 条目完成时移入矩阵（改格），不留在缺口清单里积灰。
