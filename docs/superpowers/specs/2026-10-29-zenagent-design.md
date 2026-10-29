# zenagent 设计文档

日期：2026-10-29
状态：待用户批准

## 意图与动机

用纯 Python 从零构建一个最小的 coding agent，同时把可复用的 agent 内核做成独立可依赖的库。对标 pi 生态的 `pi-ai`（统一 LLM API）+ `pi-agent-core`（agent loop 运行时）两层，`pi-coding-agent` 对应的部分（交互式 CLI）是第一个消费者。

两个动机：

1. **学习**——作者想真正理解 agent 的运作原理，尤其是 provider 层最脏的部分（流式增量拼接、thinking 方言、各家 compat 差异），所以要亲手写，不用 SDK 隔着毛玻璃看。
2. **复用**——作者的很多项目在用 pi-agent-core（TypeScript），近期有项目必须纯 Python，而 Python 生态没有对位的最小内核（LangChain 太重、PydanticAI 是框架思路、claude-agent-sdk 绑定 Anthropic）。zenagent 要成为那个「不存在的合适项目」。

## 目标

- 交互式终端 coding agent：流式输出、思维链显示、工具调用可视化、Ctrl+C 中断、斜杠命令、会话恢复。
- 留 headless 入口：`zenagent -p "..."` 单次执行，输出结果，退出码表成败，可嵌入脚本。
- 内核（types + providers + agent loop）零 CLI 依赖，可作为库被其他 Python 项目 import。
- 覆盖三个 provider：GLM token plan（zai-coding-cn）、DeepSeek、opencode-go。两个协议适配器（openai-completions、anthropic-messages）覆盖其绝大部分模型。
- 全链路 async（asyncio），headless 提供同步便捷封装。

## 非目标（v1）

- MCP、subagent、插件系统、远程会话。
- google-generative-ai / openai-responses 适配器（架构上随时可加，不预留代码）。
- 自动生成的模型目录（手写 YAML 小目录即可）。
- 沙箱/权限系统内建于内核（pi 哲学：确认门是 harness 层的 `before_tool_call` 钩子的事）。
- TUI 全屏界面（textual 那种）。v1 是行式终端 UI，rich 渲染。

## 反目标（做了就算失败）

- 内核依赖任何 LLM 厂商 SDK 或 agent 框架。
- provider 的原生概念（finish_reason 字符串、SDK 事件类型、方言字段名）泄漏到 loop 或 UI 层。
- loop 会因为工具抛异常、模型输出烂 JSON、网络错误而崩溃——一切失败必须变成协议内的事件或 isError 结果。
- 为了通用性引入抽象层（基类继承树、插件注册机制、依赖注入容器）。

## 约束

- Python 3.15+，uv 管理项目与依赖。
- 内核依赖仅 `httpx`。CLI 层加 `prompt_toolkit` + `rich`。开发依赖 pytest。
- 单仓库单包 `zenagent`，目录内部按层分模块；未来值得时再拆成 core / coding-agent 两个发行包。
- macOS arm64 为主力平台（Linux 可用即可，Windows 不保证）。

## 已考虑的路线（Approaches）

**路线 A（选定）：全手写，最小依赖。** 数据模型 dataclass 手写，SSE 手写解析，适配器直接对着 wire format 写，工具 schema 用原生 JSON Schema dict。脏活就是课程内容；provider 面小（2 适配器、2 方言），工作量可控；无 SDK 依赖，被其他项目复用时干净。

**路线 B（否决）：官方 SDK 做传输，自己做归一化。** pi-ai 实际采用此路线（底层用 openai/@anthropic-ai/sdk）。省传输层脏活，但 SDK 抽象渗入归一化层，zaiToolStream 这类私货行为隔着 SDK 更难调，学习价值打折。pi 需要 SDK 是因为要伺候 40 个 provider，我们只有 2 个适配器。

**路线 C（否决）：基于 PydanticAI 等现有框架。** 与根本动机冲突——本项目要成为的那个库不存在，所以才建它。

作者已确认选择 A，理由如上。

## 架构

### 分层

```
┌─────────────────────────────────────────┐
│ cli/      repl + headless + rich 渲染    │  ← harness（可换）
├─────────────────────────────────────────┤
│ agent/    loop + state + session        │  ← 对标 pi-agent-core
├─────────────────────────────────────────┤
│ providers/ 适配器 + provider 配置 + 目录  │  ← 对标 pi-ai
├─────────────────────────────────────────┤
│ types.py / events.py  统一数据模型        │  ← 两层共用
└─────────────────────────────────────────┘
```

