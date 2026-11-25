# 只保留 coding：删除 zen / swarm 两个工作区模式

> 状态：待批准。目标是把 zen、swarm 两套编排器及其控制面从仓库里删干净，只留 coding；
> 同时删掉权限模式里残留的 `zen` 别名。旧数据留在盘上不动。

## 1. 现象与根因

`zen` 与 `swarm` 不在 kernel 里，它们叠在 `RpcHost` 的 slot 体系之上，各自带来一套**并行的
所有权模型**：

- **zen**：每个项目一个 Router slot，Router 会话里长出一支 fleet（worker）；
  离开 zen 要停 fleet、把前台交回进入前的 slot，slot 被 LRU 回收时还要反向拆 zen
  （`host.py::_drop` 里 `was_zen` 分支）。zen 是"塞在会话槽里的第二个调度器"。
- **swarm**：进程级、按项目 key 的房间（`RpcHost._swarm`），房间里是角色进程、任务表、
  消息预算与自己的审批前缀（`<project>:swarm:`），前台切换与 slot 抢占又是另一套。
- 两者共用一层只为它们存在的控制面：`Supervisor` 活体注册表、`dispatch`、
  `supervisor` RPC；以及一层只为它们存在的 wire 家族：`swarm`、`zen_fleet`、
  `supervisor`、`workspace`（模式事实）。
- 两层都把自己写进 run 账本（`zen_router` / `zen_worker` / `swarm`），都往配置里加表
  （`[zen]` 4 个键、`[swarm]` 10 个键），都在桌面长出一整套 store/页面/样式/i18n。

结果是：一个"模式"实际是两套 mini-OS，和 coding 的唯一交集是都跑在 `Agent` 上。
删它们不是删功能开关，是拆掉第二套内核。

## 2. 目标 / 非目标 / 反目标

**目标**

- 仓库里不存在工作区模式概念：没有 `coding/zen/swarm` 三态，没有模式切换器、
  `workspace.json` 模式事实、`workspace` RPC、会话列表的 workspace 归属。
- 删除 zen：`app/zen/`（fleet / roster / tools / identity / prompts）、`zen_fleet` RPC、
  `[zen]` 配置、`.zen.json` 名册读取、worker 权限模式、router 工具与提示词。
- 删除 swarm：`app/swarm/` 全部模块、`swarm` RPC、`[swarm]` 配置、`swarm_settings.py`、
  角色 manifest 与角色库、房间/任务/消息、`swarm_protocol` 提示词块。
- 删除共用控制面：`Supervisor` / `AgentHandle` / `dispatch.py` / `supervisor` RPC
  （status/cancel/approve/dispatch）及其活体注册表。
- 删除权限模式里的 `zen` 别名：`--mode zen` 按未知模式拒绝；桌面持久偏好里的旧值
  不再映射（回落应用默认 `ask`）；RPC `mode` op 本就只接受三值。权限词汇只剩
  `plan` / `ask` / `auto`。
- 保留：coding 的一切行为、`task` 子代理、`/goal`、文件浏览（`workspace_files` / work
  panel，与模式无关的同名概念）、run 账本、会话文件。
- 旧数据不删：`~/.zenagent/agents/`、项目 `swarm/` 房间、`workspace.json`、旧
  `runs.jsonl` 里的 zen/swarm 行全部留在盘上；代码不再读它们，也不报错。
- 旧 zen 会话（项目 session 目录里的 Router/worker 转录）不再有 `.zen.json` 标记，
  会作为**普通 coding 会话**出现在列表里、可继续；这是有意接受的结果，写进 CHANGELOG。
- 所有门禁绿：`check.sh`（ruff / mypy / 架构预算 / wire 生成 / i18n / pytest）、桌面
  `tsc` 与 `node --test`；`zen` / `zenagent` / `zen rpc` / TUI 都能正常启动，coding 跑通。

**非目标**

