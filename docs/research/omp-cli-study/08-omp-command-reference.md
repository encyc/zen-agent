# omp 命令全表（87 个顶层命令）

> 用途：以后想查"omp 有什么功能"时翻这里。
> 数据来源：`can1357/oh-my-pi` main 分支 `packages/coding-agent/src/slash-commands/builtin-*.ts`（按域分文件，顶层命令按缩进解析）。
> 第三列是 **ZenAgent TUI 现状**：✅ 已有；⏳ 需要 app 层新能力；❌ 不在本项目范围。

## 怎么读这张表

- omp 的命令分 7 个域，每个域一个 `builtin-<domain>.ts`：modes / session / lifecycle / control / collaboration / marketplace / skills。
- 很多命令带**子命令**（例如 `/todo append`），这里只列顶层；子命令在 `03` 之后的调研文档里。
- 状态列只描述"这个能力我们有没有"，不代表名字必须一样（例如 omp 的 `/resume` 对应我们的 `/sessions`）。


## 模式与设置（modes，21 个）

| 命令 | 作用 | 我们的状态 |
|---|---|---|
| `/security` | Plan, run, inspect, import, and compare OMP-native security scans | ❌ 不做 |
| `/settings` | Open settings menu | ✅ 已有 |
| `/setup` | Open provider setup | ❌ 不做 |
| `/plan` | Toggle plan mode (agent plans before executing) | ⏳ 未做 |
| `/plan-review` | Re-open the plan review for the latest plan (plan mode only) | ⏳ 未做 |
| `/vibe` | Toggle vibe mode (direct persistent fast/good worker sessions; read-only toolset) | ❌ 不做 |
| `/goal` | Toggle goal mode (persistent autonomous objective for this session) | ❌ 不做 |
| `/guided-goal` | Have the agent interview you in chat, then set up goal mode | ❌ 不做 |
| `/loop` | （描述由源码动态生成） | ❌ 不做 |
| `/queue` | Queue a message for after the agent yields | ❌ 不做 |
| `/model` | Switch model for this session | ✅ 已有 |
| `/switch` | （描述由源码动态生成） | ⏳ 未做 |
| `/fast` | Toggle fast service (OpenAI service_tier=priority or ultrafast, Anthropic speed=fast, Google priority) | ❌ 不做 |
| `/slow` | Toggle slow mode: flex tier on OpenAI/Google; on Anthropic, continue at low priority after the Claude session limit | ❌ 不做 |
| `/skillful` | Toggle listing available skills in the system prompt (session only) | ❌ 不做 |
| `/extended-context` | Toggle extended context windows | ❌ 不做 |
| `/computer` | Toggle the native computer-use eval prelude for this session | ❌ 不做 |
| `/ratchet` | Build (or reuse) an eval for an LLM flow, then hillclimb it unattended | ❌ 不做 |
| `/prewalk` | Arm, restart, or cancel a one-shot model handoff | ❌ 不做 |
| `/modelpreset` | Save and switch model presets (role models + thinking level) | ⏳ 未做 |
| `/effort` | （描述由源码动态生成） | ❌ 不做 |

## 会话与周边（session，20 个）

| 命令 | 作用 | 我们的状态 |
|---|---|---|
| `/todo` | View or modify the agent's todo list | ❌ 不做 |
| `/session` | Session management commands | ✅ 已有 |
| `/jobs` | Show async background jobs status | ❌ 不做 |
| `/usage` | Show provider usage and limits | ✅ 已有 |
| `/stats` | Launch the local stats dashboard | ❌ 不做 |
| `/changelog` | Show changelog entries | ⏳ 未做 |
| `/hotkeys` | Show all keyboard shortcuts | ✅ 已有 |
| `/tools` | Show tools currently visible to the agent | ⏳ 未做 |
| `/context` | Browse context usage and detailed content | ✅ 已有 |
| `/extensions` | Open Extension Control Center dashboard | ❌ 不做 |
| `/agents` | Open the agents hub (per-agent model, prewalk, and advisor) | ❌ 不做 |
| `/git` | Open the git UI (split diff viewer, staging, commit composer) | ❌ 不做 |
| `/local` | Open a session local:// file in $EDITOR (picker when no name given) | ❌ 不做 |
| `/hub` | Open the live Agent Hub (subagents, including finished ones) | ❌ 不做 |
| `/branch` | Rewind to a previous message, keeping the old path as a branch | ❌ 不做 |
| `/fork` | Fork this session, or open it in a multiplexer pane or window | ✅ 已有 |
| `/tree` | Navigate session tree (switch branches) | ❌ 不做 |
| `/login` | Login with OAuth provider | ❌ 不做 |
| `/logout` | Logout from OAuth provider | ❌ 不做 |
| `/mcp` | Manage MCP servers (add, list, remove, test) | ✅ 已有 |

## 生命周期与上下文治理（lifecycle，25 个）

