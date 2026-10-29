# pi 功能清单 × zenagent 现状对照

> 快照：pi `v1.4.0-198` (`babe30a7e`)，2026-10-29 盘点；zenagent 同期（305 tests）。
> 图例：✅ 已具备 / 🟡 部分具备 / ❌ 未做

## 0. 规模基线

| 指标 | pi | zenagent | 倍数 |
|---|---|---|---|
| 发行包 | 11 个（ai / agent / coding-agent / tui / mcp / codemode / durable / env / chord / telemetry / evals） | 1 个 `zenagent` | 11× |
| src 文件（.ts / .py） | 844 | 34 | 25× |
| 测试文件 | 839 | 22 | 38× |
| 文档 | 44 篇 / 10,600 行 | README + 2 篇设计文档 | — |
| 协议适配器 | 10+（completions / responses / anthropic / google / bedrock / azure / vertex / cloudflare / codex / system-one） | 2（openai-completions、anthropic-messages） | 5× |
| provider | ~50 | 3 | 17× |
| 内置工具 | 8（read/write/edit/bash/find/grep/ls/powershell） | 7（read/write/edit/bash/glob/grep/list） | ≈ |
| 斜杠命令 | 27 | 6 | 4.5× |
| TUI 组件 | 18 | 0（行式渲染） | — |

## 1. 汇总：102 个功能簇

| 域 | 簇数 | ✅ | 🟡 | ❌ |
|---|---|---|---|---|
| A. LLM 接入层（pi-ai） | 20 | 4 | 10 | 6 |
| B. Agent 运行时（pi-agent-core） | 15 | 7 | 3 | 5 |
| C. Coding agent 内核 | 22 | 7 | 7 | 8 |
| D. Harness / UX | 19 | 1 | 3 | 15 |
| E. 可扩展性与生态 | 14 | 0 | 1 | 13 |
| F. 工程与平台 | 12 | 1 | 1 | 10 |
| **合计** | **102** | **20** | **25** | **57** |

读法：**完整覆盖约 20%，连同"骨架有了、细节没做"的部分约 32%**。要追平 pi 的功能面，还有 **57 个簇**；其中约 **25 个是"补一半就能用"**，不是从零。

---

## A. LLM 接入层（20）

| # | 功能 | zenagent |
|---|---|---|
| A1 | 统一消息/内容块模型（text/thinking/tool_use/image） | ✅ |
| A2 | openai-completions 适配器（含 zai / deepseek 方言） | ✅ |
| A3 | anthropic-messages 适配器（thinking signature 往返） | ✅ |
| A4 | openai-responses 协议 | ❌ |
| A5 | google-generative-ai / vertex 协议 | ❌ |
| A6 | bedrock-converse 协议 | ❌ |
| A7 | azure / cloudflare / codex 等云变体 | ❌ |
| A8 | provider 注册表（~50 家） | 🟡 3 家 |
| A9 | 模型目录（models.dev 自动生成、runtime overlay、pin） | 🟡 手写 YAML |
| A10 | 认证（env key / 交互登录 / OAuth / credential store / 刷新） | 🟡 仅 env |
| A11 | compat 旗标（每 provider 方言） | 🟡 核心几项 |
| A12 | 流式事件流 + `result()` promise | 🟡 无 result promise |
| A13 | 重试（transport / assistant-call / Retry-After） | 🟡 仅状态码 |
| A14 | 用量与成本（input/output/cache/reasoning） | ✅ |
| A15 | 上下文溢出检测（30 家错误文案 + 静默溢出 + length 启发式） | 🟡 仅 usage 水位 |
| A16 | 图片输入 / 图片生成 | 🟡 类型层有，无入口 |
| A17 | 分类器模型（classifier） | ❌ |
| A18 | transport 抽象（fetch / node-http / proxy / dispatcher） | 🟡 仅 httpx |
| A19 | telemetry 钩子（onPayload/onResponse/onProviderStreamEvent） | ❌ |
| A20 | prompt cache 管理（retention / cache warmer） | 🟡 透传 |

## B. Agent 运行时（15）

