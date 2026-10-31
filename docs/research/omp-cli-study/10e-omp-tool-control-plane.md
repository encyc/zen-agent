# 10e · omp 工具控制面（契约 / 加载 / 审批 / 超时 / 流式 / 预算 / 并行）

> **基线**
> - **omp**：本机安装包 `@oh-my-pi/pi-coding-agent@18.14.0` + `@oh-my-pi/pi-agent-core@18.14.0`（源码目录，非 git 快照；行号以该版本为准）。四个路径缩写：
>   - `core/` = `/Users/mac/.bun/install/global/node_modules/@oh-my-pi/pi-agent-core/src/`
>   - `omp/` = `/Users/mac/.bun/install/global/node_modules/@oh-my-pi/pi-coding-agent/src/`
>   - `tui/` = `/Users/mac/.bun/install/global/node_modules/@oh-my-pi/pi-tui/src/`
> - **ZenAgent**：`HEAD 2aab733`（v0.3.0）+ 工作树（仅 `docs/guide/tui.md`、`tests/test_tui_rows.py`、`zenagent/cli/tui/widgets/rows.py` 三处 TUI 改动，与工具层无关）。路径缩写 `za/` = `/Users/mac/zen-agent/zenagent/`。
> - **范围**：只研究"工具怎么被建模、装载、约束、执行、回收"的机制；单个工具的语义对比见 `04-transcript-and-tools.md`，TUI 渲染/命令/键位见 `04`、`08`、`09`。所有 omp 结论均落到具体文件行；ZenAgent 现状逐条回源码核对（非引用旧文档）。
> - **标注**：「推断」= 从代码结构推出、未运行验证的结论；「实测风险」= 代码上成立但未跑 case 的风险。
> - 本文不重复 09 的 TUI 结论，也不把 09 的"必要性总表"再抄一遍；若与 09 冲突，以本文的源码证据为准。

---

## 0. 结论速览

1. **omp 的工具控制面不是"更多工具"，而是把工具当可调度资源**：一个 `AgentTool` 声明里同时含执行、展示、审批、并发、中断、推测、截断七类元数据（`core/types.ts:1139-1274`）。真正撑起体验的是这些**声明 + 统一执行器**，不是工具数量。
2. **对最小 Python 内核收益最大的三件事**（按 ROI 排序）：① 统一结果预算/截断 + 标准 notice（§3）；② 工具级声明 + 统一审批解析（tier/target/记忆键，§4）；③ 扩展 `ToolResult`/`ToolContext` 契约（context 通道、`useless`、错误类别，§1/§7）。这三件都不依赖 omp 的重型子系统。
3. **omp 的超时不是内核调度器，而是"工具参数钳制表 + 工具内实现"**（`omp/tools/tool-timeouts.ts:10-39`，调用点在 `bash.ts:1223`、`eval.ts:516` 等）。内核只负责传 `AbortSignal`。所以"加超时"对 ZenAgent 是最便宜的改造（§5）。
4. **omp 的工具加载压缩（xd:// 挂载）是 schema token 预算方案，不是能力方案**：被挂载的工具仍然可执行、可审批，只是 schema 不进请求（`omp/tools/xdev.ts:1-30`）。**同类的 BM25 工具发现已在 18.14.0 删除**（`omp/config/settings.ts:3243-3259`），说明这层复杂度的收益不稳定。ZenAgent 9 个内置工具**不需要**；MCP 工具数上去以后再做（§2）。
5. **推测执行默认关闭且只支持本地读**（`omp/tools/settings.ts:910-919`；实现仅 `read`/`eval`/`task`：`omp/tools/read.ts:1060`、`omp/tools/eval.ts:438`、`omp/task/index.ts:796`）。这是为"工具链串行等待"付的 900 行复杂度，最小内核**明确不做**（§6）。
6. **omp 的"结束 run"不是结果字段，而是 abort 语义**：工具主动 `agent.abort(TERMINAL_TOOL_RESULT_ABORT_REASON)`（`omp/session/agent-session.ts:5580-5582`，常量在 `core/agent-loop.ts:194-198`）。ZenAgent 的 `terminate` 字段更简单，但判定是 `all(...)`（`za/agent/loop.py:521-523`），语义比 omp 弱，需在文档里写清（§1）。
7. **审批的真正难点是"目标键"不是"等级"**：omp 把审批记忆键做成 `tier\0path` 两层存储（session 内存 + SQLite 跨会话，`omp/tools/approval-cache.ts:25-26,38,55,126`）。ZenAgent 目前只按工具名记忆（`za/app/permissions.py:130`），一次 allow 等于整类放行——这是 v0.4 最值得补的安全缺口（§4）。
8. **流式 partial 在 omp 是一等公民，但只进 UI 不进上下文**：`onUpdate → tool_execution_update`，run 结束后还有 `onDetachedToolUpdate` 兜底（`core/agent-loop.ts:3668-3678`）。ZenAgent 管线已存在且 bash 已在用（`za/agent/loop.py:249-259`、`za/tools/bash.py:124-148`），差距在"detached/后台任务"语义，P2（§6）。
9. **错误契约上 omp 与 ZenAgent 走的是相反路线**：omp 让工具 `throw ToolError`、内核统一 `renderError` + `coerceToolResult` 规范化（`omp/tools/tool-errors.ts:13-67`、`core/agent-loop.ts:556-590`）；ZenAgent 让工具 `return is_error`。前者适合第三方工具/扩展，后者适合"工具自己最懂错误"。**ZenAgent 不必换路线，但要补错误类别**（§7）。
10. **并行语义两者并不等价**：omp 的 `exclusive` 与前后所有 `shared` 互斥（`core/agent-loop.ts:3786-3900`）；ZenAgent 的 `sequential` 只在串行组内部有序，仍与并行组同时跑（`za/agent/loop.py:322-331`）——这是**当前代码的真实缺陷**（实测风险），见 §1/§6。

---

## 1. 工具契约与生命周期

### 1.1 omp 做法

