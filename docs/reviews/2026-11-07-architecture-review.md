# 代码架构审查(2026-11-07,全栈含 desktop,对标 PI-Desktop)

范围:`zenagent/`(177 文件 / 38.6k 行 Python)、`desktop/`(Tauri 2 + React 18,~9.5k 行 TS + 724 行 Rust)、对标仓库 `/Users/mac/PI-Desktop`(0.21.0)。上一份 [2026-11-06 审查](2026-11-06-architecture-review.md) 覆盖 `desktop/` 以外的 Python 侧;本份补上 GUI,并把三个前端(TUI/RPC/REPL)当作一个系统看。

**基线证据**(审查当日 HEAD `dda968f`):`uv run pytest -q` → **2280 passed**(299s);`uv run ruff check` → All checks passed。desktop:`pnpm build`、`pnpm test`(109 用例)、`pnpm e2e`(48 用例)为 CI 常绿基线。

**方法**:按四个面并行派出只读评审员(核心内核 / TUI 三前端 / GUI 桥与状态 / PI-Desktop 架构),回来后由审查者对全部承重结论逐条用 grep 复核(文中标 ✅ 的条目均为本人读码确认),非仅凭转述。

---

## 总体结论

**架构是健康的。** `docs/architecture.md` 的"六层、依赖只向下"不是纸面文章——它被 `tests/test_layering.py` 机械钉住,本次抽查全部 666 条包内 import,核心没有任何一处反向依赖 UI 层。运行时依赖仅 `httpx`,扩展面(工具/Provider/Skill/Extension/MCP)全部低耦合一致。**存在的债务不是结构问题,而是压缩不足**:同一件事在 2~4 个前端各写一份,集中在五个最大的文件里;外加两个"契约寄生在实现里"的错位和 GUI 的三道缺失防线。

```
健康度记分卡
┌────────────┬──────┬─────────────────────────────────────────┐
│ 低依赖      │ ✅ 满分│ 运行时仅 httpx;MCP/YAML/重试全部手写     │
│ 分层纪律    │ ✅ 满分│ kernel←app←cli 单向;test_layering 钉死   │
│ 可扩展性    │ ✅ 良好│ registry/AgentTool/ExtensionAPI 低耦合   │
│ 简洁性      │ ✅ 内核│ kernel ~15.4k 行无神文件;债务在装配层     │
│ 高效性      │ ⚠️ 大体│ 3 处事件循环同步 IO + 每次流新建连接       │
│ 前端去重    │ ⚠️ 欠账│ usage×4、命令表×2、装配×4、事件解码×4     │
└────────────┴──────┴─────────────────────────────────────────┘
```

---

## 一、核心内核(`types/events/agent/providers/tools/mcp/prompts`)

### ✅ 低依赖:教科书级

- 运行时依赖**只有 `httpx>=0.28.1`**;`prompt-toolkit/rich/textual` 全隔离在 `cli` optional extra。MCP 客户端为手写 JSON-RPC(`mcp/client.py`,零 SDK)、`catalog.yaml` 手写解析器(不引 pyyaml)、TOML 用 stdlib `tomllib`。`project.py` 刻意 stdlib-only(`project.py:17-18` 有注释说明动机),内核可安全 import。

### ✅ 分层与依赖方向:单向,真实成立

- ✅ `tools/`、`mcp/`、`skills.py`、`extensions.py` 只 import `loop.py` 的协议类型(`AgentTool` @ loop.py:100),loop 从不反向 import 它们——工具经 `AgentConfig.tools` 注入(loop.py:184),内核无工具名硬编码(`approval` 档位声明替代了名字表,loop.py:110-114)。
- ✅ 全包仅 **1 处**真实循环打破:`loop.py:286` 函数内 import `tool_exec`,有注释、属刻意(因 `tool_exec.py:17` 模块级 import loop 词汇)。
- ✅ 支撑模块全为叶子监听者,不在热路径:`telemetry` 仅 `app/metrics.py:11` 引;`trace` 仅 `app/runtime.py:90`;`update_check` 仅 `cli/tui/app_parts/update_notice.py:5`;**`crash.py` 包内 0 importer(死代码)**。
- 内核文件规模健康:loop 540 / agent 293 / tool_exec 290 / types 355 / events 118,无内核神文件。

