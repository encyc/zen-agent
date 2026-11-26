# ZenAgent

[![PyPI](https://img.shields.io/pypi/v/zenagent-py.svg)](https://pypi.org/project/zenagent-py/)
[![CI](https://github.com/encyc/zen-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/encyc/zen-agent/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)

简体中文 · [English](https://github.com/encyc/zen-agent/blob/main/README.md)

一个**能读完、能嵌进自己程序、能改**的编码代理：纯 Python 内核，运行时只依赖 `httpx`，
四个入口（TUI、REPL、headless、RPC），外加一个桌面端——它只是同一个运行时的另一个客户端。
它读写文件、跑命令、联网搜索、把自成一体的任务交给子代理、维护任务清单——从桌面端、终端、
一次性的 headless 调用，或你自己的程序里都行。

仓库 <https://github.com/encyc/zen-agent> · PyPI 包名 `zenagent-py`（import 名 `zenagent`）

![ZenAgent 桌面端：带工具行、内联 diff 和任务清单的会话](https://raw.githubusercontent.com/encyc/zen-agent/main/assets/screenshots/desktop-chat.png)

## 一个会话，一个 agent

ZenAgent 是编码代理，不是一个要在模式之间切换的工作区。一个会话就是：一个项目里、一段对话、
一个 agent。桌面端按项目组织会话，四个入口驱动同一个运行时；没有第二个需要盯着看的编排器。
定时轮次、找回旧会话、子代理，都是 agent 在这个会话里能用的工具。

唯一的「模式」是权限：`plan`（只读）、`ask`（写操作与命令要确认）、`auto`（不打断）。
`zen` 默认 `ask`，`--mode` 改它。

## 安装

```bash
uv tool install "zenagent-py[cli]"   # 或：pip install "zenagent-py[cli]"
zen --version
```

需要 Python **3.11+**（3.11–3.14 都发）。只装内核（`pip install zenagent-py`）拿到的是 SDK，
不含终端界面。

API key 从环境变量读，变量名按 provider 定义在
[`zenagent/catalog.yaml`](https://github.com/encyc/zen-agent/blob/main/zenagent/catalog.yaml)：

| provider | 环境变量 |
|---|---|
| `openai` | `OPENAI_API_KEY` |
| `zai` | `ZAI_API_KEY` |
| `zai-coding-cn` | `ZAI_CODING_CN_API_KEY` |
| `deepseek` | `DEEPSEEK_API_KEY` |
| `kimi` | `KIMI_API_KEY` |
| `moonshotai`, `moonshotai-cn` | `MOONSHOT_API_KEY` |
| `minimax` | `MINIMAX_API_KEY` |
| `minimax-cn` | `MINIMAX_CN_API_KEY` |
| `google` | `GEMINI_API_KEY` |
| `xai` | `XAI_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |
| `xiaomi` | `XIAOMI_API_KEY` |
| `xiaomi-token-plan-cn` | `XIAOMI_TOKEN_PLAN_CN_API_KEY` |
| `xiaomi-token-plan-ams` | `XIAOMI_TOKEN_PLAN_AMS_API_KEY` |
| `xiaomi-token-plan-sgp` | `XIAOMI_TOKEN_PLAN_SGP_API_KEY` |
| `opencode-go` | `OPENCODE_API_KEY` |

海外站与国内站是两个 provider，共用同一个 key（`moonshotai` / `moonshotai-cn`）。
包月套餐端点（`zai-coding-cn`、`xiaomi-token-plan-*`）按量计 0。

配一个 `[providers.custom.<id>]` 表，同一个内核就能指向任何 OpenAI 兼容的 endpoint（本地服务也算）
——见[配置](https://github.com/encyc/zen-agent/blob/main/docs/guide/config.md)。

### 第一次运行

```bash
zen                                     # TUI：转录、工具卡、侧栏
zen --no-tui                            # 行式 REPL，共用同一套命令
zen -p "explain this repo" --json       # headless：一个 JSON 对象，适合脚本/CI
```

TUI 每小时最多查一次 PyPI，有新版本时安静打一行；`-p`/`--json` 从不检查。
`ZENAGENT_NO_UPDATE_CHECK=1` 关掉它。

## 这是什么，为什么

编码代理要么闭源、锁死厂商，要么大到「改一层要先读十层」。ZenAgent 是那个小的：一个晚上能读完、
一个下午能嵌进项目、不需要谁批准就能改的 Python 内核。

- **只有一个依赖。** `types`、`events`、`providers`、`agent`、`tools` 只 import `httpx`，
  没有别的；wheel 不到 1 MB，你和模型之间没有 daemon。
- **纯 Python。** 没有 node runtime、没有编译步骤 —— `pip install` 就跑。
- **一套内核，四个入口。** TUI、REPL、headless（`-p`/`--json`）和 `zen rpc`（JSONL 协议）
  驱动的是同一个运行时，行为一致。
- **做 GUI 是写客户端，不是重写。** RPC 面已经存在，所以 Tauri 桌面端复用它，
  而不是 fork 一份内核 —— 提供 macOS、Windows、Linux 安装包。
- **三十行嵌进你的程序。** `Agent(model=..., tools=..., api_key=...)` 加一个 subscribe 回调：
  你的工具、你的存储、你的事件循环。
- **扩展不用重启。** skills、提示词模板、扩展、MCP server、自定义 provider 全是文件，
  `/reload` 当场生效。
- **不锁厂商。** 四个协议适配器（`openai-completions`，含 zai/deepseek 方言；`openai-responses`、
  `anthropic-messages`、`google-generative-ai`）加一份内置 catalog（带单价和上下文窗口）。
- **花费看得见。** `zen usage`、`/usage`、转录里逐轮成本，以及 `[limits]` 里的预算。
- **历史也是工具。** `list_sessions`、`search_sessions`、`read_session` 让 agent 自己去找
  早先会话里已经定下的结论，而不是让你再贴一遍。
- **定时任务就是一个文件。** 一条自动化 = 一段保存的 prompt + `daily HH:MM` 或 `every N<m|h>`；
  每个项目一个 daemon 在固定会话里跑一轮，报告留在那里。

## 特性

| | 你能拿到什么 | 去哪看 |
|---|---|---|
| **一个小的内核** | 六层依赖严格向下；每一层都能单独换掉 | [架构](https://github.com/encyc/zen-agent/blob/main/docs/architecture.md) |
| **四个入口** | TUI（转录、工具卡、侧栏）、REPL、headless 一次性调用、RPC/SDK | [CLI](https://github.com/encyc/zen-agent/blob/main/docs/guide/cli.md) · [TUI](https://github.com/encyc/zen-agent/blob/main/docs/guide/tui.md) |
| **内置工具** | 文件读写、带后台作业的 bash、grep/glob、联网搜索 + 抓取、子代理、任务清单、项目记忆、LSP、GitHub（装了 `gh` 才有）、checkpoint/rewind、跨会话检索 | [工具与权限](https://github.com/encyc/zen-agent/blob/main/docs/guide/tools-permissions.md) |
| **54 条命令** | TUI 与 REPL 共用同一套斜杠命令，支持 Tab 补全 | [斜杠命令](https://github.com/encyc/zen-agent/blob/main/docs/guide/commands.md) |
| **可扩展** | skills、模板、扩展、MCP server、自定义 provider —— 全是文件驱动 | [扩展](https://github.com/encyc/zen-agent/blob/main/docs/guide/extensions.md) · [MCP](https://github.com/encyc/zen-agent/blob/main/docs/guide/mcp.md) |
| **自动化任务** | 每个项目一个 daemon，按计划跑一轮；定义是纯文件，CLI 与桌面端共用 | [自动化任务](https://github.com/encyc/zen-agent/blob/main/docs/guide/automations.md) |
| **可嵌入** | Python 里用 `Agent` + `subscribe`；其它语言用 `zen rpc` | [RPC 与 SDK](https://github.com/encyc/zen-agent/blob/main/docs/guide/rpc-sdk.md) |
| **桌面端** | 同一个 `zen rpc` 之上的 Tauri 外壳：会话与项目、分屏、文件、Git 审查、终端、记忆、自动化、审批、自动更新 | [desktop](https://github.com/encyc/zen-agent/blob/main/desktop/README.md) |

### 桌面端

桌面端是同一个内核套一层 Tauri 外壳，也是一个会话最完整的视图：左侧会话与项目，中间是这一轮，
右侧工作面板放着文件、Git 审查栏、终端、项目记忆和自动化。一个会话可以分屏到另一个会话旁边，
也可以拖成独立窗口；打开着的项目由宿主保持常驻。

审查栏把工作区改动分组，点开任何文件都是内联 diff，下面还有提交图——不用离开对话就能读完一次改动。

![工作面板的 Git 审查栏：分组变更、内联 diff 与提交图](https://raw.githubusercontent.com/encyc/zen-agent/main/assets/screenshots/desktop-review.png)

## 参与贡献

```bash
uv sync --extra cli      # 内核 + 终端界面 + 开发工具
bash scripts/check.sh    # 唯一门禁：sync + ruff + pytest（CI 跑的完全一致）
```

包本身支持 3.11+，但仓库把 `.python-version` 固定在 3.15 —— 那是 `uv` 的开发工具链，
不是使用者的门槛。

接下来读什么：

| | |
|---|---|
| [架构总览](https://github.com/encyc/zen-agent/blob/main/docs/architecture.md) | 分层，以及一次请求会碰到什么 |
| [斜杠命令](https://github.com/encyc/zen-agent/blob/main/docs/guide/commands.md) | 全部 54 条命令 |
| [配置与目录](https://github.com/encyc/zen-agent/blob/main/docs/guide/config.md) | `config.toml`、provider、预算 |
| [自动化任务](https://github.com/encyc/zen-agent/blob/main/docs/guide/automations.md) | 定时轮次与项目级 daemon |
| [核心契约](https://github.com/encyc/zen-agent/blob/main/docs/reference/contracts.md) | 内核赖以成立的不变量 |
| [开发与发版](https://github.com/encyc/zen-agent/blob/main/docs/development.md) | 门禁、打包检查、发版流程 |
| [文档索引](https://github.com/encyc/zen-agent/blob/main/docs/README.md) | 其余全部，按阅读顺序 |

**还没做的：OS 级沙箱。** 工作区围栏和写入快照防的是 agent 犯错，不是恶意仓库或提示注入。

MIT 许可 —— 见 [LICENSE](https://github.com/encyc/zen-agent/blob/main/LICENSE)。