| 维度 | omp 机制（file:line） | 说明 |
|---|---|---|
| 工具身份 | `AgentTool` 继承 `Tool`（name/description/parameters），再加 `label`、`summary`、`docTopics()`（`core/types.ts:1145,1158,1169`） | `summary` 供发现索引/一行目录；`docTopics` 让工具把大文档拆成 `xd://<tool>/<topic>` 按需读 |
| 动态描述 | 工具可用 getter 让 description 随会话变化（如 write 在 device-only 模式下换文案，`omp/tools/write.ts:606-609`） | 描述是能力面的一部分，不是静态字符串 |
| 执行签名 | `execute(this, toolCallId, params, signal?, onUpdate?, context?)`（`core/types.ts:1110-1117`） | 5 个参数，最后一个是 host 构造的上下文 |
| 工具上下文 | `AgentToolContext { addAdditionalContext?, [SPECULATIVE_STREAM_SESSION]? }`（`core/types.ts:1095-1108`）；loop 侧 `ToolCallContext { batchId,index,total,toolCalls,providerMetadata,steeringSignal,addAdditionalContext }`（`core/types.ts:666-689`） | coding-agent 用 declaration merging 扩成 `ui/hasUI/toolNames/toolCall/settings/...`（`omp/tools/context.ts:5-33,34-66`），由 `getToolContext(toolCall)` 每调用构造（`core/agent-loop.ts:3641-3663`） |
| 结果 | `AgentToolResult { content, details?, isError?, providerMetadata?, useless? }`（`core/types.ts:1025-1037`） | `details` 只给 UI/日志；但 `details.meta` 会被统一包装器转成**模型可见 notice**（§3），这是唯一例外 |
| 非抛错失败 | `isError: true` 由 loop 规范化为 wire 上的 tool error（`core/agent-loop.ts:3685-3690`） | 工具聚合器可捕获子项错误后整体标错 |
| 无用结果 | `useless: true` → 压缩可安全丢弃（`core/types.ts:1036`；loop 透传 `core/agent-loop.ts:3493-3504`；loop guard 也用它跳过空 poll，`@oh-my-pi/pi-ai/src/utils/tool-call-loop-guard.ts:95-102`） | 上下文治理信号，模型仍能看到该结果一次 |
| 终止 run | 无 `terminate` 字段。工具主动 abort run：`TERMINAL_TOOL_RESULT_ABORT_REASON`（`core/agent-loop.ts:194-198`），coding-agent 的 `yield` 这么做（`omp/session/agent-session.ts:5575-5582`），loop 在 `core/agent-loop.ts:1725` 停在新一轮 provider 调用前 | 语义是"本 run 到此为止"，由工具显式发起 |
| 生命周期事件 | `tool_execution_start`（`core/agent-loop.ts:3472-3481, 3564-3570`）、`tool_execution_update`（3668-3678）、`tool_execution_end`（3485-3491） | 结果消息严格按请求顺序 flush（`flushResultMessages` 3462-3470），完成乱序不改变写回顺序 |
| 被跳过/中止 | skip/abort 都产生占位结果（`createToolSignalAbortedResult` 4081-4090、`createSkippedToolResult` 4092-4105；skip 路径 3497-3512、3577-3606） | 保证每个 toolCall 都有对应 result，历史可重放 |
| 并发声明 | `concurrency: "shared" / "exclusive" / (args)=>…`（`core/types.ts:1171-1176`）；调度在 `executeToolCalls`：exclusive 的起点等 `lastExclusive` + 全部 `sharedTasks`，且其后的 shared 也排在它之后（`core/agent-loop.ts:3786-3900`）；bash 只在 pty 时 exclusive（`omp/tools/bash.ts:645-647`），write/edit/todo/eval/ask 恒 exclusive（`omp/tools/write.ts:612`、`omp/edit/index.ts:365`） | 工具自己声明副作用冲突，内核据此插 barrier；默认 shared 是"乐观并行" |
| 只读声明旁路 | `mcpReadOnlyHint`（MCP `annotations.readOnlyHint`，`omp/mcp/tool-bridge.ts:726,757`）供 prewalk 判断，**审批仍保守按 write**（728） | 「读/写声明」与「审批等级」是两件事，不能混用 |
| 参数校验 | `validateAgentToolArguments`（`core/tool-arguments.ts:22-41`）：默认严格；`lenientArgValidation` 工具在 schema mismatch 时拿原始 args，但 malformed JSON 仍报错；内部哨兵 `__parseError/__rawJson` 永不传给工具 | 区分 `source="parsed"`（provider 流）与 `"payload"`（eval/xd:// 模型手写 JSON），防伪造哨兵 |
| intent 字段 | `intentTracing` 打开时向每个 schema 注入 `i` 字段，执行前剥离（`core/agent-loop.ts:3101-3118`；设置默认开 `omp/tools/settings.ts:883-895`） | 让模型先写"意图"，供 UI/循环检测/遥测用；错误长度直接变成校验错误 |
| before/after 钩子 | `BeforeToolCallResult { block/reason/args/additionalContext }`（`core/types.ts:917-928`）、`AfterToolCallResult`（930-949）；在 `prepareToolCallDispatch`（3076+）与执行段（3698-3735）调用 | 参数可被钩子改写且**改写后重新校验**；`additionalContext` 变成独立的 developer/system 消息（`core/tool-context.ts:44-54`） |

**为什么这么设计**：omp 的宿主形态多（TUI / RPC / ACP / SDK / Cursor / subagent），工具既可能来自内置、MCP、扩展、RPC host，也可能在 `xd://` 设备后面。所以契约必须把"谁能改参数、谁能拦执行、谁能注上下文、谁能提前停"全部显式化；否则每个宿主都要写一套私有逻辑。代价是 `AgentTool` 有 30+ 个可选字段，新人很难判断哪些是必须的。

### 1.2 ZenAgent 现状（已核对）

- 契约在 `za/agent/loop.py:68-105`：`ToolResultAgent(content/details/is_error/terminate)`（68-79）、`AgentTool(name/description/parameters/execution_mode/execute)`（82-105）。`execute` 是 4 参数 `(call_id, args, abort, on_update)`，**没有 context、没有 signal、没有 provider metadata**（99-105）。
- 钩子：`AgentConfig.before_tool_call/after_tool_call`（`za/agent/loop.py:133-136`），钩子只收到 `ToolCall` 和 `args`（240-243），**拿不到解析后的 tool 对象**，因此无法读取工具声明（这是 §4 目标级审批做不了的直接原因）。
- 执行顺序：`_execute_tool`（`za/agent/loop.py:209-275`）依次 unknown tool → arguments_error → 非 dict → `validate_arguments` → `before_tool_call` → `execute` → `after_tool_call`，任何异常降级为 `is_error`（256-257）。
- 结果字段 `details` 明确"永不进模型"（`za/agent/loop.py:71-73`），与 omp 一致；但没有 `useless`/`providerMetadata`；`terminate` 是本仓库自创（79）。
- 写回顺序与 omp 一致：按 assistant 请求顺序（`za/agent/loop.py:497-519`）。
- 并发：`AgentTool.execution_mode` 只有 `"parallel"/"sequential"`（`za/agent/loop.py:96`），`_run_tool_batch` 同时 `gather(run_parallel(), run_sequential())`（`za/agent/loop.py:322-331`）——`sequential` 只在**串行组内部**保序，仍与并行组同时跑。**与 omp 的 `exclusive` 语义不等价**（§0-10），是当前代码的真实缺陷。
- 批次终止判定：`if all(outcome.result.terminate ...)` 才结束 run（`za/agent/loop.py:521-523`）——**混合批次里一个工具要求 terminate、另一个没要求，就不会结束**；omp 是单个工具主动 abort 即可（推断：ZenAgent 若要保留 `terminate`，应改为"任一 terminate 即结束，其余标记 skipped"）。

### 1.3 差距与建议