依赖方向严格向下。`agent` 不知道 `cli` 存在，`providers` 不知道 `agent` 存在。

### 数据模型（types.py，抄 pi-ai 的骨架）

全部 dataclass，手写 `to_json`/`from_json`（不引 pydantic）：

- `Message = SystemMessage | UserMessage | AssistantMessage | ToolResultMessage`
- `AssistantMessage.content: list[TextContent | ThinkingContent | ToolCall]`——内容块数组是整个系统的中枢
  - `ThinkingContent(thinking, signature, redacted)`
  - `ToolCall(id, name, arguments: dict, arguments_error: str | None)`——模型参数不是合法 JSON 时降级为空参数 + 错误描述，不炸 loop
- `Usage(input, output, cache_read, cache_write, reasoning, cost)`——cost 由模型目录单价计算。注意：GLM token plan 是包月计费，目录单价填 0，`cost` 对包月模型退化为 0，统计意义落在 token 用量上；按量计费模型（DeepSeek、opencode-go 部分模型）目录填真实单价。
- `StopReason = pending | stop | length | tool_use | error | aborted`
- `Tool(name, description, parameters: dict)`——parameters 是原生 JSON Schema
- `Model(id, api, provider, base_url, context_window, max_tokens, reasoning, cost, compat)`——目录条目
- 中途 system 消息机制 v1 **不做**（pi 允许 system 消息在 transcript 中途出现并携带 prompt 段落/工具增删；v1 只有开头一条 system 消息，简化）

### 流式事件协议（events.py，最值得抄的设计）

适配器对外只吐 `AssistantEvent`：

```
start → (text|thinking|toolcall)_(start|delta|end)* → done | error
```

每个事件携带 `partial: AssistantMessage`（拼到一半的完整快照）。增量拼接的脏活完全归属适配器，只做一次；loop 和 UI 永远拿现成快照。

`agent_loop` 对外吐 `AgentEvent`（UI 事件）：`agent_start/agent_end`、`turn_start/turn_end`、`message_start/message_update/message_end`（包裹 AssistantEvent）、`tool_execution_start/update/end`。两层事件用同一个 AsyncIterable 风格的 EventStream 抽象（async generator 优先，v1 不做 backpressure）。

### Provider 层（providers/）

- `openai_completions.py`（~600 行）：`stream(model, context, options) -> EventStream`。SSE 手写解析（httpx `aiter_lines`）。实现 thinking 方言 `zai`（GLM：请求 `thinking: {type: enabled}` 风格字段 + `zaiToolStream` 工具流式增量格式）与 `deepseek`（响应 `reasoning_content` 字段 + 多轮对话需把 reasoning_content 塞回 assistant 消息）。兼容旗标：`max_tokens_field`、`supports_developer_role`、`requires_reasoning_content_on_assistant`、`zai_tool_stream` 等，从 base_url 嗅探默认值、按模型覆盖。
- `anthropic_messages.py`（~500 行）：thinking block（含 signature 往返、redacted）、tool_use 内容块、cache_control v1 不做。
- `compat.py`：`CompatFlags` dataclass + base_url 嗅探。**嗅探只是未知端点的兜底默认值；目录条目一律显式携带 compat**——因为 opencode-go 这类网关会归一化上游方言（网关上的 GLM 是不带 zai 方言的普通 openai-completions），按域名嗅探会把 zai 旗标错误套到网关模型上。
- `catalog.py`：手写 YAML 模型目录（zai-coding-cn / deepseek / opencode-go 三 provider 的常用模型，含单价与窗口）；provider = 配置（id、base_url、env var 名、默认适配器、模型列表），不是代码。v1 只发行包内置目录；`config.toml` 选择默认模型、可按 provider 名覆盖 base_url / api key 环境变量名；用户自定义任意 provider 是非目标。
- 传输层公用：超时、指数退避重试（仅幂等错误：429/5xx/连接错误；stream 开始后不重试）、错误归类为协议内 `error` 事件。StreamFn 契约：**永不抛异常**，一切失败走事件流。
- **中断契约**：`options.signal`（基于 asyncio 的 abort token）随每次请求传入；适配器在 SSE chunk 之间检查，触发时发出 `stop_reason=aborted` 的终局事件并负责关闭 httpx 响应；loop 一侧 try/finally 兜底。取消不是异常，是协议内状态。

