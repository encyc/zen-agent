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
- **内置 server 是第三层（最低优先级）**，见下节。
- 项目层随仓库提交：**不要放 token/密钥**。需要 header 凭据的 server 把整条定义放全局
  `~/.zenagent/mcp.json`（不提交），或用 stdio server 的环境变量读取；项目层只覆盖 command/url
  等可共享部分。
- CLI 过滤：`--mcp-server files`（可重复）、`--mcp-file PATH`、`--no-mcp`。

## 内置 server（Computer Use）

有些 server 的定义属于 **zen 版本**，不属于用户的 `mcp.json`：零凭据、不需要逐项目 trust、
不需要安装目录，而版本必须跟着 zen 一起走。这类 server 写在代码里
（`zenagent/mcp/builtin.py`），开关写在 `settings.json` 的 `prefs.builtin_mcp`，**永远不写 `mcp.json`**。

| | |
|---|---|
| id | `compute-use` |
| label | Computer Use |
| 上游 | `agent-desktop`（npm，Vercel Labs）经 `agent-desktop mcp` 提供 stdio MCP |
| 版本 | 由 `COMPUTE_USE_VERSION` 常量锁死；zen 发版前实测通过才升 |
| 运行依赖 | Node.js 18+（`npx -y agent-desktop@<pin> mcp`）与系统无障碍授权 |

合并顺序是 **内置 → 全局层 → 项目层**：任意一层出现同名 server 就覆盖内置那份，这是
「我想用自己的 agent-desktop 命令行」的逃生门。`--mcp-file` 只读那一个文件，**不叠加**内置层。

为什么不做成插件：插件（`zenagent/plugins/`）解决的是「要凭据 + 要附带 skill + 要装卸状态」的第三方
连接器，装完的 server 名会变成 `<plugin id>.<key>`，而且不出现在 MCP 设置页。Computer Use 三样都不需要。

### 只暴露一个工具

`agent-desktop` 在 MCP 侧有 **75 个工具、合计约 44 KB 的 inputSchema**，且**没有任何一个声明
`readOnlyHint`**。原样挂上去，每轮请求都要付这份 schema，而每个 click 仍然会要一次确认。
所以 zen 把它折叠成一个工具 `desktop`：

- `action=` 分发到上游工具（`desktop_<action>`），参数用上游原名（`ref`/`text`/`app`/`value`/`combo`…），
  只放行本工具声明过的键，模型塞不进未公开的 flag。
- 只读 action（`snapshot`/`find`/`get`/`is`/`screenshot`/`status`/`permissions`/`list_apps`/
  `list_windows`/`help`）在权限门上**按参数降级为 read**：plan 模式、`--read-only` 下照常可用，
  snapshot 不弹确认。其余 action 是 `write`，走正常审批。
- `action=help` 返回二进制内置的、与该版本严格一致的 agent 指南（按 server 版本缓存一次）。
- 需要在会话里用原始 75 个工具时：`/desktop raw on`（会话级，不写盘；代价是每轮多付 44 KB schema）。
  `off` 收回。

### 怎么开、怎么看

- 桌面端 **Settings → MCP → 内置**：一张 Computer Use 卡片（label、id、被锁的版本、运行依赖）。
  「安装并启用」= 写开关 + 就地重装配 + 用 `probe_mcp_server` 拉一次 `tools/list`（顺带把 32 MB 的
  npx 首装跑完）。连不上就回滚开关并显示上游给的原因，不留下「开关说有、实际没连」的假状态。
- CLI：`zen mcp enable compute-use` / `zen mcp disable compute-use`；`zen mcp` 的列表里带一行
  `builtin: compute-use (Computer Use 1.3.1) — off`。CLI 只写开关，**下次会话生效**（headless 没有
  runtime 可重装配）。
- 斜杠命令：`/desktop`（状态）、`/desktop on|off`、`/desktop raw on|off`；`/mcp` 的输出里带一行内置状态。

### 权限与边界

真正的边界是 **操作系统的无障碍授权**（macOS：系统设置 → 隐私与安全性 → 无障碍，授予**启动 zen 的
那个 app**），不是 zen 的审批。zen 侧只做两件事：`write` 档工具的常规审批，以及 `--read-only`/plan
模式下拒绝动作类 action。

**启用即意味着无人值守任务也会用它**：automation 跑在 `auto` 模式，不弹确认。这一点在设置页的按钮
下方就写着，而不只是文档里。

敏感数据：snapshot、剪贴板、截图都可能带出桌面私密内容，别写进公开日志或上传。

### 发版时升版本

1. `npx -y agent-desktop@<new> mcp` 手工握手，确认工具数与 `desktop_*` 名字没变。
2. 真机冒烟代理映射（Finder：snapshot → click → wait）。
3. 改 `zenagent/mcp/builtin.py` 的 `COMPUTE_USE_VERSION` 一个常量，CHANGELOG 加一行。
4. `bash scripts/check.sh` → `bash scripts/release-check.sh`。

## 工具怎么出现

- 每个 MCP 工具暴露为 `mcp__<server>__<tool>`，例如 `mcp__files__read_text_file`。
- 参数 schema 直接用 MCP 的 `inputSchema`。
- 描述带 `[server]` 前缀，便于模型区分来源。
- MCP 工具默认按**写/执行**处理：需要 `--yes`、allowlist 或交互确认，`--read-only` 下拒绝。
  例外：服务器在工具 `annotations.readOnlyHint` 里声明只读时，按 `read` 档处理
  （plan / `--read-only` 放行、免确认）。`readOnlyHint` 是**服务器自述**，不是本地保证。
- 与内置工具重名不会冲突（命名空间隔离），内置优先。
- **声明了 `proxy_tool` 的内置 server 会被折叠**：连上之后只有那一个代理工具进工具列表，
  上游的 `mcp__<server>__*` 全部不暴露（`/desktop raw on` 例外）。

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