| 差距 | 影响 | 建议 |
|---|---|---|
| 无 `ToolContext` | 工具拿不到 settings/UI/session/批次位置；审批、TUI 回填、后台任务都只能靠闭包 | **P0**：`execute(..., context: ToolContext \| None = None)`，字段最小集 `session_id/cwd/batch_id/index/total/add_context` |
| `before_tool_call` 只给 name+args | 无法按工具声明做 tier/target 审批 | **P0**：钩子签名改 `(tool_call, tool, args, context)`（保留旧签名兼容一层） |
| 无 `useless` | 压缩/上下文治理无法区分"空结果"和"有意义结果" | **P1**：加 `useless: bool`，loop 透传，压缩器读它 |
| 无 `providerMetadata` | 需要原样回放的 provider 特性（thinking 签名、tool result 元数据）无法承载 | **P2**（只有接 Anthropic/Responses 特殊字段时才需要） |
| `terminate` 语义是 all() | 混合批次不结束，行为反直觉 | **P1**：改成 any() + 其余 skipped；或对齐 omp 用 abort（更复杂，不建议） |
| `details` 无 meta 子层 | 无法做统一的截断/限制 notice（§3） | 归入 §3 的 P0 |

---

## 2. 工具加载与发现

### 2.1 omp 的机制分层

| 机制 | omp 实现（file:line） | 解决什么问题 | ZenAgent 是否需要 |
|---|---|---|---|
| 内置注册表 | `BUILTIN_TOOLS`（`omp/tools/index.ts:570-601`）+ `HIDDEN_TOOLS`（603-607：think/yield/goal） | 把"工具实现"和"工具是否启用/展示"分开 | **要**（现在只有平铺 `ALL_TOOLS`） |
| 选择计划 | `resolveBuiltinToolPlan`（635-823）：显式名单/限制名单/settings 门（`isToolAllowed` 747-809）/自动配对（AST 对、checkpoint+rewind、memory 后端等，685-745） | 40+ 工具不能全上；用规则推导"本会话该有哪些" | **部分要**：只要"启用集合 + hidden"两档 |
| 展示模式 | `loadMode: "essential" \| "discoverable"`（`core/types.ts:1056-1064`）；essential 白名单 `omp/tools/essential-tools.ts:23-37`；适配器边界默认 discoverable（`omp/tools/essential-tools.ts:45-48`、`omp/sdk.ts:1456`） | 区分"每次请求都带 schema"与"可按需装载" | **要（概念）**：最小版只做 hidden/defaultInactive |
| xd:// 挂载 | `isMountableUnderXdev`（`omp/tools/xdev.ts:85-88`）、`createTools` 里把 discoverable 从 tools 数组移走（`omp/tools/index.ts:908-924`）、协议 docs（`xdev.ts:1-30`）、执行 `dispatchXdevTool`（433-506）；`tools.xdev` 默认 **true**（`omp/tools/settings.ts:1012-1019`） | 压缩每请求 schema token；把 MCP/扩展工具藏在 read/write 后面 | **暂不做**（推断：工具 <15 时收益为负） |
| 挂载的硬约束 | `XDEV_KEEP_TOP_LEVEL`（todo/yield/ask/grep/web_search，`xdev.ts:58-74`）、`XDEV_TRANSPORT_TOOLS`（read/write，79）、需要 `write` 作为传输层、read-only 会话给 device-only write（`omp/tools/index.ts:879-906`） | 挂了不该挂的工具会让模型"找不到工具"（issue #5764/#5973 的教训） | 读一下这些坑即可，别复刻 |
| xd 文档预算 | 内联总预算 48k、单设备 10k、外部工具描述 200 字符（`xdev.ts:249-253`），策略 inline/builtins/catalog（默认 catalog，`settings.ts:1025-1044`） | 挂载不是免费：文档要么内联要么按需读，需要预算 | 这是"按需装载"的通用课题，**P2** |
| MCP 桥 | `MCPTool`（`omp/mcp/tool-bridge.ts:711-841`）/`DeferredMCPTool`（853-1025，连接延迟解析）；名字铸造 `mcp__<server>_<tool>` + 64 字符上限 + 8 位 hash（464-538）；重名去重（668-703）；结果含 `rawContent`/`structuredContent`（278-336）；可重试错误 + 重连重试一次（47-81、404-436） | MCP 工具名不可控、schema 不可信、连接会断 | **部分要**：ZenAgent 已有前缀与热替换，缺 annotations/重试/结果规范化 |
| MCP 定义缓存 | SQLite `agent.db` + config hash + 30 天 TTL（`omp/mcp/tool-cache.ts:10-12,20-23,31-87`） | 启动时不用等每个 server list_tools | **P2**（只影响启动延迟） |
| 扩展代理 | `applyToolProxy`（`omp/extensibility/tool-proxy.ts:6-34`）：把 wrapper 未声明的属性/方法懒转发给被包工具，且 ArkType schema 不能 bind | 包装工具（审批、meta notice、生命周期）不破坏 schema/`this` | **要**：Python 可用 `__getattr__` 等价 |
| 实时 reconcile | `SessionTools.#applyActiveToolsByName`（`omp/session/session-tools.ts:1272-1460`）：算 mount 集 → 重建 system prompt（签名比较，避免无谓刷新）→ `agent.setTools`；`reconcileBuiltinTools`（2235-2258）、`refreshMCPTools`（2497-2568）、`refreshRpcHostTools`（2570-2625） | 会话中设置/MCP/扩展变化要"立刻生效且只刷一次 prompt" | **P1**：统一 `ToolRegistry.reconcile()`，替换 REPL 的手写 `refresh_mcp` |

**为什么要有「加载」这层**：omp 的工具面包含内置 + MCP + 扩展 + RPC + 设备，全量进请求时 schema token 会挤掉真正的上下文；同时「启用（enabled）」与「展示（presented）」必须分开——工具可以启用但默认不展示（hidden/defaultInactive），也可以展示但 schema 不进请求（discoverable/xd://）。ZenAgent 现在只有「全部启用且全部展示」一档。

**BM25 tool discovery 已被删除**：`omp/config/settings.ts:3243-3259` 明确删除 `tools.discoveryMode`/`essentialOverride`/`mcp.discoveryMode`，且"no replacement"。`core/types.ts:1059` 的注释仍提到 BM25，属于文档残留（推断：代码里已无实现，全仓只有注释/迁移代码引用）。**结论：lazy schema discovery 这条路 omp 自己都退了，ZenAgent 不要做。**

### 2.2 ZenAgent 现状