### ⚠️ 高效性:三处摩擦

1. **4 个适配器每次 `stream()` 新建 `AsyncClient`**(`openai_completions.py:639`+`:650`、`anthropic_messages.py:542`、`google_generative_ai.py:242`、`openai_responses.py:292`)——跨 turn 无连接池复用。
2. **事件循环上的同步 IO**:`tools/fs.py:172` 的 `path.read_bytes()`、`agent/session.py:399` 每条消息 append 一次 open/close(持久性优先属刻意,但仍可 to_thread 保序)、`trace.py:88`/`telemetry.py:104` 异步监听器内同步写盘。
3. `load_catalog()` 每次重读重解析 YAML(`catalog.py:387-390`),未缓存(低频,轻微)。

### 错位契约(按修复性价比排序)

| # | 问题 | 证据(均 ✅ 复核) | 影响 |
|---|------|------|------|
| 1 | **`StreamOptions` 寄生在具体适配器** | 定义于 `openai_completions.py:59`,被通用 loop(loop.py:51)、其余 3 适配器、registry(:36)、app(compact.py:17 / btw.py:14)共 7 处 import | 厂商中立契约随厂商实现改名波及 8 文件 |
| 2 | **工具协议住在 loop.py** | `ToolResultAgent/AgentTool/ToolContext` @ loop.py:82/100/144(✅),是整个扩展面的 import 目标 | loop 改动波及全部扩展;顺带造成唯一的延迟 import |
| 3 | **`tools/task.py` 是穿工具外衣的子代理运行时** | 853 行 = 工具面 + TaskManager 进程监管(:280)+ 看门狗(:573)+ transcript 预算 + 子 Agent 构造(:710),直连 `agent.agent/artifacts/subagents` | tools→agent 最重的一条边,持续吸策略 |
| 4 | `crash.py` 死代码 | `install_excepthook`(:53)从未被调用 | 要么接线要么删 |
| 5 | 根包门面缺失 | `zenagent/__init__.py` 仅 4 行 `__version__`(✅) | 库用户无入口提示;~10 行 re-export 即可 |

### ✅ 扩展面:一致且低耦合

Provider registry(`register_stream_fn`,registry.py:55-68)、`AgentTool` 自声明 dataclass 协议、`ExtensionAPI` 窄能力插件(extensions.py:43-68,失败隔离 :114-117)、skills 文件系统发现(零代码)、MCP 动态桥(`mcp__<server>__<tool>` 命名,晚重连)、`AgentConfig` 钩子组(`before_tool_call`/`transform_context` 等,loop.py:188-205)——app 层正是靠钩子挂进内核而不碰它。**值得注意:`app/runtime.py:604-622` 的工具装配本来就是 builder 注入模式(`build_memory_tool(store)`),下面的全局钩子问题是异类而非常态。**

---

## 二、TUI 与三个前端(`app/` harness + `cli/`)

**设计是对的:一个 harness,五个前端。** `app/` 是前端中立的装配与状态层(`Runtime.bootstrap` 唯一装配入口,runtime.py:1-6 自述),四个进程内消费者(REPL、headless `-p`、Textual TUI、JSONL RPC)全部走它;桌面端是第五个客户端、只碰 JSONL 线协议。✅ 边界经查证:`app/` 不 import 任何 UI 库(`__init__.py:1-5` 规定,grep 零命中);核心不 import app/cli(仅 docstring 提及)。

### 重复清单(前端债务的主要标的,均 ✅ 复核)

