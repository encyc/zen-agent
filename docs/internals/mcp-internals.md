# MCP 内核：手写 JSON-RPC 与迟到重连

## 三层结构

```
config.py     mcp.json 解析 → McpServerConfig → make_transport
client.py     Transport 协议 → StdioTransport / HttpTransport → McpClient（initialize/tools/list/tools/call）
manager.py    McpManager：多 server 状态、重试、工具热替换、AgentTool 包装
```

## JSON-RPC

- 每行一个 JSON 对象（stdio）；HTTP 是每请求一个 POST。
- 请求 `{"jsonrpc":"2.0","id":N,"method":...,"params":...}`。
- 响应按 id 关联 pending future；`error` → `McpError(code, message)`。
- 通知（无 id）如 `notifications/initialized` 直接发送，不等待。
- 超时 → `McpTimeout`（`McpError` 子类）；进程崩溃/EOF → `McpError("server exited ...")`。

## StdioTransport

- `subprocess` 启动 command，`stdin/stdout` 管道，`stderr` 单独读取保留最后一行用于错误信息。
- 读循环 task：逐行读 stdout → 分发 future / 记录 error。
- `close()`：关闭 stdin → 等待退出 → 超时 kill；幂等。
- 进程退出时所有 pending future 立刻得到错误，不会挂死。

## HttpTransport

- `POST url`，`Accept: application/json, text/event-stream`。
- 响应可能是普通 JSON，也可能是 SSE（逐行 `data:`）；两种都解析。
- 记住 `mcp-session-id` 响应头，后续请求回显；同时带 `mcp-protocol-version`。
- 可注入 `httpx.AsyncClient`（测试用 `MockTransport`），不注入则自建并负责关闭。

## McpClient

```python
client = McpClient("files", transport, timeout_s=30)
await client.start()               # initialize + notifications/initialized
tools = await client.list_tools()
result = await client.call_tool("read_file", {"path": ...})
await client.close()
```

`server_info`、`protocol_version` 暴露给诊断；`close()` 幂等，关闭后请求报明确错误。

## McpManager：为什么失败不是终点

状态：`connected`（有 client）或 `failed`（有 error，client 为空）。

- `start()`：并发连接所有 server；失败只返回 warning，**server 仍留在册**。
- `retry_failed(force=False)`：只重试未连接的；默认间隔 10s（避免每个 prompt 都等连接超时）；
  `force=True` 立即重试。
- 连接成功后：`list_tools()` 重新发现，`tool_defs()` 生成 `mcp__server__tool` 包装。
- REPL 在每轮新 prompt 前调用 `refresh_mcp()`：重试 + 对比工具集合 + 更新 `agent.tools`
  + 重建 system prompt（否则模型看到的工具清单是旧的）。
- `/mcp` 查看状态，`/mcp retry` 强制重连。

真机复现过「启动失败 → 后来就绪 → 同一会话可用」，见 [MCP 指南](../guide/mcp.md)。

## 工具包装

`McpManager._make_tool` 生成 `AgentTool`：

- name：`mcp__<server>__<tool>`（非字母数字下划线转 `_`）；
- description：`[server] <原描述>`；
- parameters：MCP 的 `inputSchema`（由 `validate_arguments` 校验）；
- approval：`tools/list` 的 `annotations.readOnlyHint: true` → `"read"`，否则保守 `"write"`
  （权限门据此决定 plan/`--read-only` 是否放行、是否需要确认；这是服务器自述）；
- execute：调用 `client.call_tool`，把 content blocks 映射成 `TextContent`/`ImageContent`；
  `isError` → `is_error=True`；`McpError` → `is_error=True` 的结果（不抛给 loop）。

## 限制

- 不实现 OAuth、resources、prompts、sampling、notifications 的业务处理。
- 单次调用无 abort（只有 timeout）。
- 不做 server 自动安装/升级。

## 源码对照

- `zenagent/mcp/client.py`（`McpClient`、`StdioTransport`、`HttpTransport`、`McpError`）
- `zenagent/mcp/config.py`（`load_mcp_config`、`make_transport`）
- `zenagent/mcp/manager.py`（`McpManager`、`tool_name_for`）
- 测试：`tests/test_mcp_client.py`、`tests/test_mcp_manager.py`、`tests/fixtures/mcp_echo_server.py`、`tests/fixtures/mcp_flaky_server.py`