- 静态清单：`za/tools/__init__.py:27-37`（9 个工具，全部每请求全量 schema）；`ToolDef` 在每轮重新构造（`za/agent/loop.py:401`）。
- 扩展：`za/extensions.py:57-58` 只 `register_tool` 进 list（91-130 加载），没有 wrapper/生命周期，扩展工具与内置工具在 loop 里无差别（这本身是优点）。
- MCP：`za/mcp/manager.py:25-26`（`mcp__` 前缀）、`tool_defs()` 158-172、`_make_tool` 174-196（直接用服务器 `input_schema`，无 annotations、无 approval、无截断）、`_to_result` 199-225（text/image/resource → 我们的 content，**不做大小预算**）；连接失败保留 `failed` 状态并退避重试（92-113）。
- 热更新：REPL 手写 `refresh_mcp()`（`za/cli/repl.py:647-661`，工具名集合变化时重建 tools + system prompt）和 `reload_runtime()`（663-693）；TUI 只在启动期经 `Runtime` 装配 MCP（`za/app/runtime.py:179-190`），**没有任何热更新/重连触发点**（`za/cli/tui/` 全目录 grep 不到 mcp）。
- 没有 hidden/defaultInactive/loadMode；没有"工具启用集合"抽象。

### 2.3 差距与建议

| 差距 | 影响 | 建议 |
|---|---|---|
| 无统一 registry/reconcile | 热更新靠 REPL 手写，TUI 缺失；每个前端都要重写一遍 | **P1**：`ToolRegistry`（名字→实例、active set、变更通知、可选 `rebuild_prompt` 回调） |
| 无 hidden/按需展示 | 内部工具（todo/skill/后台 wait）一旦加入就永久占 schema | **P1**：`AgentTool.hidden` + `default_inactive` 两字段足够 |
| MCP 工具无 annotations/结果预算 | 只读 MCP 也要审批；MCP 大结果直接进上下文 | **P1/P2**：`mcpReadOnlyHint` → read tier；MCP 结果走统一预算（§3） |
| 无 MCP 定义缓存 | 每次启动等所有 server `list_tools` | **P2**（纯启动优化） |
| 无 xd:// 等价 | MCP 多时 schema token 线性涨 | **P2/条件项**：先做"工具 schema 体积审计"，超过阈值再考虑简化版（只挂 MCP） |
| 扩展工具无包装 | 无法统一做审批/截断/生命周期 | 与 registry 合并解决；不需要 omp 的 proxy 复杂度 |

---

## 3. 输出预算与截断

先区分两件常被混淆的事：

- **请求级输出预算**：omp `core/output-budget.ts` 的 `fitOutputTokensToContextWindow`（81-96）把 `max_tokens` 压到"上下文窗口 − prompt − headway"：下限 `MIN_FITTED_OUTPUT_TOKENS=1024`（:8）、`OUTPUT_FIT_HEADWAY_TOKENS=64`（:38）、每消息固定 `MESSAGE_FRAMING_TOKENS=16`（:29）。它防的是 Chat Completions 系 400（prompt+max_tokens 超窗），**不是**工具结果截断。
- **结果级预算**：`omp/tools/output-meta.ts` + `OutputSink`，才是工具结果截断/落盘。

### 3.1 omp 做法（结果级）

| 环节 | 实现（file:line） | 关键数字 |
|---|---|---|
| 统一包装 | `wrapToolWithMetaNotice` 重写 `execute`（`omp/tools/output-meta.ts:780-800`），`wrappedExecute` 在工具返回后 spill + 追加 notice（744-778） | 每个工具自动获得，不需要工具自己写截断代码 |
| 落盘阈值 | `spillLargeResultToArtifact`（554-720）：文本总字节 > threshold 时 `sessionManager.saveArtifact(fullText, toolName)`（604-612） | `tools.artifactSpillThreshold` 默认 **50 KB**（`omp/tools/settings.ts:12-35`） |
| 内联保留 | 配置了 head 就中间省略（`truncateMiddle`），否则只保尾（`truncateTail`，636-651） | head 20 KB / tail 20 KB / tail 500 行（`settings.ts:38-58,60-82,106-125`）；artifact 单文件上限 16 MB（127-130） |
| 列宽 | 流式输出与 read 共用每行字节上限 | `tools.outputMaxColumns` 默认 **768**（`settings.ts:84-104`） |
| 模型如何被告知 | `appendOutputNotice`（454-470）把 `formatOutputNotice`（`tui/tools/output-meta.ts:304-`）拼到最后一个 text block 尾部 | 截断 notice 带方向/行号范围/`nextOffset`/`artifact://N`；限流 notice 带 "Use limit=N for more"（318-356） |
| 恢复指针 | artifact 存在时 notice 给 `artifact://N`；列截断给 `:raw:<line>` 选择器（`tui/tools/output-meta.ts:344-355`） | 模型可用 read 精确回读，而不是重跑工具 |
| 二次保护 | `pagedSource` 结果不再落盘（567-573）；MCP `rawContent` 在落盘时剪枝以免磁盘翻倍（676-715） | 防"artifact 套 artifact" |
| 流式执行器 | bash/pty/python/ssh 用 `OutputSink` 在产生时截断并同步写 artifact（`tui/tools/streaming-output.ts:927-990`；`omp/tools/bash-interactive.ts:26-35`） | 内存不会先爆 |
| 工具侧辅助 | `ToolResultBuilder` 提供 `truncation/limits/sourceUrl/diagnostics/error/useless`（`omp/tools/tool-result.ts:12-105`） | 工具只填元数据，notice 由包装器统一渲染 |

**为什么**：工具结果是最容易把上下文打爆的入口，而每个工具手写截断一定会漂移（ZenAgent 现状就是证据）。把"预算 + 落盘 + notice 语法"收敛到一处，才能让读取路径（read 的 `:raw`/行选择器）和写入路径（artifact）形成闭环。

### 3.2 ZenAgent 现状

| 工具 | 上限（file:line） | 超出后的行为 |
|---|---|---|
| bash | `MAX_OUTPUT_BYTES = 30_000`，head/tail 各半（`za/tools/bash.py:31-34`） | 中间插 `... [truncated N bytes] ...`（79-89），纯文本、无恢复指针 |
| read_file | `MAX_READ_LINES = 2000`（`za/tools/fs.py:32`） | 前 2000 行 + "[truncated: showing 2000 of N lines; read a smaller range or use grep]"（130-137） |
| list_dir | `MAX_LIST_ENTRIES = 500`（`za/tools/fs.py:35,223`） | 前 500，提示被截断 |
| edit_file diff | `MAX_DIFF_LINES = 200`（`za/tools/fs.py:41,58-61`） | 前 200 + "[N more diff lines]" |
| glob/grep | 500 / 200（`za/tools/search.py:24-26`） | 提示 stopped at N |
| fetch | `MAX_FETCH_CHARS = 12_000`（`za/tools/web/tools.py:29,104-105`） | 截断提示 |
| MCP | 无上限（`za/mcp/manager.py:199-225`） | 服务器给多大就多大 |