**从 pi-ai 1.4.0 源码核实过的 wire 事实**（实现时以 M1 实捕 SSE 为准，本节是起点不是终点）：

- GLM（zai 方言）请求：`thinking: {type: "enabled", clear_thinking: false}`（关闭时 `{type: "disabled"}`）；glm-5.3 支持 `reasoning_effort`；带工具时请求 `tool_stream: true`（zaiToolStream 旗标——请求侧字段，让服务端增量流出工具参数）。
- DeepSeek 请求：`thinking: {type: "enabled" | "disabled"}` + 可选 `reasoning_effort`。
- 响应思维链字段：依次试 `reasoning_content`（DeepSeek/llama.cpp 系）→ `reasoning` → `reasoning_text`，取第一个非空，防重复；DeepSeek 多轮对话需把 `reasoning_content` 塞回 assistant 消息。
- 工具调用增量组装：按 chunk 里的 `index` 追踪块，无 index 时回退按 `id`；参数 JSON 增量拼接，非合法 JSON 终局降级为 `arguments_error`。
- opencode-go：base URL `https://opencode.ai/inference/go`（anthropic-messages）与 `/go/v1`（openai-completions 等）；每次会话必须带 `x-opencode-session: <id>` 头；Claude 系模型走 anthropic-messages 协议。
- Anthropic 适配器的实捕参照端点：opencode-go 的 Claude 系模型（用户持有 key）；备选按公开协议文档合成 fixture。

### Agent loop（agent/loop.py，抄 pi-agent-core 的骨架）

`async def agent_loop(prompt, state, config, signal) -> AsyncIterator[AgentEvent]`，**核心是函数**（纯、可离线测试）。有状态封装另做 `Agent` 类（`agent/agent.py`：持有 transcript、`subscribe` 订阅事件、steering 队列、`abort`、`wait_for_idle`、`reset`），对标 pi-agent-core 的 `agentLoop()` 与 `Agent` 并存两层；CLI 消费 `Agent`，不直接驱动 loop。核心循环：

```
append prompt →
loop:
  （无请求级钩子；model/thinking 从 config 读取）
  stream = stream_fn(model, to_llm_messages(state), options)
  async for event in stream: 转发为 message_* 事件，累积 AssistantMessage
  append assistant 消息
  if stop_reason != tool_use: break
  并发执行工具（asyncio.gather，遵守每工具 sequential 声明），
    每个调用过 before_tool_call（权限门）→ execute → after_tool_call（结果改写）
  append tool 结果消息（按 assistant 消息中的源顺序）
  get_steering_messages 钩子注入中途用户输入
```

v1 保留的钩子（都是 config 上的可选 callable）：`before_tool_call`、`after_tool_call`、`get_steering_messages`、`transform_context`（上下文管理的挂点，v1 只装一个朴素策略：超窗时丢最老的成对消息 + 警告）。`prepare_request` 不做：换 model / thinking 档位由 `Agent` 在每次 run 开始时读取实例属性实现（`/model`、`/thinking` 命令改属性、下一轮生效），不需要请求级钩子。pi 的 `finish_turn/prepare_next_turn/get_follow_up_messages/convertToLlm/自定义 AgentMessage` v1 不做——transcript 里只有标准四类消息，扩展点等真实需求出现再开。

工具执行契约：`AgentTool.execute(tool_call_id, params, signal, on_update) -> ToolResult(content, details, is_error, terminate)`。抛异常 = 捕获转 is_error 结果。未知工具、参数校验失败同理。abort 通过 `signal` 传入每个 execute，工具自行检查。

### 会话持久化（agent/session.py）

JSONL，每行一条消息（含时间戳、model、usage），存 `~/.zenagent/sessions/<date>-<slug>.jsonl`。`--resume` 重放最近会话。格式就是数据模型的 JSON 序列化，无额外 schema。

### 工具集（tools/，coding agent 第一消费者）

v1 七个：`read_file`、`write_file`、`edit_file`（字符串精确替换，唯一匹配校验）、`list_dir`、`glob`、`grep`（调 ripgrep，无则退纯 Python）、`bash`（超时、输出截断、cwd 固定项目根）。工具用 dataclass + `parameters` JSON Schema dict 声明；schema 从类型注解自动生成是后续增强，不是 v1。

