# 在 zen 上做 Personal Agent：架构调研

> 调研目的：回答两个问题——(1) 在 zenagent 内核之上构建 personal Agent，架构该怎么设计；(2) 现在同类高星开源项目有哪些、各自值得借鉴什么。
> 方法：读本仓库代码（五层架构、RPC 宿主、扩展面、Swarm / 记忆系统），并对标网络上的同类项目。外部数据截至 **2026-11-19**，星数为搜索报道口径，随时间波动大，只作量级参考。
> 结论先行：**personal Agent = coding Agent 内核 + 四件套（消息渠道、定时唤醒、个人记忆、连接器）**。zen 的内核与扩展面已经齐备，四件套全部缺失。推荐走「Zen 原生」路径：在 RPC 宿主中新增 `ops_personal` ops 族承载渠道与调度，Swarm 的持久角色模型复用为多角色人格；产品形态对标 OpenClaw 的 Gateway，记忆分层对标 Letta。

## 1. zen 的家底：personal Agent 需要什么 vs. 现在有什么

zen 本质是编码 Agent 内核：五层架构 `cli → app → tools → agent → providers`（依赖只准向下，`tests/test_layering.py` 强制），多个表面（TUI / REPL / headless `zen -p` / `zen rpc`），Tauri 桌面端是纯 RPC 客户端。

| personal Agent 需要 | zen 现状 | 位置 |
|---|---|---|
| 常驻进程 / 控制面 | ✅ `zen rpc` 就是长驻宿主（JSONL 协议 v3，桌面端经 Tauri 桥 spawn 它） | `zenagent/cli/rpc/server.py`、`desktop/src-tauri/src/rpc.rs` |
| Agent 循环 + 多模型 | ✅ 无状态异步生成器循环 + 17 个 provider | `zenagent/agent/loop.py`、`zenagent/providers/` |
| 工具 / 技能扩展 | ✅ `AgentTool`、SKILL.md 技能、MCP、extensions、plugins | `zenagent/tools/`、`zenagent/skills.py`、`zenagent/mcp/`、`zenagent/extensions.py`、`zenagent/plugins/` |
| 连接器的凭据面 | ✅ plugin manifest 的 `CredentialSpec`（只是还没有连接器用它） | `zenagent/plugins/` |
| 持久化角色 Agent | ✅ Swarm：manifest + @mention 路由 + append-only room | `zenagent/app/swarm/`、`~/.zenagent/agents/<id>/` |
| 项目内记忆 | ✅ journal + entries、索引注入（**仅 per-project**） | `zenagent/agent/memory_store.py` |
| 预算 / 安全 | ✅ 预算注册表、成本上限、审批流 | `zenagent/limits.py`、`app/modes.py` |
| **消息渠道**（IM / 邮件收发） | ❌ 完全没有（grep 无任何 connector） | — |
| **定时 / 主动唤醒**（cron） | ❌ 只有后台 bash job（`tools/jobs.py`），无 cron、无心跳 | — |
| **跨项目个人记忆** | ❌ 记忆库挂在 `<project>/.zenagent/memory/`，无个人 scope | — |
| **通知系统** | ❌ 仅 LSP/UI 内部 notify，无对外通知 | — |

RPC ops 族现状（`zenagent/cli/rpc/`）：`ops_config / ops_files / ops_git / ops_plugins / ops_runs / ops_secrets / ops_session / ops_skills / ops_supervisor / ops_swarm / ops_workspace / ops_zen`——没有任何渠道、调度、个人记忆类的 ops。**四件套是从零新建，但全部可以架在现有扩展面之上，不需要动内核循环。**

## 2. 参考架构：personal Agent 的通用形态

本品类标杆是 **OpenClaw**（ex-Clawdbot）。它的架构（见参考资料 1、2）：

```
消息渠道 (WhatsApp / Telegram / Discord / 邮件 …)      ← 你主动找它
        ↓
   Gateway 控制面（常驻 WebSocket 服务）
   ├─ 渠道适配器：各平台消息收发 / 归一化（15+ 平台）
   ├─ Scheduler：cron 唤醒 + 维护任务                  ← 它主动找你
   └─ 路由到 Agent 循环（dual-loop ReAct）
        ↓
   记忆（分层：常驻核心 / 对话召回 / 归档检索，参考 Letta）
   连接器（邮件 / 日历 / 文件…，各自带凭据）
   安全层（预算、审批、沙箱）
```

两个关键子系统值得单独对标：

- **主动性的来源是 Scheduler，不是模型。** OpenClaw 的 Gateway 内核有专门的 `GatewayScheduler`，一个 host timer 驱动所有注册任务（cron 唤醒 + 维护）。没有这个，agent 只能被动应答，"personal" 就名不副实。
- **记忆分三层**（Letta / MemGPT 的贡献）：core memory（常驻上下文、agent 可自编辑的记忆块）、recall memory（对话史检索）、archival memory（长期归档 + 向量检索）。zen 的 `memory_store.py` 目前只有一层半（索引注入 + 条目文件），没有检索层，也没有跨项目 scope。

核心洞见：**personal Agent 与 coding Agent 的差别不在内核循环，而在"渠道 + 主动唤醒 + 个人记忆 + 连接器"四件套**——前三件决定它以什么形态出现在你的生活里，第四件决定它能替你碰到多少东西。

## 3. 三条落地路径