- 全是**工具内硬编码**，没有共享预算层、没有 artifact 落盘、没有统一 notice 语法、没有列宽限制；`details` 里的 `truncated/total_lines` 只给 UI（`za/tools/fs.py:137`、`za/tools/bash.py:200-205`）。
- 唯一的"上下文预算"是 `ContextGuard`：请求前按 85% 触发、丢最老整轮直到 70%（`za/config.py:43-45,321-363`）。**这不能替代结果预算**：单条工具结果仍可能一次吃掉几十 K token，且触发丢弃时用户无感知。
- 请求级：`max_tokens = options.max_tokens or model.max_tokens`（`za/providers/anthropic_messages.py:200-205`、`google_generative_ai.py:122`），**不做窗口拟合**。由于 85% 才 prune，prompt 落在 `(window − max_tokens, 0.85·window)` 区间时会 400（推断；DeepSeek 类 1M 窗口 + ~384k max_tokens 时区间很大）。

### 3.3 差距与建议

| 差距 | 建议 |
|---|---|
| 无统一结果预算/notice | **P0**：`post_process_result(tool, result, context)`：阈值 + head/tail + 标准 notice（`[truncated: ...]`）；所有工具、MCP、扩展自动获得，工具内的硬编码降级为兜底 |
| 无 artifact 落盘 | **P1**：超阈值结果写 `~/.zenagent/sessions/<id>/artifacts/N`（或会话目录），notice 给路径 + 行号；`read_file` 支持按行回读（已有行号基础）；这是"模型可恢复"的关键 |
| 无列宽 cap | **P1**：默认 512–768 字节/行，与 bash/read 共用；单行超长是日志类输出的主要爆点 |
| 无 per-result token 估算 | **P2**：用现有 token 估算器（`za/config.py:token_estimate`）做二次上限；字节阈值先够用 |
| 无请求级 max_tokens 拟合 | **P1**：`max_tokens = max(1024, min(model.max_tokens, window − prompt_est − headroom))`；改动 20 行、无新依赖、直接消灭一类 400 |
| 无 `useless` | 归入 §1 |

---

## 4. 审批与权限

### 4.1 omp 做法

**声明（工具侧）**：`approval?: ToolTier | { tier, reason?, override?, policy?: "allow"|"deny"|"prompt", policyKey? } | (args)=>同前`（`core/types.ts:1067-1089`）；省略按 `exec` 处理（1074）。等级序 `read < write < exec`（`omp/tools/approval.ts:132-136`），模式到最高可自动放行等级：`always-ask`/`always-ask-cached`→read、`write`→write、`yolo`→exec（153-158）。另有 `formatApprovalDetails(args)` 决定提示正文（`core/types.ts:1246-1247`）、`approvalCacheTargets(args)` 给出目标键（1249-1254）、`approvalRestrictionPaths(args)` 给出受限目录匹配路径（1256-1260）。

**解析（host 侧）**：`resolveApproval(tool,args,mode,userPolicies,grants,restrictions)`（`omp/tools/approval.ts:364-387`），顺序：

1. 工具声明（含函数式，动态按 args）→ 2. 用户 `tools.approval.<name|policyKey>` 覆盖（含改名工具的 legacy 键，只继承 deny/prompt，389-420）→ 3. 模式等级比较（442-509）→ 4. `always-ask-cached` 的目标记忆（193-214）→ 5. 受限目录一票升 prompt（`source:"restriction"`，364-387、569-592）。
- `deny` 直接抛 `denyError`（613-622）；模式默认值 `yolo`（`omp/tools/settings.ts:313-320`），但"用户策略仍权威"（442-460）。
- 会话级放行只对"模式引起的 prompt"生效，且键是 `subject\0tier`（601-608）——显式 prompt/override 不可记忆。

**为什么**：omp 的宿主多、工具来源杂，审批不能写死在某个前端里；同时「一次批准 = 整类放行」在真实使用中不可接受（`bash`、`write` 各一次就永久放开），所以必须把「声明（tier/policy）」「模式（自动放行等级）」「目标（记忆键）」三件事拆开。ZenAgent 把三者压成了一个按工具名的 gate，简单但粗。

**目标键与缓存**：
- 键归一化：绝对路径（缺省穿越符号链接、Windows 大小写折叠）`approvalCacheKeyForPath`（`omp/tools/approval-cache.ts:154-160`）、目录键尾部分隔符（162-166）、内部 URL/web URL 原样（`approval-targets.ts:100-115`）、递归搜索用不透明 `tree\0` 键防"目录授权误放行整棵树"（`approval-targets.ts:16-31`）。
- 两层存储：`SessionApprovals`（内存，会话级）+ `ApprovalCache`（`<agentDir>/approval-cache.db` SQLite，跨会话），键 `"<tier>\0<subject>"`，记录 `(key, tool, approved_at)`（`approval-cache.ts:14-16,25-26,38,55,84-124`）；`getApprovalGrants(dir, sessionId)` 组合两者（178-190）。
- 目录授权提示会列出该目录一层内容（`approval-targets.ts:120-175`、`approval.ts:764-798`），让用户知道"批准这个目录"意味着什么。

**执行点**：`ExtensionToolWrapper.execute`（`omp/extensibility/extensions/wrapper.ts:234-583`）：
- 先 `resolveApproval` 预检，`deny` 在触发任何 extension 事件前短路（269-281）；
- `tool_call` 事件可改参数，**改完重新解析审批**（347-359）——封死"批准一套、执行另一套"；
- `grantable` 只对模式 prompt 且无 provider safety check（379-384）；`sessionScope` 只在无目标键时启用（386-391）；
- 无 UI 时 fail-closed 并给出三条修复建议（434-457）；
- 批准后 `recordApprovalGrant`（473）、发 `tool_approval_requested/resolved` 事件（406-431）。

**Plan mode**：`enforcePlanModeWrite`（`omp/tools/plan-mode-guard.ts:129-150`）在工具内部拦截写/改名/删除，但允许写 `local://` 沙箱（80-103）；`targetsLocalSandbox` 做了 realpath 比较，防符号链接逃逸。注意这是"工具内防御"，与审批门（host 层）是双保险。

### 4.2 ZenAgent 现状

- 声明：**没有**。等级由静态集合 `READ_ONLY_TOOLS` 反推（`za/app/permissions.py:32`）。
- 解析：`PermissionGate.policy_refusal`（143-165）先查危险命令/`deny` glob/plan/`--read-only`，`check`（167-188）再走 allow glob → 只读 → full/--yes/会话 always → 交互询问；`_ask`（190-203）用 `input()`；`PermissionBroker`（218-280）叠加 TUI 弹窗与 `asyncio.Lock`（232），TUI 侧 120s 超时视为拒绝（`za/cli/tui/app.py:103-127`）。
- 记忆：`_always_allowed` 是**进程内按工具名**的 set（`za/app/permissions.py:130,198-200`）；Broker 另有 `_allowed` 集合（252-253）。没有目标键、没有跨会话。
- 没有 restricted folders；plan 模式是模式门（158-162），没有写沙箱（plan 下 `write_file` 一律拒绝，而不是允许写 plan 文件）。
- 所有 MCP 工具一律按"非只读"处理（不在 `READ_ONLY_TOOLS` 里，且 manager 没读 `annotations.readOnlyHint`）。
- 工具看到的模型错误文本是 block reason（`za/agent/loop.py:242-243`），可读但无类别。