| # | 功能 | zenagent |
|---|---|---|
| B1 | loop 函数（prompt / continue 两个入口） | ✅ |
| B2 | Agent 有状态类（subscribe/steer/abort/idle/reset） | ✅ |
| B3 | 事件协议（agent/turn/message/tool） | ✅ |
| B4 | 工具参数 schema 校验 | ✅ 最小实现 |
| B5 | 工具契约细节（prepareArguments / outputSchema / structuredContent / replay / `details`） | 🟡 |
| B6 | 钩子集（convertToLlm / finishTurn / prepareRequest / prepareNextTurn / getApiKey） | 🟡 只有 tool/steering/context |
| B7 | steering 队列 | ✅ |
| B8 | follow-up 队列 | ❌ |
| B9 | 队列模式（all / one-at-a-time） | ❌ |
| B10 | 工具并发 + sequential 声明 + 顺序保证 | ✅ |
| B11 | abort 全链路（协议内状态） | ✅ |
| B12 | 自定义 AgentMessage（declaration merging） | ❌ |
| B13 | 中途 system 消息 / 工具增删宣告 | ❌ |
| B14 | Proxy stream fn（服务端代理鉴权） | ❌ |
| B15 | 运行状态快照（streamingMessage / pendingToolCalls） | 🟡 |

## C. Coding agent 内核（22）

| # | 功能 | zenagent |
|---|---|---|
| C1–C7 | read / write / edit / bash / find(glob) / grep / ls | ✅ 7 个 |
| C8 | powershell（Windows） | ❌ |
| C9 | 每工具专属渲染器（富 UI） | ❌ 单行摘要 |
| C10 | 文件变更队列（并发写串行化） | ❌ |
| C11 | 工具输出截断策略（头尾保留） | ✅ |
| C12 | system prompt 构建（环境/工具/技能注入） | 🟡 简单模板 |
| C13 | 上下文文件（AGENTS.md 等自动加载） | ❌ |
| C14 | 项目信任（project trust） | ❌ |
| C15 | compaction（自动 + 手动 + 自定义指令 + 分支摘要） | 🟡 仅手动 |
| C16 | 会话 JSONL 持久化 / resume | ✅ |
| C17 | 会话分支树 / fork / clone / import | ❌ |
| C18 | 会话导出 HTML / 分享链接 | ❌ |
| C19 | 模型解析（别名 / 虚拟模型 / 本地模型） | 🟡 |
| C20 | usage totals / timings / telemetry | 🟡 仅 --json 汇总 |
| C21 | cache warmer / http dispatcher / crash log | ❌ |
| C22 | 消息类型与渲染（系统消息/通知/自定义） | 🟡 |

## D. Harness / UX（19）

| # | 功能 | zenagent |
|---|---|---|
| D1 | 差分渲染 TUI 库 | ❌ |
| D2 | 18 个 TUI 组件（editor/markdown/layout/scroll/image…） | ❌ |
| D3 | 键位/焦点/鼠标/alt-screen | ❌ |
| D4 | 斜杠命令 27 个 | 🟡 6 个 |
| D5 | 主题系统（内置/自定义/包级） | ❌ |
| D6 | 可配置键位绑定 | ❌ |
| D7 | 设置管理器 / 设置 UI | ❌ |
| D8 | 自动补全 / @文件 / fuzzy | ❌ |
| D9 | 多行编辑器 / 外部编辑器 | 🟡 prompt_toolkit 基础 |
| D10 | 终端集成（tmux / termux / windows / setup） | ❌ |
| D11 | 诊断 / bug report | ❌ |
| D12 | 状态行 / program status | ❌ |
| D13 | alt-screen 搜索 | ❌ |
| D14 | 剪贴板复制 / 导出 | ❌ |
| D15 | CLI 参数 + print/JSON 输出 | ✅ |
| D16 | RPC 模式（JSON 协议 / 生命周期 / 错误 / ext-UI） | ❌ |
| D17 | 嵌入式 SDK | 🟡 Python 包可 import，无 SDK 文档 |
| D18 | 安装器 + `update` | ❌ |
| D19 | 终端内图片显示 | ❌ |

