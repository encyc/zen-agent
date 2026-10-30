# MCP（Model Context Protocol）

ZenAgent 手写实现 MCP 客户端（不依赖官方 SDK），支持 **stdio** 与 **Streamable HTTP** 两种传输。

## 配置

两层 JSON，按 server 名合并：

1. `~/.zenagent/mcp.json`（全局层，机器本机，不进 git）
2. `<项目根>/.zenagent/mcp.json`（项目层，可提交；项目根 = 最近的含 `.git` 的目录）

同名 server 由**项目层整条覆盖**，不同名的全局 server 保留；两层都不存在就是没有 server。
显式 `--mcp-file PATH` 只读该文件，不叠加项目层。

```json
{
  "servers": {
    "files": {
      "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "env": {"DEBUG": "0"}
    },
    "remote": {
      "url": "https://example.com/mcp",
      "headers": {"authorization": "Bearer token"}
    }
  }
}
```

- `command` 与 `url` 二选一，类型必须正确，否则启动即报错（错误信息带文件路径）。
- 项目层随仓库提交：**不要放 token/密钥**。需要 header 凭据的 server 把整条定义放全局
  `~/.zenagent/mcp.json`（不提交），或用 stdio server 的环境变量读取；项目层只覆盖 command/url
  等可共享部分。
- CLI 过滤：`--mcp-server files`（可重复）、`--mcp-file PATH`、`--no-mcp`。

## 工具怎么出现

- 每个 MCP 工具暴露为 `mcp__<server>__<tool>`，例如 `mcp__files__read_text_file`。
- 参数 schema 直接用 MCP 的 `inputSchema`。
- 描述带 `[server]` 前缀，便于模型区分来源。
- MCP 工具**不是只读工具**，默认要 `--yes` 或 allowlist；`--read-only` 下全拒。
- 与内置工具重名不会冲突（命名空间隔离），内置优先。

## 启动失败不是终点（重点）

很多工具（包括我们遇到过的实现）在 session 启动时把工具列表快照下来，服务器没起来就永久不可用。
ZenAgent 不这样：

- 启动失败的 server 保留在 manager 里，状态为 `failed`，错误原因可见；
- **每个新 prompt 前自动重试**（默认间隔 10s，避免每次都等连接超时）；
- `/mcp retry` 立即强制重连；
- 连上后重新拉 `tools/list`，把新工具**热替换**进 `agent.tools`，并重建 system prompt；
- `/mcp` 查看每个 server 的状态与工具数。

真实例子（server 先以退出码 2 失败，后来就绪）：

```
start warnings: ['files: McpError: server exited (code 2): not ready']
status: [('files', 'failed', 0)]        tools before: []
retry warnings: []
status: [('files', 'connected', 14)]    tools after: mcp__files__read_file, ...
```

## 超时与错误

- 连接和调用超时默认 30s（`McpClient(timeout_s=...)`）。
- JSON-RPC error、进程崩溃、管道 EOF 都会变成 `McpError`，最终呈现为 `is_error=True` 的工具结果。
- 服务端上一行 stderr 会带在错误信息里，方便排障。
- `/reload` 会重新发现 skills/templates/extensions，但 MCP 连接保持不变。

## 当前限制（有意不做）

- 不支持 OAuth、resources、prompts、sampling。
- 单个 MCP 调用**不支持中途 abort**（只有超时兜底）；Ctrl+C 会等这次调用结束。
- 不自动安装/启动 server（command 自己负责）。

## 源码对照

- 传输与协议：`zenagent/mcp/client.py`（`StdioTransport`、`HttpTransport`、`McpClient`）
- 配置：`zenagent/mcp/config.py`（`load_mcp_config`、`make_transport`）、
  `zenagent/project.py::find_project_root`/`project_mcp_path`
- 生命周期/重试/热替换：`zenagent/mcp/manager.py`（`McpManager.retry_failed`、`refresh_tools`、`tool_defs`）
- CLI 接线：`zenagent/cli/main.py`（headless）、`zenagent/cli/repl.py`（`refresh_mcp`、`/mcp`）
- 对应测试：`tests/test_mcp_client.py`、`tests/test_mcp_manager.py`、`tests/fixtures/mcp_echo_server.py`