- 不重做 coding 的架构；不改 `task` 子代理、goal、压缩、权限、记忆、技能。
- 不做数据迁移脚本，不删除用户磁盘上的旧房间/角色/模式文件。
- 不保留任何空壳 op、隐藏 tab 或"暂时兼容"的 wire kind。

**反目标**

- 留下一个能点开但没后端的模式入口。
- 让旧 `config.toml` 因 `[zen]`/`[swarm]` 直接启动失败。
- 把 coding 的既有能力（尤其 `task`）顺手删掉。
- 文档继续教 `--mode zen`、三态切换器或哨兵式多智能体。

## 3. 约束

- Python 3.11+，运行时依赖仍只有 `httpx`；不得引入新依赖。
- 架构预算：`scripts/check_architecture.py` 的 800 行上限与 allowlist 必须同步
  （删掉 `zenagent/app/swarm/manager.py` 的 after-list 条目）。
- i18n：`zh`/`en` 键必须成对；桌面 `messages.ts` 同时持有两语言，键数按双份计。
- wire 是双侧契约：`zenagent/cli/rpc` 与 `desktop/src/rpc/types.ts` 共用
  `PROTOCOL_VERSION` 与 `REQUEST_KINDS`，由 `tests/test_rpc_contract.py` 钉住；
  `desktop/src/rpc/wire.gen.ts` 由 `scripts/gen_rpc_ts.py` 生成，不得手改。
- 退役配置不得导致启动失败（先例：`max_turns` 的 `RETIRED` + `warn_retired`）。
- 旧会话文件、旧 `runs.jsonl` 必须仍可被新代码读取（至少不崩、能列出）。

## 4. 方案

- **A. 直接删干净（选定）**：删模块、删 wire kind、删桌面、删文档；旧配置键走墓碑
  （接受、忽略、警告一次），旧数据留盘。一次破坏性变更，协议版本 +1。
- **B. 保留空壳兼容层**：`workspace`/`supervisor` op 保留并恒返回 `coding` / 空列表，
  旧桌面不崩。代价是多一层只为兼容存在的代码，而旧桌面本来就会因版本检查拒绝连接；
  正是要拆掉的"第二套内核"又留了一半。
- **C. 分三版灰度**：先在 UI 隐藏、再删后端、最后删 wire。代价是三个版本里仓库同时存在
  死代码与半死 wire，测试要维护两套状态；对单仓库、前后端同发的项目没有收益。

选 A。B 的兼容点只保留在**配置**这一层（墓碑键），因为那是用户磁盘上的既成事实，
不是我们自己的架构债。

## 5. 设计

### 5.1 词汇表（删完后）

| 概念 | 取值 | 说明 |
|---|---|---|
| 权限模式 `mode` | `plan` / `ask` / `auto` | `--mode`、桌面偏好、RPC `mode` op。`zen` 不再是别名。 |
| 工作区模式 | **不存在** | 一个项目只有 coding 一种会话形态。 |
| run kind | `coding` / `subagent`（历史值 `zen_router` / `zen_worker` / `swarm` 只读） | 账本读取保持向后兼容。 |
| 审批/会话归属 | slot / session | 只剩一套。 |

`zen` 这个词在仓库里只允许作为**产品名**存在：`zenagent` 包、`zen` 可执行文件、
`~/.zenagent/` 与 `<root>/.zenagent/` 路径。任何"模式/别名/编排器"含义都删掉。

### 5.2 Python：删除清单

整文件删除（行数为当前值）：

