# Agent SDK 生态调研：Qoder、OpenAI、Claude、ADK 与开源框架

> 调研目的：回答两个问题——(1) Qoder、OpenAI 等厂商的 Agent SDK 以及同类开源框架各有什么能力；(2) 它们的**定制化程度**到底有多高：system prompt 能不能整个换掉、工具能不能任意扩展、agent 循环（loop）本身归谁所有。
> 方法：四路并行网络调研，以官方文档与 GitHub 一手来源为主。外部数据截至 **2026-11-19**；星数为搜索报道口径，随时间波动大，只作量级参考。两处未能从官方源验证的信息（Qoder 的 Java SDK、Qoder 的浏览器/计算机操控能力）已在文中标注存疑。
> 结论先行：**衡量定制化的唯一关键问题是「谁拥有 agent loop」**。裸模型 API SDK（`openai` 包）把 loop 完全留给你；引擎封装型 SDK（Qoder、Claude Agent SDK、Codex SDK）loop 是黑盒但壳可深度定制；轻量编排 SDK（OpenAI Agents SDK、Pydantic AI、ADK）loop 归 SDK、周边全可插拔；框架托管型（CrewAI、MS Agent Framework、Mastra）连流程一起收走。Qoder 确有真 SDK 但闭源且推理绑定自家服务；OpenAI 三层粒度全有；Claude Agent SDK 的 hooks/权限拦截面全场最深；zen 走的「自持 loop + 可嵌入」路线正是各家 SDK 都不提供的空档，最直接的同类是 Pydantic AI v2 的 Harness 层。

## 1. 总览：按「谁拥有 agent loop」分四层

| 层 | 代表 | loop 归属 | 定制化 |
|---|---|---|---|
| ① 裸模型 API SDK | `openai`（Python/JS/Go/Java/.NET）、`httpx` 直连 | **你自己写** | 最高，一切 DIY（含状态管理与工具分发） |
| ② 厂商引擎封装 SDK | Qoder Agent SDK、Claude Agent SDK、Codex SDK | 厂商（引擎黑盒） | 中高——壳全可换，loop 不可改 |
| ③ 轻量编排 SDK | OpenAI Agents SDK、Pydantic AI、Google ADK | SDK（插拔点极多） | 中——周边全可插，内循环固定 |
| ④ 框架托管 | CrewAI、MS Agent Framework、Mastra、Strands | 框架 | 低到中——声明式配置为主 |

2025–2026 的行业趋势是各家把 loop 越收越紧：引擎封装型 SDK 越来越强（Claude Agent SDK、Codex SDK 都是 2025 年才成型的产品），想改 loop 本身的人只剩裸 API 和 LangGraph 核心层两条路。协议层面 MCP 已是所有框架标配，A2A（agent↔agent，Google 发起、现归 Linux 基金会，spec v1.x）和 AG-UI（agent→前端事件）在补代理间与 UI 层的标准。

## 2. Qoder（阿里）：有真 SDK，但闭源 + 服务绑定