### 4.3 差距与建议

| 差距 | 影响 | 建议 |
|---|---|---|
| 无工具声明（tier/targets/reason） | 无法按文件/目录记忆，无法让 MCP 只读工具免问 | **P0**：给 `AgentTool` 加 `approval(args) -> {tier, reason?, targets?}`；gate 改 `resolve(tool,args,mode,policies,grants)` |
| 只按工具名记忆 | 一次 allow 覆盖整类操作（最危险的是 `write_file`/`bash`） | **P1**：会话级目标缓存 `key = tier\0normalized_path`，目录授权只覆盖一层；不做 SQLite |
| 无 reason/正文格式 | 审批弹窗信息不足 | **P1**：`reason` + 工具详情行（复用现有 args preview，去掉 120 字符粗暴截断，改为每字段一行） |
| MCP 无 readOnlyHint | 只读工具也要问，噪音大 | **P1**（先读 annotation，无则保守 write） |
| 无受限目录 | 高危目录无法强制逐次确认 | **P2**：`[tools] restricted_folders`，命中即 `prompt` 且不可记忆 |
| 无 `override`/`policyKey` | 仅 dispatcher 形态需要（xd://、eval 桥） | **N**（除非做设备/转发工具） |

---

## 5. 超时与取消

### 5.1 omp 做法

- **超时是"参数表 + 工具内实现"**：`TOOL_TIMEOUTS`（`omp/tools/tool-timeouts.ts:10-20`）给了每个工具的 `default/min/max`（bash 300/1/3600、eval 30、fetch 20/1/45、lsp 20/5/300 等）；`clampTimeout(tool, raw, maxTimeoutSec)`（34-39）先应用全局 `tools.maxTimeout`（默认 0=不限，`omp/tools/settings.ts:941-`）再夹到 per-tool 范围；`resolveLspTimeouts` 还区分"整调用 deadline"与"等 server 响应"（57-70）。
- 应用点在各工具：`bash.ts:1223`、`eval.ts:516`、`browser.ts:332`、`computer.ts:278`、`ida.ts:210`、`debug.ts:597`。**内核不设 deadline**，只把 `AbortSignal` 传给 `execute`（`core/types.ts:1114`）。
- **取消**由内核对 `AbortSignal` 分类：
  - `interruptible` 工具（纯等待，如 `wait`/`vibe`）收到"外部 abort + steering abort + IRC abort"的合成 signal；其余工具只收外部 abort（`core/agent-loop.ts:3297-3318`）。
  - 中批 steering 只"软通知"（`steeringSignal`，只协作不杀，`core/types.ts:673-681`；`core/agent-loop.ts:3284-3289`），未开始的 `interruptible` 工具会被跳过（3497-3512、3577-3606）。
  - 两种专用 abort reason：`TERMINAL_TOOL_RESULT_ABORT_REASON`（run 结束，194-198）、`TOOL_INTERRUPT_ABORT_REASON`（steering/IRC 打断等待，201-205）。
  - 批次内以 250ms 轮询 steering/IRC/后台完成（`STEERING_INTERRUPT_POLL_MS` 209、watch 逻辑 3810-3860）。

**为什么内核不管 deadline**：工具类型差异太大（bash 5 分钟、fetch 20 秒、LSP 需要等 server），内核设统一 deadline 必然要么误杀要么形同虚设；更合理的分工是工具按表 clamp 参数、自己实现计时，内核只保证「取消信号一定传得到」。

交互式 PTY：`runInteractiveBashPty`（`omp/tools/bash-interactive.ts:14-90+`）把终端交给用户（PtySession + xterm overlay），输出仍进 `OutputSink`。它是 UI 能力，不是控制面必需。

### 5.2 ZenAgent 现状

- 只有 bash 有超时参数：`timeout_s` 默认 120（`za/tools/bash.py:107`），超时 `killpg` 整组（90-96、152-173），返回 `[timed out after Ns; process group killed]`（189-191）。**无 min/max、无全局上限、无表**。
- 网络工具固定 20s（`za/tools/web/net.py:14-22`，可被 `ZENAGENT_SEARCH_TIMEOUT` 覆盖），模型不可调。
- 取消：`AbortToken`（`za/events.py:91-118`）贯穿 run；loop 只在**每轮顶部**检查 `abort.aborted`（`za/agent/loop.py:371-374`），工具能自检（bash 用 `abort.wait()` 与读任务 race，`za/tools/bash.py:152-173`）；没有内核强杀、没有 per-tool 中断语义、没有 steering 触发的中断（steering 只在批次结束注入，`za/agent/loop.py:527-532`）。

### 5.3 差距与建议

| 差距 | 影响 | 建议 |
|---|---|---|
| 无统一超时表/全局上限 | 每个新工具自己拍脑袋；用户无法统一收口 | **P1**：`TOOL_TIMEOUTS` 表 + `clamp_timeout()`（Python 20 行）；bash/fetch 先接入 |
| 网络工具超时不可调 | 慢站点/大文件无解 | **P1**：fetch 暴露 `timeout_s` 并 clamp（web 工具是只读，风险低） |
| 无 interruptible 概念 | 未来的 wait/job/后台轮询工具无法被新消息打断，只能等满 | **P2**：工具声明 `interruptible: bool`，loop 在批次内 poll steering（先只对纯等待工具） |
| 无 detached 更新 | 后台任务结果只能等下一轮 | **P2**（依赖后台任务系统，见 §6） |
| PTY 交互 | — | **N**（TUI 自己的 shell 与 agent 工具无关） |

---

## 6. 流式/部分结果与推测

### 6.1 omp 做法