| 路径 | 行数 | 内容 |
|---|---|---|
| `zenagent/app/zen/`（5 文件） | 931 | fleet / identity / prompts / roster / tools |
| `zenagent/app/swarm/`（17 文件） | 4,793 | room / roles / manager / runner / schedule / router / roster / gate / protocol / transcript / workflow / workitems / approvals / events_tap / frames / instances |
| `zenagent/swarm_settings.py` | 210 | `[swarm]` 表解析与默认值 |
| `zenagent/app/workspace_state.py` | 108 | `workspace.json` 模式事实 |
| `zenagent/app/dispatch.py` | 91 | Supervisor 的 dispatcher 协议 |
| `zenagent/cli/rpc/ops_zen.py` | 344 | `zen_fleet` wire |
| `zenagent/cli/rpc/ops_swarm.py` | 357 | `swarm` wire |
| `zenagent/cli/rpc/ops_supervisor.py` | 204 | `supervisor` wire |
| `zenagent/cli/rpc/ops_workspace.py` | 93 | `workspace` wire |
| `zenagent/prompts/blocks/swarm_protocol.md` | 12 | 房间协议提示词 |

### 5.3 Python：手术清单

| 文件 | 改动 |
|---|---|
| `app/agent_profile.py` | 保留 `AgentProfile`（删 `project_sessions` 字段）；删 `AgentHandle`、`Supervisor`、`register_swarm_agent`、`swarm_handle_key`、`effective_worker_mode`；docstring 去编排叙述。 |
| `app/runtime.py` | 删 `zen_fleet`、`tool_providers`、`prompt_note_providers`；`_note_session_scope` 去掉 `project_sessions` 分支；相关 docstring。 |
| `app/skills_recompose.py` | 删 `for provider in runtime.tool_providers` 循环。 |
| `app/sessions.py` | 删 `read_roster` 导入与 `SessionEntry.workspace` 字段及其两处赋值（`entries` / `_unsaved_entry`）。 |
| `app/runs.py` | `RUN_KINDS` 保留历史值，三个历史字面量单独成行并以行尾 `# HISTORICAL:` 注释（"不再产生，只为读旧账本"）；`cancelled` 注释里的 "swarm" 改为泛称。 |
| `app/run_wiring.py` | 删 `attach_swarm_run`（唯一调用方在 `app/swarm/manager.py`，删除后成死壳）及其 `__all__` 导出；docstring 里 zen_router/zen_worker 例子改为历史说明。 |
| `app/approval_router.py`、`agent/jsonl.py` | docstring 去掉 swarm 房间叙述（代码不动）。 |
| `app/limits_apply.py` | 删 `SWARM_FIELDS` 导入与 `zen_*`/`*SWARM_FIELDS` 拷贝；docstring 同步。 |
| `app/modes.py` | 删 `LEGACY_MODES`、`normalize_mode` 里的映射、docstring 里"zen 别名"段。 |
| `cli/flags.py` | `_permission_mode` 保留（负责大小写/空白归一），删 docstring 里的 legacy 说明与 help 里的 `legacy alias: zen`；`zen` 经 `normalize_mode` 返回 `None` → argparse `unknown mode: zen` → 退出 2。 |
| `cli/rpc/cli.py` | 注释 `(zen)` 改为 `(auto)`。 |
| `config.py` | 删 `_ZEN_KEYS`、`DEFAULT_ZEN_*`、`_ZEN_*` 字段、`LEGACY_ZEN_WORKER_MODES`、`[zen]` 解析、`SwarmPolicy` 继承与 `[swarm]` 解析；新增**退役表**：`[zen]`/`[swarm]` 出现时忽略并每进程警告一次（全局层与项目层同待遇）。 |
| `limits.py` | 删 `[zen]`（4 行）与 `[swarm]`（10 行）注册项及导入；`GROUP_ORDER` 不保留空组。 |
| `prompts/spec.py` | 删 `swarm_protocol` BlockSpec。 |
| `i18n/{zh,en}.py` | 删 `swarm.locked`。 |
| `cli/rpc/host.py` | 删 `_swarm`、`_supervisors`、`_zen_pre`；删 `import ops_swarm/zen/supervisor/workspace`；dispatch 的四个 kind 分支；`_is_swarm_request_id` 与 `_resolve_swarm_prompt` 分支；`_drop` 的 `was_zen` 分支；`set_workspace_mode` 导入与调用；`initial`/`_adopt` 里的 `supervisor.register`。 |
| `cli/rpc/slot.py` | `pinned`/`close` 去 fleet 分支。 |
| `cli/rpc/ops_secrets.py` | 删 `_drop_idle_swarm_rooms` 方法与两处调用；`_knob_value` docstring 去 fleet/room。 |
| `cli/rpc/ops_runs.py` | docstring 去 zen/swarm 例子。 |
| `cli/rpc/server.py`、`cli/rpc/codec.py` | `PROTOCOL_VERSION` 3 → 4。 |
| `cli/rpc/protocol/_host.py` | 删 `HostSupervisor*`/`HostWorkspaceSetRequest` 请求类、`SupervisorAgentRow`/`WorkspaceStateWire` shape、`supervisor` 与 `workspace` OpSpec、`HOST_PARTIAL` 的 `swarm`/`zen_fleet` 条目。 |
| `cli/rpc/protocol/_views.py` | `SessionSummary` 删 `workspace` 字段。 |
| `config_errors.py`、`config_fields.py` | docstring 去 swarm policy 例子（代码不动）。 |