| 命令 | 作用 | 我们的状态 |
|---|---|---|
| `/ssh` | Manage SSH hosts (add, list, remove) | ❌ 不做 |
| `/new` | Start a new session | ✅ 已有 |
| `/fresh` | Reset provider stream state without changing the local transcript | ❌ 不做 |
| `/clear` | Clear the conversation context in place, keeping the session | ✅ 已有 |
| `/delete` | Delete the current session and start a new one | ⏳ 未做 |
| `/compact` | Manually compact the session context | ✅ 已有 |
| `/shake` | Drop heavy content from context (tool results, large blocks) | ❌ 不做 |
| `/handoff` | Summarize the session into a handoff document and compact in place | ❌ 不做 |
| `/resume` | Resume a different session | ⏳ 见 `/sessions` |
| `/pin` | Pin or unpin a session at the top of the resume list | ⏳ 未做 |
| `/btw` | Ask a side question, or browse this session's BTW history | ⏳ 未做 |
| `/tan` | Run a full background agent on tangential work | ❌ 不做 |
| `/omfg` | Forge a TTSR rule from a complaint to stop a recurring behavior | ❌ 不做 |
| `/cleanse` | Detect and fix project diagnostics with weighted parallel subagents | ❌ 不做 |
| `/retry` | Retry the last failed agent turn | ⏳ 未做 |
| `/debug` | Open debug tools selector | ⏳ 未做 |
| `/memory` | Inspect and operate memory maintenance | ❌ 不做 |
| `/rename` | Rename the current session (omit title to generate) | ⏳ 未做 |
| `/move` | Move the current session to a different directory | ⏳ 未做 |
| `/wt` | Move this session into a new worktree, changes included | ❌ 不做 |
| `/add-dir` | Add a workspace directory to this session (multi-root) | ⏳ 未做 |
| `/remove-dir` | Remove a workspace directory from this session | ⏳ 未做 |
| `/dirs` | List this session's workspace directories | ⏳ 未做 |
| `/exit` | Exit the application | ✅ 已有 |
| `/restart` | Restart omp with the same launch flags, resuming this session | ⏳ 未做 |

## 运行时控制（control，6 个）

| 命令 | 作用 | 我们的状态 |
|---|---|---|
| `/force` | Force next turn to use a specific tool | ❌ 不做 |
| `/live` | Start Codex-backed realtime voice mode | ❌ 不做 |
| `/record` | Start or stop recording this screen to a replayable file (omp play) | ❌ 不做 |
| `/pause` | Freeze all agents (main, subagents, advisor) until resumed | ❌ 不做 |
| `/forget-approvals` | Forget every file and folder approval Ask and remember has recorded | ❌ 不做 |
| `/quit` | Quit the application | ✅ 已有 |

## 协作与分享（collaboration，11 个）

| 命令 | 作用 | 我们的状态 |
|---|---|---|
| `/advisor` | Toggle the advisor (a second model that reviews each turn and injects notes) | ❌ 不做 |
| `/export` | Export session to HTML file | ✅ 已有 |
| `/trace` | Open this session's trace in the stats dashboard | ❌ 不做 |
| `/dump` | Copy session transcript to clipboard (and write LLM request JSON to tmp) | ❌ 不做 |
| `/share` | Share session via an encrypted link (share server or secret gist) | ❌ 不做 |
| `/collab` | Share this session live via a relay | ❌ 不做 |
| `/join` | Join a shared collab session | ❌ 不做 |
| `/leave` | Leave the collab session | ❌ 不做 |
| `/browser` | Toggle browser eval-prelude headless vs visible mode | ❌ 不做 |
| `/copy` | Pick text or code from the conversation to copy | ❌ 不做 |
| `/open` | Open the last link from the conversation in your browser (or pick one with /copy) | ❌ 不做 |

## 插件市场（marketplace，3 个）

| 命令 | 作用 | 我们的状态 |
|---|---|---|
| `/marketplace` | Manage marketplace plugin sources and installed plugins | ❌ 不做 |
| `/plugins` | View and manage installed plugins | ❌ 不做 |
| `/reload-plugins` | Reload all plugins (skills, commands, hooks, tools, agents, MCP) | ❌ 不做 |

## 技能（skills，1 个）

| 命令 | 作用 | 我们的状态 |
|---|---|---|
| `/skills` | Search, install, and update skills from the skills.omp.sh registry | ✅ 已有 |

## 汇总

| 状态 | 数量 |
|---|---|
| ✅ 已有 | 20 |
| ⏳ 需要 app 层能力 | 见各表的 ⏳ 行 |
| ❌ 明确不做 | 其余 |

**⏳ 里最值得做的四个**（按用户价值排序）：`/undo`（回滚上一轮）、`/retry`（重试）、`/todo`（任务 HUD）、`/tree`+`/branch`（会话树）。

## 源码对照

- `packages/coding-agent/src/slash-commands/builtin-{modes,session,lifecycle,control,collaboration,marketplace,skills}.ts` — 命令定义
- `packages/coding-agent/src/slash-commands/builtin-registry.ts` — 域的组合顺序
- `docs/slash-command-internals.md` — 发现/去重/优先级规则