权限门（CLI 层的 `before_tool_call`）：写/执行类工具首次每种询问一次（y/n/a=本次会话全允许），读类直接放行；`--yes` 跳过全部。内核无权限概念。

### CLI（cli/）

- `zenagent`（无参）：进入 REPL。prompt_toolkit 读输入（历史、多行），rich Live 区流式渲染 thinking（暗色折叠）/text/toolcall 进度；工具执行显示为 spinner + 摘要行。Esc 或 Ctrl+C：首次 = 中断当前流，连按 = 丢弃。斜杠命令：`/model`、`/clear`、`/compact`（手动触发上下文压缩）、`/exit`、`/help`。
- `/compact` 语义：用当前模型对既有 transcript 做一次独立的一次性总结调用，然后把会话截断为 `[system, user("# 此前会话摘要\n...")]`，**写入新会话文件**（旧 JSONL 不可变、保留追查）；`transform_context` 的朴素超窗丢弃策略只作为自动兜底，两者独立。
- `zenagent -p "..."`：headless 单次执行，plain text 输出（无 ANSI），退出码 0/1，`--json` 输出结构化结果。
- 模型选择：`--model glm-5.3`，默认取配置文件 `~/.zenagent/config.toml` 第一项。API key 走环境变量（`ZAI_CODING_CN_API_KEY`、`DEEPSEEK_API_KEY`、`OPENCODE_API_KEY`）。

### 测试策略

适配器是重点：录制真实 SSE 流存为文本 fixture，离线重放断言事件序列（GLM thinking、GLM 工具流、DeepSeek reasoning_content、Anthropic thinking block 各至少一套：纯文本、单工具、并行工具、烂 JSON 参数、中断、错误）。loop 层用 fake stream_fn 驱动全路径。工具层临时目录集成测试。CLI 冒烟测试一次。

## 交给实现者的决定

- 各模块内函数拆分、私有 helper 命名。
- EventStream 的具体形态（AsyncGenerator 包装类 vs 裸 async generator + helper）。
- rich 渲染细节（颜色、折叠交互）。
- 重试参数默认值（次数、退避上限）。
- 目录 YAML 的确切字段布局。
- 测试 fixture 的组织方式。

## 里程碑

1. **M1 内核可见**：types + openai_completions（GLM 直连）+ 最小 loop + headless `-p`，终端里能看到流式 thinking/text。验收：对真实 GLM API 跑通「问一句 → 流式答完」。
2. **M2 工具闭环**：工具协议 + 读写/bash 三件套 + 权限门，`-p "在 /tmp 建个文件写 hello"` 端到端成功。
3. **M3 交互式**：REPL、流式渲染、中断、steering（工作中插话）、会话持久化与 resume、补齐七个工具与 anthropic 适配器、DeepSeek 方言。
4. **M4 打磨**：上下文压缩策略、grep/glob、`--json`、录制 fixture 全绿、README。

每个里程碑结束停下来真实使用，把摩擦记下来再进下一个。

## 附录：builder 审查后的裁定

设计文档完成后经独立 builder 审查，六项质疑，裁定如下（前四项已并入上文相应章节）：

1. **GLM wire 格式的依据**——已答：从 pi-ai 1.4.0 源码核实（见「wire 事实」小节），M1 第一步就是对真实 GLM 端点实捕 SSE 存 fixture，实捕优先于源码推定。
2. **opencode-go 是什么**——已答：多协议网关（`opencode.ai/inference/go`），需 `x-opencode-session` 头；上游方言被网关归一化，故目录条目显式携带 compat，嗅探仅作未知端点兜底。
3. **Anthropic 协议参照端点**——已定（我的裁定）：opencode-go 的 Claude 系模型；无 live 端点的场景按公开协议文档合成 fixture。
4. **`/compact` 语义**——已定（我的裁定）：LLM 总结 + 截断，产出新会话文件，旧文件不可变；与自动兜底策略相互独立。
5. **provider 是否用户可扩展**——已定（我的裁定）：v1 仅包内置目录，config.toml 可覆盖 base_url / key 环境变量名；自定义 provider 非目标。
6. **中断与 StreamFn 契约的 reconciliation**——已定（我的裁定）：abort token 放在 options.signal，适配器 chunk 间检查并终局 aborted 事件、负责关闭响应；取消是协议内状态，不是异常。