### 5.4 Wire 协议 v4

- 删除请求 kind：`supervisor`、`swarm`、`zen_fleet`、`workspace`。
- 删除结果 shape：`SupervisorAgentRow`、`WorkspaceStateWire`、`RunView` 不动（kind 是字符串）。
- 删除 `SessionSummary.workspace`。
- `PROTOCOL_VERSION` 两侧同时 3 → 4；`tests/test_rpc_contract.py` 的断言改 4。
- `desktop/src/rpc/wire.gen.ts` 重新生成；`scripts/gen_rpc_ts.py --check` 必须 fresh。
- `desktop/src/rpc/types.ts` 的 `REQUEST_KINDS` 删除四个 kind；`client.ts` 的
  `SESSION_TAG_EXEMPT` 同步删除。
- 旧客户端（v3）连新后端由版本检查明确拒绝，不做静默降级。

### 5.5 配置与 limits

- `[zen]` / `[swarm]` 进入**退役表**：解析时忽略、不报错、每进程 `logging.warning` 一次，
  文案说明这些能力已删除。项目层不再因这两个表触发 global-only 拒绝。
- 设置页（`limits` RPC）的 knob 列表随注册表缩小；`[zen]`/`[swarm]` 组消失。
- 旧 `workspace.json` 不再被读取；新代码不写它。

### 5.6 提示词与角色

- `swarm_protocol` 块与 spec 条目删除；zen 的 router/worker 提示词随 `app/zen/` 删除。
- 角色 manifest（`~/.zenagent/agents/*.json`、项目 `agents/`）不再被读取；
  文件保留，用户自行处置。README/CHANGELOG 说明这一点。

### 5.7 桌面：删除与手术

整文件删除：

- `ui/`：`ModeSwitcher.*`、`Swarm{Panels.module.css,Roster.tsx,Sidebar.tsx,Tasks.tsx,Works.module.css,Works.tsx}`、
  `WorkerConversation.*`、`Zen{Roster.tsx,Sidebar.tsx,Wordmark.module.css,Wordmark.tsx}`、
  `ProjectSwitcher.*`、`pages/{RoomPage.*,WorkspaceSettings.*,AgentSettings.*,LibraryPicker.tsx,agentEditor.tsx}`、
  `composer/RoomComposer.*`。
- `state/`：`zenActions.ts`、`zenStore.ts`、`zenSidebar.ts`、`swarmStore.ts`、`swarmWiring.ts`、
  `swarmRun.ts`、`swarmWorks.ts`、`roomCommands.ts`、`roomPreview.ts`。
