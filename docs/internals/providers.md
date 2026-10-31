# Provider 层：一个契约，四种方言

## 契约：`StreamFn`

```python
async def stream(model, messages, tools, options) -> AsyncIterator[AssistantEvent]
```

- **永不抛异常**：任何失败都变成 `error` 事件（由 `safe_stream` 兜底）。
- 只依赖 `httpx`，没有 vendor SDK。
- 入参 `messages` 已经是「system + 历史」的完整列表；`transform_context` 的改写发生在 loop 里，
  adapter 看不到 state。

## 注册与安全包装（`providers/registry.py`）

| 符号 | 作用 |
|---|---|
| `ADAPTERS` | `api` 名 → `stream` 函数 |
| `stream_fn_for(model)` | 按 `model.api` 取适配器，未知 api 抛 `ValueError` |
| `safe_stream(stream_fn, …)` | 捕获所有 `Exception`，转成终局 `error` 事件；`CancelledError` 继续传播 |

`stream_with_retry`（`providers/retry.py`）再包一层：只对「可重试的终局错误」重试。
`retries=0`（默认）时事件**直接透传**，保持流式；开启重试（`retries>0`）后，
每次尝试的事件先缓冲、失败则丢弃，避免 UI 收到重复的半截消息——代价是这一轮文本
在调用结束才出现。

## 目录与兼容开关（`providers/catalog.py` + `catalog.yaml`）

`Model`：id/provider/api/base_url/context_window/max_tokens/reasoning/cost/compat，
加上 `default`、`supports_vision`。

`CompatFlags`：不同厂商/网关的行为差异，例如：

- `thinking_format`：`deepseek` / `anthropic` / `none` —— reasoning 内容怎么回传；
- `supports_reasoning_effort`：是否接受 `reasoning_effort`；
- `requires_reasoning_content_on_assistant`：历史消息是否必须带回 reasoning 字段；
- `anthropic_adaptive_thinking`：Anthropic 新网关用 `thinking:{type:adaptive}` 而不是 `budget_tokens`。

`thinking` 用户级取值统一为 `off/low/medium/high/max`（`providers/compat.py::normalize_thinking`），
`off` 在 loop 里被转成 `None`，线上永远收不到字符串 `"off"`。

## 输出 token 拟合（`providers/limits.py`）

Chat Completions 系 request 里 `prompt + max_tokens` 超过上下文窗口会直接 400。三个适配器
在构造请求前都过 `fit_stream_options()`：请求的 cap 放得下就保持原值（包括"不显式传"），
放不下就压到 `window − prompt_est − 64`（低于 1024 时按剩余空间取，至少 1），
避免长 prompt 在第一轮就失败。`prompt_est` 包含消息 + **工具 schema**（9 个内置/MCP 工具
可以是数千 token）；图片按固定 1024 token 估算而不是 base64 字符数。

## 传输层（`providers/transport.py`）

- `post_stream()`：发请求，按状态码重试（429/5xx），等待带 abort；`Retry-After` 尊重。
- `_sleep(delay, abort)`：可中断的 sleep（`asyncio.wait` 组合）。
- `iter_sse_data(lines)`：SSE 解析（多行 data、注释、CRLF、`[DONE]`、流末丢弃未终止块）。
- `parse_usage()`：各厂商 usage 字段归一；OpenAI 的 `prompt_tokens` 含 cache，需减去
  `prompt_tokens_details.cached_tokens`；缓存价缺失时回退输入价。
- 错误响应体截断到合理长度；不把 key 写进错误。

## 四种方言速查

| api | 请求关键点 | 流事件 | 工具调用 |
|---|---|---|---|
| `openai-completions` | `/chat/completions`，`messages`，`tools` | `choices[].delta` | `tool_calls[]` 增量（index 配对） |
| `anthropic-messages` | `/v1/messages`，`system` 独立，`thinking` block | `content_block_*` | `tool_use` block 完成时成型 |
| `openai-responses` | `/responses`，`instructions` + `input` items | `response.output_text.delta` 等 | `function_call` item + arguments delta |
| `google-generative-ai` | `:streamGenerateContent?alt=sse`，`contents`/`systemInstruction` | `candidates[].content.parts` | `functionCall` 一次成型 |

每个适配器都有 `build_params`/`build_request` 纯函数（可单测）+ `_events_from_chunks`（可回放 fixture）。

## 用量与成本

- `Usage(input, output, cache_read, cache_write, cost)`。
- cost 由 catalog 单价计算；未知单价记 0 并在 UI 显示 token 数。
- 会话持久化了 usage，`zen usage` 才能跨会话统计。

## 新增一个 provider 的步骤

1. `providers/<name>.py`：实现 `build_params`（纯函数）+ `_events_from_chunks` + `stream`。
2. `registry.py` 注册 api 名。
3. `catalog.yaml` 加模型，必要时加 `compat` 开关。
4. `tests/fixtures/` 放一份真实 SSE 回放，写测试断言请求体与事件序列。
5. 用 `--trace` 跑真实请求对照。

## 源码对照

- 契约与注册：`zenagent/providers/registry.py`
- 传输/SSE/用量：`zenagent/providers/transport.py`
- 目录与兼容：`zenagent/providers/catalog.py`、`zenagent/providers/compat.py`、`zenagent/catalog.yaml`
- 四个适配器：`openai_completions.py`、`anthropic_messages.py`、`openai_responses.py`、`google_generative_ai.py`
- 重试：`zenagent/retry.py`
- 测试：`tests/test_registry.py`、`test_transport.py`、`test_openai_stream.py`、`test_openai_request.py`、`test_anthropic.py`、`test_openai_responses.py`、`test_google.py`、`test_retry.py`