1. **Usage 聚合写了 4 份**:`views.py:434 usage_total`(正主)、`rpc.py:195 _usage`、`render.py:143 aggregate_usage`、`main.py:102 _usage_and_counts`——同一循环体四种抄写,TUI 状态栏与 REPL 状态行走两条代码路径。
2. **命令词表双源**:`app/commands.py:814` 注册表(46 条)vs `cli/completion.py:33` 静态 `SLASH_COMMANDS`(42 条,平行 `cmd.repl.*` i18n key)。REPL 补全用静态表(:405),TUI 用活注册表(app_parts/hints.py:20)→ **往注册表加命令,REPL 补全静默缺失**;completion.py:5-6 自称 "one source of truth",名不符实。
3. **装配仪式复制 4 份**(broker→RuntimeOptions→bootstrap→ChatController→AppServices→set_confirm/ask @ main.py:604-668 / repl.py:461-654 / rpc.py:1029-1104 / main.py:188-236),且**已漂移过一次**:`app/permissions.py:497-502` 注释自认"最后只有 RPC 转发了 `read_only`"。
4. **进程级全局钩子**:`tools/ask.py set_ask_handler`(:64)、`tools/task.py:208 set_subagent_observer`(三前端 set/reset 调用点 ✅:repl.py:942/1038、rpc.py:1104/1108、tui/app.py:392/399)——绕过 Runtime 的隐藏耦合边,同进程双前端互相踩;tui/app.py:395 注释("so a later headless run degrades, not hangs")表明故障已知、打了补丁而非消除。
5. **事件解码器 4 份**:`_PlainOutput`(main.py:54-99)、`_EventPrinter`(repl.py:283-373)、`TranscriptBuilder.apply`(views.py:506-545,正主)、`serialize_event`(rpc.py:135-192)。REPL 最分化:绕开 TranscriptBuilder 手绘一切(:94-256),享受不到视图层的孤儿配对/稳定行 id/notice 修复;RPC 线上同时带原始事件流 + builder 快照两套模型(rpc.py:648-681),桌面端被迫做双模型调和。

### 神文件(全部在前端层)

| 文件 | 行数 | 病灶 |
|---|---|---|
| `zenagent/cli/rpc/` | 1181 | 三职:线编解码 + 18 类请求服务端 + 复刻 main.py 全部旗标的 argparse(:1112-1159);另重写 usage/摊平/图片读取 |
| `cli/repl.py` | 1045 | 渲染器 + 主题状态 + 主循环 + 单 stdin 协议四职;:579 与 :726 重复 reload 块 |
| `cli/tui/screens/modals.py` | 1042 | 13 个互不相关 Modal 类 grab-bag |
| `cli/main.py` | 747 | 解析器 + headless 前端 + 路由 + 内联 TUI 装配(:639-668,与死代码 `cli/tui/run.py:run_tui` 二选一) |
| `app/runtime.py` | 887 | 装配根、37 条 import——大但单职,**可接受**(全项目唯一"什么都知道"的地方) |

其余:`app/commands.py`(868)单职可接受;`tools/todo.py`(784)三受众一文件。

---

## 三、GUI(`desktop/`)

**底子很好**:Rust 壳仅 3 文件 ~724 行、只做进程管道(`rpc_start/send/stop/probe`),域协议零 Rust 序列化;Tauri capabilities 最小化(仅 core/event/dialog/updater);markdown 经唯一 DOMPurify 关口(`ui/chat/markdown.ts:48`);e2e 伪造桥不伪造应用(真 React 树 + 确定性帧);~14 个纯逻辑 helper + `node --test` 109 用例零框架;更新器按 pubkey 特性启用。**整体是一个缩水版 PI-Desktop 的正确方向。**