- `rpc/types-swarm.ts`；`dev/swarmMock.ts`、`dev/zenMock.ts`。
- 测试：`roomCommands`、`roomComposerStore`、`roomPreview`、`swarmRun`、`swarmStore`、
  `swarmWiring`、`swarmWorks`、`workspaceMode`、`zenActions`、`zenSidebar`、`zenStore`；
  e2e `swarm.spec.ts`、`zen.spec.ts`。

手术：

| 文件 | 改动 |
|---|---|
| `App.tsx` | 删 `workspaceMode` 分支与 `closeWorker` 副作用；只渲染 coding 布局。 |
| `ui/Sidebar.tsx` | 删模式切换与 Zen/Swarm 分支；固定 `SidebarCoding`；`ProjectSwitcher` 若不再需要则随文件删除。 |
| `state/stores.ts` | 删 `WorkspaceMode`、`WORKSPACE_MODES`、`workspaceMode` 与持久化 `zen.workspaceMode`（旧 localStorage 键忽略）。 |
| `state/prefs.ts` | 删 `LEGACY_MODES = { zen: 'auto' }` 与 `loadPrefs` 里的映射。旧值 `zen` 落在 `MODES.includes` 之外 → `null` → 子进程默认 `ask`；这是一次性的姿态收紧，写进 CHANGELOG。 |
| `state/appStore.ts` | 删 `switchWorkspaceMode`/`stopFleet`/`zenTranscript`、`syncProjectState` 调用与 `zenActions` 导入。 |
| `state/frameRouter.ts` | 删 `applyFleetFrame`、`applySwarmFrame` 导入与调用。 |
| `state/sessionPanes.ts` | 删 `zen_fleet op=transcript` 的两段探测（直接走会话/磁盘路径）；docstring 去 zen。 |
| `state/sessionGroups.ts` | 删 `sessionsForWorkspace` 与 `workspace` 类型引用。 |
| `state/mentions.ts` | 删 `@role` 分支与 `agents` 参数（`RoomComposer` 一并删除）。 |
| `ui/composer/{InputBar,Menus}.tsx` | 删 `swarm.modelDefault*` 菜单项与房间分支；coding 输入条不变。 |
| `ui/pages/SettingsPage.tsx`、`settingsNav.tsx` | 删 Swarm/Agents 设置入口与相关关键字。 |
| `ui/ConversationTopbar.tsx`、`ui/AppFrame.tsx` | 去掉模式相关渲染/注释（若有）。 |
| `rpc/types.ts` | 删 `types-swarm` 导入/再导出、`REQUEST_KINDS` 四项。 |
| `rpc/client.ts` | `SESSION_TAG_EXEMPT` 删四项。 |
| `dev/bridge.ts` | 删 `supervisor`/`swarm`/`zen_fleet`/`workspace` 的 dev 处理。 |
| `i18n/messages.ts` | 删 `workspace.*`、`zen.*` 与 `swarm.*` 中无调用方的键；删 `messages-swarm.ts` 的 import/merge。 |
| `i18n/messages-swarm.ts` | 整文件删除（291 行）。 |
| `e2e/{home,layout,session,settings,work-panel,git-panel,plugin}.spec.ts` | 删模式切换相关步骤/断言。 |

### 5.8 文档

删除：`docs/guide/swarm.md`、`docs/guide/zen-mode.md`、`docs/guide/orchestration.md`、
`docs/internals/supervisor.md`、`docs/internals/zen.html`。

更新：`docs/README.md`（索引）、`README.md`（删 swarm 截图与模式段）、
`docs/architecture.md`（拓扑图与 run kind）、`docs/guide/{config,projects,sessions,rpc-sdk,cli,commands,i18n}.md`、
`docs/internals/{run-ledger,agent-loop}.md`、
`docs/frontend-matrix.md`、`docs/feature-inventory.md`、`docs/feature-map.md`、
`docs/architecture/allowlist.json`（删 `swarm/manager.py` 条目）、`CHANGELOG.md`
（Breaking 段：两个模式与 `--mode zen` 删除；`[zen]`/`[swarm]` 成为墓碑；旧 zen 会话按
coding 列出；桌面旧 `zen` 权限偏好回落 `ask`；旧数据留盘不读）。

