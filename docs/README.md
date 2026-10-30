# ZenAgent 文档

这套文档按「**用起来** → **看懂它** → **查细节**」三层组织。推荐顺着读；每篇末尾都有「源码对照」，列出该篇涉及的
模块、关键符号和对应测试，方便你一边读文档一边翻代码。

## 建议阅读路线

1. [架构总览](architecture.md) — 五层依赖、一次请求的完整数据流、为什么这样分层
2. 使用指南（先知道它怎么工作）：
   - [CLI](guide/cli.md) · [配置与目录](guide/config.md) · [工具与权限](guide/tools-permissions.md)
   - [MCP](guide/mcp.md) · [Skills 与模板](guide/skills-templates.md) · [扩展](guide/extensions.md)
   - [会话/导出/用量](guide/sessions.md) · [RPC 与 Python SDK](guide/rpc-sdk.md)
3. 内核导读（重点，按执行顺序）：
   - [类型与事件](internals/types-events.md) → [Provider 层](internals/providers.md)
   - [Agent Loop](internals/agent-loop.md) → [Agent 类](internals/agent-class.md)
   - [工具内核](internals/tools-internals.md) → [上下文与压缩](internals/context-compaction.md)
   - [护栏与校验](internals/guardrails.md) → [持久化/重试/崩溃](internals/persistence.md)
   - [MCP 内核](internals/mcp-internals.md) · [扩展内核](internals/extensions-internals.md)
4. 速查：[核心契约](reference/contracts.md) · [事件全表](reference/events.md) · [文件地图](reference/file-map.md)
5. [功能地图](feature-map.md)（功能 → 源码 → 测试）· [测试策略](testing.md)

## 文档是怎么组织的

- **guide/**：面向使用者，解释「怎么用 + 边界行为」，不要求读源码。
- **internals/**：面向学习，解释「为什么这么写 + 不变量 + 失败模式」，直接引用源码符号。
- **reference/**：表格化速查，不做解释。
- **feature-map.md**：任何功能都能从这里跳到实现和测试。

## 一句话记住这套设计

> 内核只有一个契约：`StreamFn` **永不抛异常**，所有失败都变成事件；取消是**协议内的正常状态**而不是
> `asyncio.CancelledError`；工具**永远返回结果**，所以 loop 里没有任何 `try/except` 包裹工具执行。
> 其余所有能力（MCP、skills、模板、扩展、RPC）都是在这套契约之上「接线」，不改变内核。

## 文档与代码的同步约定

- 代码里的行号会漂移，文档只写**符号名**（如 `AgentTool.execute`、`stream_with_retry`），不贴固定行号。
- 每篇末尾的「源码对照」给出文件路径 + 符号名，可直接 `grep -n "class Agent" zenagent/agent/agent.py`。
- 行为以测试为准；文档与测试冲突时先看测试（`scripts/check.sh` 是唯一闸门）。