**存在性确认**：官方 [Agent SDK](https://qoder.com/zh/agent-sdk)（[文档](https://docs.qoder.com/zh/cli/sdk/overview)）把 Qoder 产品背后的 "Agent Harness" 开放出来——任务规划、工具调用、上下文/会话管理、权限控制、记忆、skills/插件/子代理、可定制 system prompt。包：npm `@qoder-ai/qoder-agent-sdk`（v1.0.53）、PyPI `qoder-agent-sdk`（v1.0.15，2026-09 发布，npm 周下载约 5k）。文档宣称有 Java 版（Maven Central `com.qoder.sdk:qoder-agent-sdk`，JRE 8+），第三方评测未找到构件——**存疑**。另有 [Cloud Agents API](https://qoder.com/zh) 与 CLI headless 模式（`qoder --print`，适合脚本/CI，配官方 GitHub Action `qoder-action`）。

**能力面**（CLI 为载体）：

- 模式：Interactive / Plan / Goal / 定时任务 / `/loop` / Quest / Headless。**注意：IDE 里的 Quest 窗口正在退役**，Quest 式自主执行转移到 CLI。
- 内置工具：文件读写、shell、Grep/Glob、联网搜索；多模态输入（图片、PDF）；记忆/知识库。
- IDE 与 CLI 都是 **MCP client**，任意 MCP server 可加工具。
- 特色产品能力：自定义子代理（2025-10 上线，CLI 自带一批内置子代理与 skills）；**Repo Wiki**（自动生成并持续同步项目结构化文档——架构/模块/业务逻辑，有配置文件可引导生成方向）。

**定制化程度：中等，且有硬边界。**

| 可定制 | 不可定制 / 边界 |
|---|---|
| `AGENTS.md` 规则（用户级会注入权限分类器上下文） | **闭源、不可自托管**；推理必须走 Qoder 的模型服务 |
| 项目级 `.qoder/` 目录：自定义子代理（`.qoder/agents/`）、skills、hooks、插件、自定义命令 | BYOK 只能挂 Qoder 维护的 provider 目录里的 OpenAI/Anthropic 兼容端点，**不能任意 endpoint** |
| 企业级：工具白名单、审批回调、审计日志、Service Account/PAT | 内置模型目录以国产模型为主：Qwen3.7-Max/Plus、DeepSeek、GLM、Kimi、MiniMax；Claude/GPT 仅 BYOK |
| system prompt 可定制（SDK 层） | 无自托管部署形态；国际版数据可能跨境，中国版留在国内 |

**商业与分发**：2025-08 全球发布。国际版 qoder.com 由 BRIGHT ZENITH PRIVATE LIMITED 运营（SOC 2 / ISO 27001）；中国版 Qoder CN（原通义灵码）在阿里云上、人民币计费、钉钉集成——**两套完全独立的栈**（账号、计费、合规）。定价：Free（基础模型 + 额度）/ Lite $10 月 1,000 credits / Pro $20 月 2,000 credits / Pro+ $60 月 6,000 credits / Ultra；有 Teams/Enterprise。

**判断**：Qoder 的 SDK 是「把产品 harness 开放成库」的路线，定位就是嵌入你自己的应用；但引擎黑盒 + 推理绑定决定了深度定制的用户用不了它。它在国内有云 + 钉钉分发和 Repo Wiki 这类产品化能力，是「开箱即用」端的强势玩家，对 zen 不构成引擎层威胁、构成生态层威胁。

## 3. OpenAI：三层粒度全都有

### 3.1 裸 SDK（`openai`，Apache-2.0，openai-python ~32k★）

[Responses API](https://developers.openai.com/api/docs/guides/tools) 是官方推荐的 agent 原语（Chat Completions 保留但定位为无状态对话）：

- **有状态**：`previous_response_id` 链式拼接、Conversations API（持久会话对象）、或手工回放历史（需完整回放含加密 reasoning 项的 output）。
- **托管工具**（Responses 独有的一等公民）：web search、file search、code interpreter、computer use、image gen、code generation，以及文档导航里更新的 tool search、skills、hosted MCP / Secure MCP Tunnel、apply patch、local shell、异步/程序化工具调用。
- Realtime API（WebSocket/WebRTC/SIP 语音会话 + function calling + MCP）、Structured Outputs（`strict: true`）、后台模式（`background: true` + 轮询/webhook）。
- 定制化：**loop 完全归你**——这是上限也是工作量。文档中现已出现服务端托管的 **Agents API**（sessions/sandboxes/MCP/tracing 全托管），走的是低定制化路线，与本调研主题互补。

### 3.2 OpenAI Agents SDK（`openai-agents-python` / `@openai/agents-js`，MIT，Python ~30.8k★ / JS ~4k★）

Python 侧 v0.1.0（2025-06）→ v0.25.0（2026-11-18），**仍在 0.x 快速演进**。五原语：**Agent / Handoffs / Guardrails / Sessions / Tracing**（Swawn 的产品化升级）。

- **不可定制**：内循环本身（model → tool calls → 循环至最终输出）。只能塑形：`tool_use_behavior`、`max_turns`、`failure_error_function` 失败恢复。要完全自持 loop → 退回裸 API。
- **可定制**：
  - instructions 静态或**动态**（callable 接收 `RunContextWrapper[TContext]`）；每 agent 的 `model` / `model_settings`；Pydantic `output_type`；`Agent.as_tool()`；`clone()`。
  - 生命周期钩子 `RunHooks` / `AgentHooks`：agent/LLM/工具/handoff 的前后共十余个回调。
  - Sessions 可插拔：SQLite / Redis / SQLAlchemy / MongoDB / Dapr / OpenAIConversations / 加密与压缩包装器 / 自实现协议。
  - Tracing 默认进 OpenAI 免费面板，可转发 LangSmith / Logfire / AgentOps / Datadog / Langfuse（ZDR 组织不可用）。
  - **模型不绑定 OpenAI**：`AsyncOpenAI(base_url=...)`、`ModelProvider`/`RunConfig`、LiteLLM 与 Any-LLM 适配器（beta；Responses 独有功能在 Chat Completions 适配器上会失效）。
- 多代理模式：agents-as-tools、handoff 路由、并行化、确定性代码编排（结构化输出分类 → 链/循环）。另有 `VoicePipeline` 语音、沙箱 agent（代码执行）、Temporal 持久工作流集成。

### 3.3 Codex（Rust，Apache-2.0，~136k★）

开源编码代理引擎（CLI / IDE 扩展 / 桌面 / Cloud）。

- 定制：`~/.codex/config.toml` 中 `sandbox_mode`（`read-only` / `workspace-write` / `danger-full-access`，macOS Seatbelt、Linux Landlock **系统级沙箱**，与 `approval_policy` 审批策略正交）；`[mcp_servers.*]`；AGENTS.md 项目指令；profiles；**`[model_providers.*]` 指向任意 OpenAI 兼容端点**（Ollama/LM Studio 均可）。
- [Codex SDK](https://developers.openai.com/codex/sdk)：TS `@openai/codex-sdk`（`codex.startThread()` / `thread.run()` / `resumeThread`）+ Python `openai-codex`（beta，JSON-RPC 跑在本地 codex app-server 上）；Rust core（`codex-rs` 工作区的 `app-server` crate）可嵌入；CLI 也能**作为 MCP server** 被 OpenAI Agents SDK 编排——官方给出的「把 Codex 当大流程里一个专家代理用」的模式。

## 4. Claude Agent SDK：引擎换壳路线的标杆

[claude-agent-sdk](https://code.claude.com/docs/en/agent-sdk/overview)（Python `claude-agent-sdk`，CLI 二进制随包分发；TS `@anthropic-ai/claude-agent-sdk`；其他语言走子进程 `-p --output-format json`）。官方定位：**运行 Claude Code 二进制的库**，给你"same tools, agent loop, and context management that power Claude Code"。

**定制化亮点（四家里拦截面最深）**：

- **system prompt 三态**：完全自定义字符串 / `{"type":"preset","preset":"claude_code","append":...}`（推荐姿势：preset + 追加）/ 文件路径。完全替换会丢掉 Claude Code 的调优行为，所以 preset+append 是工作模式。
- **工具**：进程内自定义工具（SDK MCP server，`@tool` 装饰器，无子进程开销）+ 外部 MCP（stdio/HTTP）+ `canUseTool` 权限回调（仅在权限流落到「需要询问」时触发）。
- **hooks**：PreToolUse / PostToolUse / PostToolUseFailure / UserPromptSubmit / Stop / SubagentStart / SubagentStop / PreCompact / PermissionRequest / Notification（TS 版另有 SessionStart/SessionEnd/PostCompact/model-switch 等 20+ 种）。钩子可**拒绝、改写工具输入（`updatedInput`）、替换工具输出、注入上下文**。
- **子代理**：`AgentDefinition`（description/prompt/tools/model/maxTurns/skills/permissionMode）；`subagent_type:"fork"` 复制上下文；TS v0.3.149+ 的 `Workflow` 工具可编排数十到上百代理。
- **会话**：`resume` / `fork_session` / `continue`，两侧均有 list/get/rename/tag 会话 API；**文件级 checkpoint + rewind**（`enable_file_checkpointing`）。
- **输入**：`prompt` 可以是异步可迭代对象（流式输入，输入未结束 agent 就可应答）；`ClaudeSDKClient` 双向 REPL + `interrupt()`；结构化输出 JSON Schema。
- **设置分层**：企业策略 > 内联 `settings` > 文件系统（user `~/.claude/settings.json` / project / local，可用 `settingSources: []` 全关）。模型 `model` + `fallback_model`；权限模式 `default/acceptEdits/plan/dontAsk/bypassPermissions/auto`；`disallowedTools` 在 bypass 下也生效。
- **Claude Code 特性快车道**：CLAUDE.md 记忆、skills（`.claude/skills/` 自动发现，但 SDK 无编程式注册 API）、项目命令/代理、文件系统 hooks、本地插件。

**不可定制**：内循环与内置工具实现是黑盒（包装的是二进制）；只能通过 hooks/权限去门控与观察，不能重写。品牌限制：衍生产品须自有品牌，不得使用 "Claude Code" 命名/ASCII art。第三方产品**不允许接 claude.ai 订阅登录**——只能 API key / Bedrock / Vertex / Microsoft Foundry。许可：GitHub 上 MIT 文件 + Anthropic 商业条款。

## 5. Google ADK（附 Gemini CLI）

[ADK](https://adk.dev/)（[adk-python](https://github.com/google/adk-python) Apache-2.0 ~22.3k★，双周发版；另有 Java/Kotlin/Go/TS，已进 2.0 时代）。

- **多代理编排最全**：`LlmAgent` + 确定性模板 workflow（`SequentialAgent`/`ParallelAgent`/`LoopAgent`，不耗模型调用）+ LLM 驱动的 `transfer_to_agent` 路由 + AgentTool（agent 当工具）+ agent teams；2.0 以**图工作流与动态工作流**（Workflow Runtime：路由/扇出扇入/循环/重试/HITL）取代模板，Task API 做代理间委派。**A2A 协议一等公民**（Python/Go/Java/Kotlin 都有 expose/consume 快速入门）；代理自身也可作为 **MCP server** 被暴露。
- **工具**：函数工具（普通 Python/Java 函数自动包装，带确认流）、`OpenAPIToolset`、`McpToolSet`（streamable HTTP/SSE/stdio）、内置（Google Search grounding、Enterprise Web Search、Code Execution、Computer Use、沙箱 Bash、Files Retrieval）、约 90 项集成目录（BigQuery/Stripe/Temporal…）。
- **状态四件套**：SessionService（state_delta 追踪）/ MemoryService / ArtifactService，支持会话回溯与迁移；上下文管理明言「像源码一样对待」（过滤/摘要/懒加载/压缩）。
- **callbacks**：`before/after_model_callback`（可直接返回 `LlmResponse` **跳过模型调用**、改写请求/响应）、`before/after_tool_callback`（阻断/改写工具调用）、错误回调；回调可传列表，第一个非 None 结果生效。
- **模型自由**：Gemini（AI Studio / Vertex）、GCP 上的 Claude、`LiteLlm`/`ApigeeLlm` 连接器 + OpenAI/Anthropic/Ollama/vLLM 包装器、运行时模型路由 + 自动故障转移。
- **工具链与部署**：Agents CLI（脚手架/构建/测试/评估/部署）、`adk web` 开发 UI、`adk eval`（判据/用户与环境模拟/自定义指标）、部署到 Gemini Enterprise Agent Platform（原 Vertex AI Agent Engine）/ Cloud Run / GKE。

**Gemini CLI 不是 Google 的可嵌入引擎答案**：Apache-2.0 开源、可定制（GEMINI.md、`~/.gemini/settings.json`、MCP、自定义扩展、headless JSON 输出），但无「嵌入引擎」级 SDK（只能靠 core 包或子进程）；2026-06 起免费个人入口迁往 Antigravity CLI，Gemini CLI 转向 Code Assist 组织场景且「不为 Gemini 4+ 优化」。**ADK 才是 Google 支持的可嵌入 agent 运行时。**

## 6. 开源框架一览

| 框架 | 星数（约） | 定位 | loop 归属 | 许可 | 备注 |
|---|---|---|---|---|---|
| [LangGraph](https://github.com/langchain-ai/langgraph) | ~44k | 图编排、持久化、time-travel | **核心层你写图，loop 归你**；预置 `create_react_agent` 则框架持有 | MIT（Py+TS） | v1.0 GA 2025-09/10；checkpointing（Postgres/Redis/SQLite）+ 持久执行 + 重试；Replit/Uber/LinkedIn/GitLab 生产在用；商业化靠 Platform/LangSmith |
| [MS Agent Framework](https://github.com/microsoft/agent-framework) | — | Semantic Kernel + AutoGen 官方合并后继（2025-10 宣布，AutoGen classic 维护模式） | 框架持有（agent 循环 + 工作流执行都归它） | MIT（.NET+Py） | v1.0 GA 2026-04；图工作流（executor/边/事件）+ sequential/concurrent/handoff/group-chat/**Magentic**（黑板式动态）模式；MCP+A2A；Azure 深度集成 |
| [CrewAI](https://github.com/crewAIInc/crewAI) | ~50k | 角色制多代理 crew + 事件驱动 Flows | 框架持有（`@listen`/`@start` 声明式） | MIT（Py） | ~570 万月下载；v1.15.x，conversational Flows 已稳定；LiteLLM 模型路由；有企业控制面 |
| [Vercel AI SDK](https://ai-sdk.dev) | ~28k | TS 模型层 + 前端流式桥 | **频谱**：v6（2025-12）`ToolLoopAgent`（`stopWhen`/工具审批/max steps）或自己写 while 循环 | Apache-2.0（TS） | `generateText/streamText/generateObject` + React/Svelte/Vue hooks；provider 无关 + 回退；Next.js 生态事实标准 |
| [Mastra](https://github.com/mastra-ai/mastra) | ~29.6k | TS 全家桶：agents/图工作流/持久 suspend-resume/记忆/RAG/evals/Studio | 框架持有 | **双许可**：Apache-2.0 核心 + EE 目录源码可得 | v1.0 2026-01，发版极快；40+ provider 路由、语音、浏览器、部署器 |
| [Pydantic AI](https://github.com/pydantic/pydantic-ai) | ~21k | 类型化 agent loop；v2（2026-06）加 **Harness 层** | SDK 持有（v2 Harness 提供 80+ 可组合能力：记忆/规划/子代理/沙箱） | MIT（Py） | Pydantic 原生结构化输出 + 类型安全依赖注入；模型一字符串切换；Logfire 观测；常与 LangGraph 并列 Python 默认推荐 |
| [Strands](https://strandsagents.com)（AWS） | harness-sdk ~9.2k | 模型驱动（Nova 团队出品，2025-05 开源） | 框架持有；`harness-sdk` 可收回 loop 控制权 | Apache-2.0（Py+TS） | 20+ 内置工具、多代理图/群、会话、流式、hooks、MCP、Rust 沙箱（box/shell）、evals；Bedrock 优先但不绑定 |

一行带过：**smolagents**（HF，代码即动作，~27k★）、**AG2 / AutoGen classic**（维护模式，活跃开发已转移）、**Letta**（ex-MemGPT，记忆优先，2026-08 出 Agents SDK，转向 Letta Code）、**OpenHands**（ex-OpenDevin，~78k★，已出 Software Agent SDK + Agent Canvas）、**Eino**（字节 CloudWeGo，Go，~13.5k★，链/图/RAG）、**Qwen-Agent**（重心转向 AgentWorld 世界模型代理与 Qwen Code 1.0）、**OpenClaw**（2026 年爆发者，9k→60k+★，个人代理跑在自己基础设施上）、**Agno**（高性能 Python 框架，DeepSeek Harness 曾登顶 GitTrend）。

## 7. 定制化对比

| 维度 | Qoder SDK | OpenAI Agents SDK | Codex SDK | Claude Agent SDK | Google ADK | LangGraph | 裸 API SDK |
|---|---|---|---|---|---|---|---|
| 内 loop 可改 | ❌（闭源） | ❌（可塑形） | ❌（Rust core 需自编译） | ❌（黑盒二进制） | 部分（低层可组） | ✅ | ✅ |
| system prompt | ✅ | ✅ 静/动态 | ✅ AGENTS.md | ✅ 替换或 preset+append | ✅ | ✅ | ✅ |
| 工具扩展 | MCP + skills/插件 | 函数工具 + MCP | MCP + 自定义 provider | 进程内工具 + MCP（最强） | 函数/OpenAPI/MCP/90 集成 | 任意 | 任意 |
| 拦截/钩子 | hooks（有限） | RunHooks/AgentHooks | 审批策略 | **最全（20+ hooks，可改写一切）** | before/after model/tool（可跳过模型调用） | 图节点即钩子 | 自己实现 |
| 模型自由度 | 国产目录 + 受限 BYOK | 任意（LiteLLM，部分功能受限） | 任意兼容端点 | Claude 系（+Bedrock/Vertex） | 任意（LiteLlm） | 任意 | 任意 |
| OS 级沙箱 | — | — | ✅ Seatbelt/Landlock | 权限模式（非 OS 沙箱） | 沙箱 bash | — | — |
| 会话/记忆 | 会话 + 记忆 + skills | Sessions 可插拔 + 加密/压缩包装 | 线程 resume | resume/fork/list + checkpoint/rewind | Session/State/Memory/Artifact + 回溯 | checkpointing + time-travel | 自己实现 |
| 可嵌入自产品 | ✅（推理走 Qoder 服务） | ✅ | ✅ | ✅（限 API key 认证） | ✅ | ✅ | ✅ |
| 开源 | ❌ | MIT | Apache-2.0 | MIT 文件 + 商业条款 | Apache-2.0 | MIT | Apache-2.0 |

## 8. 对 zen-agent 的启示

zen 的内核形态（`zenagent/agent/loop.py` 的自持循环 + `catalog.yaml` 多 provider + `zen rpc` 可嵌入宿主）处在「裸 API SDK 之上的自研引擎」位置，与上表对照：

1. **「能改/自持 loop」是稀缺品，这是 zen 定位的护城河。** 引擎封装型 SDK 全部把 loop 收成黑盒（Qoder 闭源、Claude 二进制、Agents SDK 固定循环）；框架托管型连流程一起收走。想要「读得懂、改得动、嵌得进」的 loop，2026-11 的市场只提供裸 API 和 LangGraph 图层。zen 最直接的同类是 Pydantic AI v2 的 Harness 层（类型化 loop + 80+ 可组合能力），可作为对标基线。
2. **各家收敛出的「标配能力」，缺哪块就是相对短板**：
   - Claude Agent SDK 的 **hook 深度**（改写工具输入/输出、注入上下文）——zen 的 extensions/hooks 面可对照其 PreToolUse/PostToolUse/Stop/PreCompact 清单查缺；
   - Claude 的**文件级 checkpoint + rewind**；
   - Codex 的 **OS 级沙箱与审批策略正交设计**（zen 现有审批流可借鉴其 `sandbox_mode × approval_policy` 二维解耦）；
   - OpenAI Agents SDK 的 **tracing 默认免费开箱**（zen 的 run ledger 已有数据源，差对外可观测面）；
   - ADK 的 **eval 工具链**（`adk eval`：判据/模拟用户/自定义指标——zen 的 `evals/` 可对标）。
3. **协议位**：MCP 必配（zen 已有）；A2A 已是 Linux 基金会 v1.x 标准，多代理/swarm 场景值得预留 expose 位（ADK 的「agent 即 MCP server」是个低成本切入姿势）。
4. **Qoder 的 Repo Wiki 值得单独研究**：自动生成并持续同步项目结构化文档，是上下文工程产品化的样板，且 Qoder 把它做成了用户可引导（配置文件）的形态。

## 9. 参考资料

1. Qoder Agent SDK 产品页：<https://qoder.com/zh/agent-sdk>；SDK 文档：<https://docs.qoder.com/zh/cli/sdk/overview>；CLI 文档：<https://docs.qoder.com/cli/overview>
2. Qoder 定价：<https://docs.qoder.com/account/pricing>；模型目录：<https://docs.qoder.com/qoderwork/models>；中国版：<https://docs.qoder.cn>
3. OpenAI 工具指南（Responses/托管工具/MCP）：<https://developers.openai.com/api/docs/guides/tools>；会话状态：<https://developers.openai.com/api/docs/guides/conversation-state>；后台模式：<https://developers.openai.com/api/docs/guides/background>
4. OpenAI Agents SDK 文档：<https://openai.github.io/openai-agents-python/>（sessions/tracing/multi_agent/models 各子页）；发版记录：<https://github.com/openai/openai-agents-python/releases>；JS 版：<https://github.com/openai/openai-agents-js>
5. Codex 仓库：<https://github.com/openai/codex>；配置：<https://developers.openai.com/codex/config>；SDK：<https://developers.openai.com/codex/sdk>
6. Claude Agent SDK 总览：<https://code.claude.com/docs/en/agent-sdk/overview>；TS/Python 参考、hooks、sub-agents、sessions、settings、authentication、claude-code-features 各子页同域；Python 仓库：<https://github.com/anthropics/claude-agent-sdk-python>
7. Google ADK：<https://adk.dev/>（workflow-agents / callbacks / models / integrations / sessions / a2a 各子页）；仓库：<https://github.com/google/adk-python>；Gemini CLI：<https://github.com/google-gemini/gemini-cli>
8. LangGraph：<https://github.com/langchain-ai/langgraph> · <https://langchain-ai.github.io/langgraph/>
9. Microsoft Agent Framework v1.0 公告：<https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0>；文档：<https://learn.microsoft.com/agent-framework/overview>
10. Vercel AI SDK 6（agent 抽象）：<https://vercel.com/blog/ai-sdk-6>；loop 控制：<https://ai-sdk.dev/docs/agents/loop-control>
11. Pydantic AI v2（Harness）：<https://pydantic.dev/articles/pydantic-ai-v2>；Mastra：<https://github.com/mastra-ai/mastra>；Strands：<https://aws.amazon.com/blogs/opensource/introducing-strands-agents-an-open-source-ai-agents-sdk> · <https://strandsagents.com>
12. A2A 协议：<https://a2a-protocol.org>；本仓库对照：`docs/architecture.md`、`zenagent/agent/loop.py`、`zenagent/catalog.yaml`、`zenagent/cli/rpc/`、`docs/research/personal-agent-architecture.md`