| 机制 | 实现（file:line） | 买到了什么 | ZenAgent 该不该要 |
|---|---|---|---|
| partial 结果 | `AgentToolUpdateCallback`（`core/types.ts:1040`）；loop 包成 `tool_execution_update` 事件（`core/agent-loop.ts:3668-3678`）；stream 已结束时走 `onDetachedToolUpdate`（`core/types.ts:358-364`、`core/agent-loop.ts:3674-3676`） | UI 实时进度；后台任务在 run 结束后仍能报 | **管线已要**（ZenAgent 已有，见下）；detached 是 P2 |
| 参数增量流 | `openArgStream`/`AgentToolArgStream`（`core/types.ts:1120-1136`）：`push(delta)/end(args)/cancel()`，`emit()` 变成 `tool_stream_update`；`edit` 是唯一实现者（`omp/edit/index.ts:478`） | 编辑类工具在参数还没流完时就画 diff 预览 | **N**（纯 UI 收益） |
| 真实 speculative execution | 工具声明 `speculation` 策略（`core/types.ts:1182`，策略类型 692-860）；实现 `core/speculative-execution.ts`（coordinator 234-968，fingerprint 145-173，默认 `maxInFlight=2`，`core/speculative-execution.ts:85`）；host 只授权 `direct.read`/`eval.read` + task 启动（`omp/speculation/host.ts:66-77,328+`）；开关默认 **false**（`omp/tools/settings.ts:910-919`） | 模型流式吐工具调用时**提前执行**只读操作，dispatch 时校验文件未变再 commit，否则丢弃重跑 | **N**：900 行 + 多文件指纹校验，收益只在"长串行读链"；v0.4 不做 |
| live steering | `LiveSteeringChannel`（`core/live-steering.ts:1-89`）：provider 能在响应流里接收新用户输入时，把 steering 直接送进去；被拒绝则退化为 boundary 注入（`accepted`/`deferred` 双队列） | 用户消息不必等模型这轮结束 | **N/P2**：强依赖 provider 能力（OpenAI Responses `response.steer`）；ZenAgent 的 turn 边界 steering 已够用 |
| run 汇总 | `AgentRunCollector`（`core/run-collector.ts:1-110`）：每次 run 的 chat/tool 记录、`ToolStatus = ok\|error\|skipped\|blocked\|timeout\|aborted`（22）、按工具名计数、available/invoked/unused 覆盖率（111-150），挂到 `agent_end` | 评测断言、telemetry、回归统计 | **P2**：ZenAgent 的 `RunMetrics` 只有 `tool_calls` 计数（`za/telemetry.py:28,49-56`），补 by-name/status/latency 很便宜 |

**为什么 omp 要做 speculation**（推断）：它的典型工作流是「read 一批文件 → 再决定」，模型流式输出工具参数时前面几个 read 已经可以开始跑；但这套「提前执行 + 指纹校验 + 不一致丢弃」只有在工具是**纯本地读**时才安全，所以 host 只授权 `direct.read`/`eval.read`，且默认关闭。对 ZenAgent 来说，当前并行组已经并发执行，speculation 顶多省掉流式那 1–2 秒，不值得。

### 6.2 ZenAgent 现状

- partial：`on_update` 已在契约里（`za/agent/loop.py:99-105`），loop 用 `asyncio.Queue` 把 `tool_execution_update` 转成 UI 事件（470-493），并有 `accepting` 标志丢弃结算后的迟到更新（247-259）。bash 是唯一使用者，阈值 4KB/0.5s（`za/tools/bash.py:33-34,124-148`）。
- 没有 detached：run 结束后工具再发 partial 无从投递（loop 已 `accepting=False`），也没有"后台任务"概念。
- 没有 speculation / 参数增量流 / live steering / run 汇总。

### 6.3 差距与建议

| 差距 | 建议 |
|---|---|
| 无 run 级工具统计 | **P2**：`agent_end` 附带 `{tool: name, status, duration_ms}` 汇总；`RunMetrics` 扩 `tools_by_name`。对 evals 与"哪个工具在烧钱"有直接价值 |
| 无 detached partial | **P2**：等后台任务系统（jobs）落地时一起做；现在做了没有消费者 |
| 无 speculation/arg stream/live steering | **N**（见上表理由） |

---

## 7. 错误契约与恢复

### 7.1 omp 做法

| 环节 | 实现（file:line） |
|---|---|
| 错误类型 | `ToolAbortError` + `throwIfAborted(signal)`（`omp/tools/tool-errors.ts:13-31`）；`ToolError`（带 `render()`，来自 `pi-tui/tools/tool-errors`）；`renderError(e)` 统一成模型可读文本（37-45）；worker 边界的克隆安全错误载荷（47-67） |
| loop 归一化 | 内核 catch 所有异常 → `content=[message]`、`isError=true`（`core/agent-loop.ts:3680-3695`）；`coerceToolResult` 再把第三方返回的畸形结果规范成错误（556-590）；`afterToolCall` 之后**再规范一次**（3698-3735），防扩展把 content 改成非法形状 |
| 包装层 | `wrapToolWithMetaNotice` 的 catch 重新抛 `new Error(renderError(e))`，让 loop 统一处理（`omp/tools/output-meta.ts:771-785`）；`ToolResultBuilder.error()` 用于"不抛异常的失败"（`omp/tools/tool-result.ts:79-83`） |
| schema 体系 | 主路径 typebox/arktype 定义 → `toolWireSchema` 推导 JSON Schema（`omp/tools/xdev.ts:113-114` 使用）；用户可用 JTD（RFC 8927）写 schema，`jtd-to-json-schema.ts:1-140` 支持 enum/elements/type(含 int/uint/float/timestamp)/values/properties+optionalProperties/discriminator(mapping inline 展开)/ref($defs)/nullable，`jtd-utils.ts` 做形态判定；结构化输出另有 `output-schema-validator.ts`。**不支持任意 JSON Schema 关键字**（无 oneOf/pattern/format 等） |
| 跨轮循环恢复 | `ToolCallLoopGuard`（`@oh-my-pi/pi-ai/src/utils/tool-call-loop-guard.ts:70-128`）：每轮把"name+规范化 args"排序后哈希，连续相同达阈值（默认 5，`omp/session/settings.ts:366-377`）判定循环；exempt 工具（poll 类）的"idle useless"不算打断（95-102）。命中后注入一条隐藏 corrective 消息（`omp/session/stream-guards.ts:224-273`，模板 `prompts/system/tool-call-loop-redirect.md`，渲染器 `session/tool-call-loop-redirect.ts:16-30`），不杀 run、只纠偏 |
| 重试 | 内核**不重试工具**；只有 MCP 连接层的一次重连重试（`omp/mcp/tool-bridge.ts:47-81,404-436`）和 provider 层重试。理由：工具可能有副作用，重试由工具自己决定 |
| 压缩信号 | `useless`（见 §1）让压缩/循环守卫区分"空 poll"与"有信息结果" |

**为什么是 throw 而不是返回错误**：omp 的工具既有内置（TypeScript 类）也有第三方（MCP/扩展/RPC），「抛异常 + 统一 render」能保证第三方不用懂宿主的错误格式；而「返回 is_error」要求每个工具自己造句，质量不可控。代价是必须在内核里做 `coerceToolResult` 规范化，防止第三方返回畸形结果污染历史。

### 7.2 ZenAgent 现状

- 零抛错路线：loop 把 unknown tool / JSON 解析失败（`arguments_error`，`za/types.py:99-111`）/ 非对象 / schema 失败 / 钩子 block / 执行异常全部转 `is_error`（`za/agent/loop.py:227-259`），异常文本截到 500 字符（65、257）。
- 工具自己返回 `is_error`（`za/tools/fs.py:110-139`、`za/tools/bash.py:186-200`），文本可操作（"file not found" / "not a file (it is a directory)"）。
- 校验：手写子集 `validate_arguments`（`za/agent/validate.py:48-87`）：顶层 object、required、属性标量/容器类型（bool 特殊处理），**不递归、不 anyOf、不 default、不拒绝多余字段**——这是与 omp "完整 typebox + lenient 例外" 的最大差异，但对 9 个自研工具已经够用（工具内部自防御）。
- 恢复：`max_turns=100` 兜底（`za/agent/loop.py:177`）；`length` 截断的 tool call 一律不执行并让模型重发（447-460），连续 2 次放弃（179-180、524）；provider 级重试在 `za/retry.py:62-124`。**没有跨轮循环守卫**，模型重复同一调用会一路烧到 100 轮（真实风险）。
- 错误没有类别：策略拒绝 / 参数错误 / 超时 / 中止 / 工具异常都是一个字符串，UI 与压缩器都无法区分。