`docs/arch-gov/*` 与 `docs/explorer/dist/*` 是生成快照：由 builder 决定重新生成或标注
为历史（见 §7）。

### 5.9 测试

删除（Python，21 文件）：`test_swarm_*`（12）、`test_zen_*`（5）、`test_rpc_swarm.py`、
`test_rpc_supervisor.py`、`test_workspace_state.py`、`test_dispatch.py`。

手术：

| 文件 | 改动 |
|---|---|
| `test_agent_profile.py` | 保留 `run_scope` 测试（kind 改 `coding`）；删 Supervisor / effective_worker_mode 测试；docstring 改名。 |
| `test_runtime_role_assembly.py` | 改名 `test_runtime_profile_assembly.py`；`swarm_post` stub 改名 `stub_tool`；四个用例保留（tools_allow / extra_tools / extra_prompt / plugins=False）。 |
| `test_modes.py` | `test_legacy_zen_is_alias_for_auto` 改为"`zen` 不是模式"：`normalize_mode("zen") is None`、`resolve_mode(explicit="zen")` raises。 |
| `test_cli.py` | `--mode zen` 的 gate 测试改为与 `--mode yolo` 相同的 argparse 拒绝断言（SystemExit 2 + `unknown mode: zen`）。 |
| `test_workspace.py` | 不动（文件浏览）。 |
| `test_run_scopes.py` | 整文件删除（三个 zen/swarm scope 用例）。 |
| `test_jsonl.py` | 删 `test_room_append_line_survives_a_torn_tail`（上面的 RunLedger 用例已覆盖同一路径）。 |
| `test_config.py` | 删四个 `[zen]` 用例；新增墓碑用例取代。 |
| `test_app_sessions.py` | 删 `test_entries_tag_zen_roster_sessions_as_zen`；新增"旧 zen 会话按 coding 列出"用例。 |
| `test_rpc_host_e2e.py` | 删 `test_zen_gives_the_router_its_own_slot`、`test_enabling_zen_does_not_abort_a_running_turn`。 |
| `test_rpc_contract.py` | `SERVER_FRAME_TYPES` 缩到 `result/error/event`；`_backend_source()` 只读 RPC 包；删两个 swarm 帧用例；版本断言改 4。 |

新增回归（小）：

- 配置里 `[zen]`/`[swarm]` 存在时：加载成功、值被忽略、警告只出现一次。
- `zen rpc` 的 `hello`/`status` 不再声明已删 kind；对 `swarm` 请求返回 `unknown type`。
- 会话列表不再包含 `workspace` 键；带旧 `.zen.json` 标记的会话作为 coding 行出现。
- 旧 `runs.jsonl`（含 `kind=swarm`）仍能列出。
- 桌面 `prefs.test.ts` 的 "legacy zen mode pref loads as auto" 改为"未知持久值加载为 null"。

### 5.10 提交顺序

1. `feat(modes)!: the permission vocabulary has no zen alias` —— `modes.py`、`flags.py`、
   相关测试与 `cli.md`。
2. `feat(agent)!: delete the zen and swarm orchestrators` —— Python 包、控制面、配置/limits、
   会话、提示词、RPC/host/protocol、版本 4、Python 测试、`wire.gen.ts`。
3. `feat(desktop)!: one workspace, no mode switch` —— 桌面删除与手术、mock、i18n、桌面测试、
   e2e。
4. `docs: coding is the only mode` —— 文档、README、CHANGELOG、allowlist。
5. 验证修复（若有）。

第 2 与第 3 个提交是一对：分支尖端必须 `tsc` + Python 双绿；第 2 个提交落地时桌面会短暂
引用已删 kind，这是 monorepo 顺序提交的正常中间态，不合并。