| | A. 前置网关 | B. Zen 原生（推荐） | C. Swarm 人格化 |
|---|---|---|---|
| 做法 | 独立 gateway 进程管渠道 + cron，通过 `zen rpc` / headless 驱动 zen 会话 | RPC 宿主新增 `ops_personal`（渠道、调度、个人记忆 scope），连接器走现有 plugin 体系 | 复用 Swarm 持久角色，加"管家"角色 + 渠道桥 + cron 到 swarm supervisor |
| 内核改动 | 零 | 中（新 ops 族 + 记忆 scope，须过 `test_layering.py`） | 小（swarm 是现成的，但要外挂渠道桥） |
| 进程 / 协议 | 两个进程、两套扩展面 | 单进程单协议；TS codegen 管线（`scripts/gen_rpc_ts.py`）直接复用给桌面端 | 单进程，但 swarm 目前面向编码场景 |
| 出活速度 | 最快 | 中 | 中 |
| 主要风险 | 双扩展面长期漂移；zen 侧只是"被驱动" | 动内核，分层纪律要守住 | 把编码 Swarm 硬拗成生活助手，语义不合 |

**推荐：B 为主、C 借型。** 理由：zen 的分层与扩展面（extensions / plugins / RPC ops）本来就是为"不动内核加能力"设计的，personal 模式做成 RPC 宿主里的新 ops 族最顺路，且桌面端 TS 类型自动跟进；Swarm 的 manifest / @mention / 持久 room 模型直接当"多角色人格"的底子（管家、研究员、记账员……），OpenClaw 的 Gateway 形态作为渠道层的蓝本。

## 4. 同类高星开源项目盘点

外部数据截至 2026-11-19；星数是搜索报道口径（不同时间点差异很大，如 OpenClaw 从年初 ~9K 到 2026-04 报道 150K–392K），只作量级参考。

### 4.1 Personal Assistant 品类（直接对标）

| 项目 | 星数（约，报道口径） | 许可 | 特点 / 借鉴点 |
|---|---|---|---|
| [OpenClaw](https://github.com/openclaw/openclaw)（ex-Clawdbot） | 150K–392K（2026-04 各报道不一） | MIT | 自托管个人助理，15+ 消息渠道，Gateway + 渠道适配器 + dual-loop ReAct + Scheduler；插件可加渠道 / provider / 语音 / 媒体生成。**看产品形态与 Gateway 设计** |
| Hermes Agent（Nous Research） | — | — | 口碑最好的开源实现之一，Docker 可选后端。看模型侧的深度 |
| NanoClaw | — | — | 本地、免费、Docker 优先的轻量替代 |
| LocalGPT | — | — | Rust 写的 local-first 助手，看本地优先取舍 |

### 4.2 记忆运行时（personal Agent 的关键子系统）

| 项目 | 星数（约） | 许可 | 特点 / 借鉴点 |
|---|---|---|---|
| [Letta](https://github.com/letta-ai/letta)（ex-MemGPT） | ~25K | Apache-2.0 | 源自 MemGPT 论文：core / recall / archival 三层记忆，agent 可自编辑记忆块，有状态跨会话。**看记忆分层与自编辑机制** |
| Mem0 | ~48–60K | — | 记忆层 / RAG 路线，是库不是运行时 |
| Zep | — | — | 记忆平台， temporal knowledge graph 路线 |

### 4.3 通用 Agent 框架（自建逻辑时用，非 personal 品类）

LangGraph、AutoGen / AG2、CrewAI、OpenAI Agents SDK（~27K，月下载 1030 万）——构建自定义 agent 逻辑的主流选择；zen 内核能力对标这一层，不是差距所在。

### 4.4 自托管平台

Dify（可视化 LLM 应用 / agent 构建）、n8n（工作流自动化 + AI agent）、LibreChat（自托管 ChatGPT 替代）、Langflow（拖拽构建）、OpenHands（自主编码，编码向）。

**借鉴排序：OpenClaw 看产品形态与 Gateway 设计 → Letta 看记忆分层 → 自己的 Swarm 看角色持久化。** zen 内核已不输这些框架，差距全在四件套。

## 5. 待决问题（决定架构走向，进入设计前必须回答）

1. **主交互界面是什么？** 像OpenClaw 活在微信 / Telegram / 邮件里；还是长在 zen 桌面端里的常驻伙伴；还是主要靠定时任务主动汇报、用户偶尔交互。三种形态对渠道层与调度层的权重完全不同，直接决定路径 B 的 ops 族切分。
2. **个人记忆的边界**：跨项目共享到什么程度（`~/.zenagent/memory/` 全局库 + 项目库双层？），要不要引入检索层（archival + 向量）。
3. **主动性边界**：cron 唤醒后的行为要不要过审批（预算 / 骚扰频率上限），沿用 `limits.py` 还是新开预算域。
4. **连接器优先级**：邮件 / 日历 / IM / 文件同步，先做哪个（决定第一个用 `CredentialSpec` 的 plugin）。

## 6. 参考资料

1. OpenClaw 官方架构文档：<https://docs.openclaw.ai/concepts/architecture>
2. OpenClaw 系统架构分析（第三方）：<https://ppaolo.substack.com/p/openclaw-system-architecture-overview>
3. OpenClaw 仓库：<https://github.com/openclaw/openclaw>
4. Letta（ex-MemGPT）仓库：<https://github.com/letta-ai/letta>
5. Firecrawl：Best Open Source Agent Frameworks：<https://www.firecrawl.dev/blog/best-open-source-agent-frameworks>
6. awesome-agent-frameworks：<https://github.com/alexbevi/awesome-agent-frameworks>
7. 本仓库：`docs/architecture.md`、`zenagent/cli/rpc/`、`zenagent/agent/memory_store.py`、`zenagent/app/swarm/`