| # | 问题 | 证据(✅) | 严重度 |
|---|---|---|---|
| 1 | **CSP 完全关闭** | `tauri.conf.json:26 "csp": null`;sanitizer 是两个 `dangerouslySetInnerHTML`(AssistantNodeView.tsx:74、FilesPanel.tsx:214)前唯一防线 | P0,免费修 |
| 2 | **线协议 3+1 处手工同步** | `rpc.py` ↔ `types.ts`(424 行,types.ts:2 自述人肉核对)↔ e2e mock(app.spec.ts:96-354)↔ Rust 信封;无 schema/契约测试;`ToolCallView` 无判别字段,靠 `'kind' in view` 启发式(chatStore.ts:158) | P1,静默漂移温床 |
| 3 | **`rpc_start` 可阻塞主线程数秒** | `login_shell_env`(rpc.rs:229)阻塞 `output()` 跑 `$SHELL -l -i -c 'env -0'` **无超时**;同文件 `version_of` 有 `recv_timeout(2s)` 正确范式(rpc.rs:511)未复用 | P0 |
| 4 | 流式期间 O(n) 扫描 | 每个 text_delta 全量 `nodes.find`+`findIndex`(chatStore.ts:300/:260);tool 有 `cards` O(1) 索引(:119),assistant 节点没有 | P1 |
| 5 | 静默吞错 + 2s 轮询 | `.catch(() => {})` 成片(appStore.ts:195,214,226…);面板骑 2s status 轮询;超时迟到结果可漏成重复 usage 行 | P2 |

Python 侧会话是唯一写者、桌面持快照投影 + 事件补丁、重同步触发点有注释(appStore.ts:356-361)——"双状态但单写者"的取舍清醒,保留。

---

## 四、对标 PI-Desktop

**学(按性价比):**
1. **协议版本握手**——PI 双端 `PROTOCOL_VERSION=11` boot 时 `app.handshake` 校验;本项目的 rpc.py 有 "protocol v2" docstring 但桌面启动**不校验版本**,协议升级会静默崩。最高杠杆的一行机制。
2. **机械架构门**——PI `check-architecture.mjs` LOC 预算 + `allowlist.json` 显式债务登记;本项目已有 test_layering,补行数门防神文件再生。
3. **契约测试而非 codegen**——注意 PI 也没有 schema/codegen,靠运行时握手 + 契约测试;对本项目最便宜的是"读双端断言方法集一致"的测试。
4. **崩溃策略表 + 具名启动失败**——PI 每类组件死亡写明"保留什么/重启什么/绝不重放什么",boot 失败具名(`DB_SCHEMA_TOO_NEW`);本项目 `runtime_probe` 分类已是雏形,可沿此收编 `crash.py`。
5. **最小权限侧车**——PI 的 agent 进程经显式方法白名单代理访问宿主,凭据回调下发、agent 不持密钥;本项目当前把整个登录 shell 环境灌给子进程(rpc.rs:287),中期改按需注入。

**不学(对小项目属过度工程):** 双语言 Rust host、124 个手写 e2e 脚本、~310 ADR + 多级 PR 门、细粒度资源配额、插件平台。**本项目"单 Python 内核 + stdio JSONL + JSONL 会话文件"恰好命中 PI 沉淀的最优形态,进程模型一个字不要动。**

---

## 五、修复清单

详见实施计划 [`docs/superpowers/plans/2026-11-07-architecture-cleanup.md`](../superpowers/plans/2026-11-07-architecture-cleanup.md)(任务级 TDD 步骤)。摘要:

- **P0**:StreamOptions 迁 `providers/stream_options.py`;工具协议抽 `agent/protocol.py`(顺带解除 loop 延迟 import);usage 四合一;命令词表归注册表;GUI 严格 CSP + `login_shell_env` 超时;chatStore 反向定位;`crash.py` 接线。
- **P1**:RPC `hello` 版本握手 + 双端契约测试;统一装配 `app/bootstrap.py`(四模式 `read_only` 全透传参数化钉死);全局钩子 DI 化(沿用 runtime 既有 builder 注入模式);`ToolCallView` 显式 kind;`task.py` 拆 `agent/subagents_runtime.py`;共享 AsyncClient + to_thread 化同步 IO。
- **P2**:`__init__` 门面 + 过期注释;LOC 预算门 + allowlist;modals/rpc/repl 机械拆分。

**一句话:内核保持现状,火力集中在"前端层去重"与"GUI 防线"两条线;进程模型与分层结构不动。**