### 7.3 差距与建议

| 差距 | 建议 |
|---|---|
| 无错误类别 | **P1**：`ToolResultAgent.error_kind: Literal["policy","invalid_args","not_found","timeout","aborted","tool_error"]`（或文本前缀 `[policy] ...`）；模型看前缀、UI 上色、遥测聚合都靠它 |
| 无 `ToolError`/`render()` 等价 | **P2**：如果做扩展生态，引入 `class ToolError(Exception): render()`；当前 9 个工具不需要 |
| schema 子集不覆盖嵌套/枚举 | **P2**：MCP schema 是完整 JSON Schema，`validate_arguments` 现在会**跳过**未知关键字（安全但等于不校验嵌套）；若 MCP 参数错误率高，再补 `items`/`enum` |
| 无循环守卫 | **P1**：跨 turn 相同 `(name, canonical_args)` 计数 ≥5 注入一次纠正（复用 steering 通道），exempt `wait` 类；实现 <100 行，测试 3 条 |
| 无 `useless` | 归入 §1 |

---

## 对 ZenAgent v0.4 的最小内核改造清单

> 只列**内核/工具层**改动；不含 TUI。复杂度是单人估算（含测试）。依赖关系用缩进表示，必须先完成前置。

| # | 改动 | 依赖 | 复杂度 | 测试点 |
|---|---|---|---|---|
| 1 | **扩展契约**：`ToolContext`（`session_id/cwd/batch_id/index/total/add_context`）+ `execute(..., context=None)`；`before_tool_call` 收 `(call, tool, args)` | — | 0.5d | 旧签名工具不破；context 字段正确；钩子能读工具声明；`add_context` 在结果后注入 |
| 2 | **ToolResult 扩字段**：`useless`、`error_kind`（枚举/前缀）、（可选）`provider_metadata` | 1 | 0.5d | 会话 JSON 往返；压缩器能读 useless；错误类别覆盖 5 条路径 |
| 3 | **统一结果预算** `post_process_result`：阈值 + head/tail + 标准 notice；列宽 cap；所有工具/MCP/扩展自动套 | 2 | 1d | 阈值下不变；超阈值 head+tail+notice；image block 保留；MCP resource 不丢；工具内旧截断不再双重触发 |
| 4 | **artifact 落盘**：超阈值写会话 artifacts 目录，notice 带路径/行号；`read_file` 支持按行回读 | 3 | 1d | 文件可读回；notice 指向正确行；落盘失败降级为纯截断不报错 |
| 5 | **工具声明**：`approval(args)->{tier,reason,targets}`、`timeout(default/min/max)`、`concurrency`、`hidden/default_inactive` | 1 | 1d | 每个内置工具声明正确分级；hidden 工具不进 wire；timeout clamp |
| 6 | **审批 v2**：`resolve(tool,args,mode,policies,grants,restrictions)`；会话级目标缓存 `tier\0path`，目录授权只覆盖一层；plan/deny/受限目录优先级 | 5 | 1.5d | deny 赢 allow；plan 赢 allow；文件授权覆盖同文件、不覆盖兄弟；目录授权覆盖一层不覆盖父/子；headless fail-closed |
| 7 | **并发语义修正**：`exclusive` 真互斥（等前面所有 shared + 阻塞后面 shared）；结果顺序不变 | — | 0.5d | write(edit) exclusive 与 read parallel 交错；顺序与 id 对应 |
| 8 | **超时表**：`TOOL_TIMEOUTS` + `clamp_timeout` + 全局 `maxTimeout`；接入 bash（已有）与 fetch | 5 | 0.5d | default/min/max/全局；0 表示不限；bash 超时仍 killpg |
| 9 | **循环守卫**：跨 turn `(name, args)` 哈希计数 ≥5 → 注入一次纠正（steering 通道），exempt wait 类 | 2 | 0.5d | 重复触发一次；换参数重置；连续 5 次不误伤不同文件读取 |
| 10 | **请求级 max_tokens 拟合**：`max_tokens = max(1024, min(model.max_tokens, window − prompt_est − headroom))` | — | 0.5d | 接近窗口时被压到下限；正常情况不动；provider "到窗即停" 不适用时不压 |
| 11 | **run 工具汇总**：`agent_end` 附带 by-name/status/latency；`RunMetrics` 扩展 | 2 | 0.5d | 计数/状态/耗时聚合；失败不抛错 |
| 12 | **MCP 细节**：readOnlyHint→read tier；结果走预算；可重试错误重连一次 | 3,5,6 | 1d | 只读不询问；大结果被截；断线重连后成功 |

**P0（v0.4 必须）**：1、2、3。**P1**：4、5、6、7、8、9、10、11。**P2**：12、MCP 定义缓存、后台任务 detached 更新、受限目录。

### 明确不做（N）

| 项 | 理由 |
|---|---|
| xd:// 设备协议（read/write 当调度器） | 只为压 schema token；9 个工具时收益为负，且带来"工具不可见/参数双跳/审批双门"三类 bug（omp 自己踩过 #5764/#5973） |
| BM25 / lazy tool discovery | omp 18.14.0 已删除该设置且无替代（`omp/config/settings.ts:3243-3259`） |
| 推测执行 `speculation` | 默认关、只支持本地读、900 行 + 指纹校验；收益场景（长读链）ZenAgent 尚不存在 |
| 参数增量流 `openArgStream` | 纯 UI 预览收益 |
| live steering（响应内注入） | 依赖 provider 能力；turn 边界 steering 已覆盖 95% 场景 |
| JTD/typebox 级 schema 系统 | 手写子集 + 工具自防御已够；补 `items/enum` 即可 |
| 跨会话 SQLite 审批缓存 | 目标级会话缓存收益/成本比最好；跨会话持久化留到有真实诉求 |
| 工具内自动重试 | 副作用不可控；MCP 连接层重试除外 |
| PTY 交互、隐藏工具渲染、`providerMetadata` 透传 | 当前无消费者 |

### 一句话给 v0.4

**把"工具结果预算 + 审批目标键 + 契约 context/错误类别"三件事做成内核统一层**，其余（发现、推测、流式参数、设备协议）都不做；omp 在这三件事上的 90% 价值集中在 `output-meta.ts`、`approval.ts`、`types.ts` 三个文件里，而非它的 60 个工具。
