# RPC 与 Python SDK

## RPC 模式（编辑器/桌面壳嵌入）

```bash
zen rpc [--model ID] [--yes] [--mode plan|ask|zen] [--read-only]
        [--thinking LEVEL] [--max-turns N] [--max-cost X] [--retries N]
        [--resume [SESSION]] [--no-mcp] [--mcp-file PATH] [--mcp-server NAME]
        [--no-skills] [--no-context-files] [--trace PATH] [--metrics PATH]
        [--cwd PATH] [--allow-dangerous] [--allow-outside PATH]
        [--allow-destructive] [--allow-protected]
```

协议：**一行一个 JSON**；stdout 只写协议，日志走 stderr。服务器在 `Runtime` 上启动，
所以会话持久化、MCP、skills、扩展、工具集和审批与 TUI/REPL 是同一套实现。

### 请求

| type | 字段 | 作用 |
|---|---|---|
| `prompt` | `text` | 跑一轮；运行中再发返回 `busy` |
| `cancel` | — | abort 当前 run |
| `status` | — | running / messages / model / session / mode / tools / usage / busy / follow_ups / steering / cost |
| `shutdown` | — | 等运行中的 run abort 后退出 |
| `steer` | `text` | 插话；空闲时进入 follow-up 排队 |
| `tools` | — | 模型可见工具 `[{name, description, approval}]` |
| `model` | `model_id` | 切模型（key / thinking / session id / 窗口一起更新） |
| `mode` | `mode` | `plan` / `ask` / `zen` |
| `usage` | — | tokens / cache / cost 汇总 |
| `approval` | `request_id`, `decision` | 回答 `approval_request`：`allow` / `always` / `deny` |
| `session` | `op`, `path?`, `scope?`, `format?` | `current` / `list` / `new` / `fork` / `resume` / `import` / `undo` / `delete` / `compact` / `retry` / `export` |
| `command` | `name`, `argument?` | 跑斜杠命令，返回 `message` / `screen` / `show_help` / `reset_transcript` / `quit` / `appearance` / `language` / `model` |

### 响应与事件

- `result` / `error` 与 v1 形状一致（v1 请求的行为不变）。
- `event`：`serialize_event` 的投影，新增 `args`（JSON 文本）、`result_text`、
  `duration_ms`、`tool_stats`；`partial_result` 仍是有界尾段。所有文本字段上限
  4000 字符。
- `approval_request`（服务器发起的事件）：
  `{"type":"event","event":{"type":"approval_request","request_id":"a0","tool_name":"bash","args":"{\"command\":\"ls\"}","kind":"confirm","risk":null,"target":null,"reason":"..."}}`，
  客户端用 `approval` 请求回答；默认 120 秒无响应按 `deny` 处理。

### 最小客户端

```python
import json, subprocess, sys
p = subprocess.Popen([sys.executable, "-m", "zenagent.cli.main", "rpc"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
p.stdin.write(json.dumps({"type":"prompt","id":"1","text":"say hi"}) + "\n"); p.stdin.flush()
for line in p.stdout:
    msg = json.loads(line)
    if msg.get("type") == "result" and msg.get("id") == "1":
        print(msg["text"]); break
p.stdin.write(json.dumps({"type":"shutdown","id":"2"}) + "\n"); p.stdin.flush()
```

### 常用请求示例

```json
{"type":"session","id":"s1","op":"new"}
{"type":"session","id":"s2","op":"list","scope":"all"}
{"type":"command","id":"c1","name":"mode","argument":"plan"}
{"type":"tools","id":"t1"}
{"type":"model","id":"m1","model_id":"deepseek-flash"}
```

## Python SDK（同进程嵌入）

```python
from zenagent.agent import Agent
from zenagent.tools import ALL_TOOLS

agent = Agent(
    model=model,                      # zenagent.providers.catalog.Model
    system_prompt="be brief",
    tools=list(ALL_TOOLS),
    api_key="...",
)

agent.subscribe(lambda event: print(event.type))   # 可选：观察事件
await agent.prompt("explain this repo")
print(agent.messages[-1].content[0].text)
```

常用 API：

| 成员 | 作用 |
|---|---|
| `await agent.prompt(text, images=None)` | 跑一轮（内部是 task，可并发 abort） |
| `agent.subscribe(fn)` | 订阅 AgentEvent，返回取消订阅函数 |
| `agent.steer(text)` | 追加 steering 消息（工具轮次后生效） |
| `agent.abort()` | 取消当前 run |
| `await agent.wait_for_idle()` | 等 run 结束 |
| `agent.messages` | 只读历史 |
| `agent.replace_messages(...)` / `agent.reset()` | 恢复/清空会话 |
| `agent.termination` | `None`/`stop`/`length`/`error`/`aborted`/`max_turns`/`max_cost` |

需要会话、MCP、skills、审批与命令时用 app 层：`Runtime.bootstrap(RuntimeOptions(...))`
+ `ChatController`，见 `examples/embed.py`；RPC 就是这套对象之上的 JSONL 协议。
完全离线的最小例子：`examples/custom_tool.py`；其他示例：`examples/headless.py`、
`examples/embed.py`、`examples/mcp.py`、`examples/rpc_client.py`。每个示例演示什么、
需要不要 key，见 `examples/README.md`。

## 源码对照

- RPC：`zenagent/cli/rpc.py`（`RpcServer`、`serialize_event`、审批往返）
- SDK：`zenagent/agent/agent.py::Agent`；app 层：`zenagent/app/{runtime,chat}.py`
- 示例：`examples/`
- 对应测试：`tests/test_rpc.py`、`tests/test_rpc_v2.py`、`tests/test_examples.py`