## E. 可扩展性与生态（14）

| # | 功能 | zenagent |
|---|---|---|
| E1 | 扩展系统（事件/API/自定义工具/UI/命令/状态） | ❌ |
| E2 | 热重载 `/reload` | ❌ |
| E3 | MCP 客户端（stdio/HTTP、OAuth、暴露控制、resources） | ❌ |
| E4 | Skills（SKILL.md / frontmatter / 加载顺序） | ❌ |
| E5 | Prompt templates | ❌ |
| E6 | Pi packages（npm/git 分发、依赖、资源选择） | ❌ |
| E7 | 自定义 provider（注册/认证/拉模型/自定义流） | ❌ 改 YAML |
| E8 | chord 插件运行时（对象/引用/复制状态/RPC） | ❌ |
| E9 | codemode（QuickJS 沙箱只调工具） | ❌ |
| E10 | durable（持久化 harness，崩溃续跑） | ❌ |
| E11 | env（远程执行环境 over SSH） | ❌ |
| E12 | 容器化（Docker / Sandboxes / OpenShell / Gondolin） | ❌ |
| E13 | 安全模型（信任/权限/漏洞报告） | 🟡 权限门 |
| E14 | 本地模型（llama.cpp / nvidia） | ❌ |

## F. 工程与平台（12）

| # | 功能 | zenagent |
|---|---|---|
| F1 | monorepo 多包构建 | ❌ 单包 |
| F2 | 静态检查（biome/tsc/import/pinned-deps/runtime-deps） | 🟡 ruff |
| F3 | 模型目录生成 + 兼容性检查 | ❌ |
| F4 | install-lock 生成与校验 | ❌ |
| F5 | 原生模块三平台构建 | ❌ |
| F6 | Nix flake / 离线构建 | ❌ |
| F7 | 安装器脚本（sh / ps1） | ❌ |
| F8 | 测试套件 | 🟡 305 vs 839 文件 |
| F9 | e2e 测试 | ❌ |
| F10 | evals 评测框架 | ❌ |
| F11 | telemetry 契约 + 一致性测试 | ❌ |
| F12 | 浏览器 smoke / CI workflows | ❌ |

---

## 2. pi 明确不做的（别当欠债）

- sub-agents、plan mode —— README 明说「skips features like sub-agents and plan mode」
- 内置权限系统 / 沙箱 —— 「does not include a built-in permission system」；靠容器化文档 + 用户自行隔离
- 所以 zenagent 现有的「权限门 + 不做 sub-agent」与 pi 的取舍一致。

## 3. 分期建议（按 zenagent 的实际情况）

| 期 | 目标 | 新增簇 | 累计覆盖 |
|---|---|---|---|
| S1 | 日常敢用（+12）：CI、成本/回合护栏、AGENTS.md、自动压缩、会话列表/fork/import、/undo、diff 渲染、补全、状态行、诊断、bash 危险命令策略、图片入口 | 12 | ~42/102（41%） |
| S2 | 可扩展（+10）：MCP、skills、prompt templates、自定义 provider、扩展系统、热重载、packages、主题、键位、工具白名单 | 10 | ~52/102（51%） |
| S3 | 生态与集成（+10）：RPC、SDK 文档、会话导出/分享、telemetry、evals、HTML 导出、crash log、openai-responses/google 适配器、分类器/图片模型 | 10 | ~62/102（61%） |
| S4 | 平台运维（+8）：安装器、原生构建、Nix、拆包、模型目录生成、install-lock、CI 矩阵、容器化 | 8 | ~70/102（69%） |

剩余约 30 簇属于「pi 特有生态」（chord/durable/env/codemode/codemode-TUI 等），对 zenagent 的定位（轻量可复用内核）不构成目标。

**一句话结论：pi ≈ 100 个功能簇、~850 个源文件；zenagent 现有 ~20 完整 + ~25 半成品。要做到"日常能用的 coding agent"大约再 12–15 个簇，做到"可扩展的 harness"再 +10，其余是生态与平台层，可按需取舍。**