### 5.11 验收

```bash
# Python 门禁
uv run ruff check .
uv run mypy zenagent
python3 scripts/check_architecture.py
python3 scripts/check_i18n.py
python3 scripts/gen_rpc_ts.py --check
python3 scripts/check_worktrees.py
uv run pytest -q

# 桌面
cd desktop && ./node_modules/.bin/tsc --noEmit && node --test tests/*.test.ts

# 启动
zen --version
printf '%s\n' '{"type":"status","id":"s1"}' '{"type":"shutdown","id":"x"}' | zen rpc
zenagent -p "Reply with exactly: pong" --json        # 真 provider，一次 coding run
zen --mode zen -p hi                                 # 必须报 unknown mode 并退出 2（argparse）
# TUI 在 tmux 里启动、退出
# 配置含 [zen]/[swarm] 时启动：忽略 + 各警告一次
```

静态 grep 门（残留必须为空，历史值例外）：

```bash
# 允许的残留只有 runs.py 里带行尾标记的三个历史字面量。
grep -rnE "swarm|zen_fleet|WorkspaceMode|workspaceMode|LEGACY_MODES|attach_swarm_run" \
  --include=*.py zenagent/ | grep -v "# HISTORICAL:"
grep -rnE "swarm|zen_fleet|supervisor|workspaceMode|LEGACY_MODES" \
  desktop/src --include=*.ts --include=*.tsx
grep -rnE "zen-mode|orchestration|swarm\.md|supervisor\.md" \
  docs/guide docs/README.md README.md
```

## 6. 风险

| 风险 | 处理 |
|---|---|
| 隐藏耦合（某个共享模块只被编排器调用） | 顺序：先删包 → `ruff`/`mypy`/`pytest` 驱动补齐；`mypy` 会点名未定义属性。 |
| 旧配置启动失败 | 退役表 + 每进程一次警告；项目层同样忽略。 |
| 旧桌面/新后端 | 协议版本 4，版本检查显式拒绝。 |
| 删测试掩盖 coding 回归 | 保留并改名 profile 装配测试；coding 相关测试（loop/chat/goal/task/terminal）一个不删。 |
| i18n 键成对性 | 只删两侧都有、且调用方已删的键；`check_i18n.py` 兜底。 |
| 架构 allowlist 指向已删文件 | 同提交删除条目。 |
| `docs/arch-gov` 快照过期 | 生成物，允许滞后；见 §7。 |

## 7. 留给 builder 的

- `docs/arch-gov/*` 与 `docs/explorer/dist/*` 是否重新生成：它们是快照，重新生成需要
  arch-gov/explorer 的生成脚本；若不重新生成，在 `docs/arch-gov/brief.md` 标注"截至
  2026-11-25 的快照"即可。这属于表达性选择，不影响代码。
- i18n 里 `swarm.*`/`zen.*` 的逐键判定：§5.7 已给出文件级清单，具体到键时以
  "调用方已删"为准；`i18nKeys` 测试与 `check_i18n.py` 会指出遗漏/孤儿。
- `SidebarCoding.tsx` 是否顺带改名（它现在是唯一的 sidebar）：改名会扩大 diff，默认不改。
- 提交粒度可在 §5.10 的两提交（Python/桌面）之间再拆，只要分支尖端绿。

## 8. 已定项

| 决定 | 值 |
|---|---|
| `task` 子代理 | 保留（属于 coding） |
| `--mode zen` | 删除；与工作区模式无关，但词汇里不再有 zen |
| 旧数据 | 留盘、不读、不删 |
| 旧 run kind | 保留为历史值，账本读取兼容 |
| 协议版本 | 3 → 4 |
| 兼容层 | 仅配置墓碑；无 wire 空壳 |
| 文档 | 删 5 篇，其余按 §5.8 更新 |
