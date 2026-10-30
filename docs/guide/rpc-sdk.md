# RPC 与 Python SDK

## RPC 模式（给编辑器/桌面壳用）

```bash
zen rpc [--model deepseek-flash] [--yes] [--max-turns N] [--max-cost X]
```

协议：**一行一个 JSON**；stdout 只写协议，日志走 stderr。

### 请求

```json
{"type":"prompt","id":"1","text":"explain this repo"}
{"type":"cancel","id":"2"}
{"type":"status","id":"3"}
{"type":"shutdown","id":"4"}
```

### 响应

```json
{"type":"event","event":{"type":"turn_start"}}
{"type":"event","event":{"type":"message_update","assistant_event":{"type":"text_delta","delta":"Hi"}}}
{"type":"result","id":"1","ok":true,"text":"Hi","usage":{"input":1007,"output":2,"cache_read":768,"cache_write":0,"cost":0.00008},"termination":"stop"}
{"type":"error","id":null,"message":"bad request: ..."}
```

行为：

- `prompt` 异步执行，事件立即流式写出；同一时间只允许一个 run（忙时返回 error）。
- `cancel` 设置 AbortToken，run 以 `termination="aborted"` 结束。
- `status` 返回 running/messages/model。
- `shutdown` 等待运行中的 run 被 abort 后退出。
- 坏 JSON、未知 type、空 prompt 都只报 error，进程继续。

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

完全离线的最小例子（自定义工具 + fake stream）：`examples/custom_tool.py`。
其他示例：`examples/headless.py`、`examples/mcp.py`、`examples/rpc_client.py`。

## 源码对照

- RPC：`zenagent/cli/rpc.py`（`RpcServer`、`serialize_event`）
- SDK：`zenagent/agent/agent.py::Agent`
- 示例：`examples/`
- 对应测试：`tests/test_rpc.py`、`tests/test_examples.py`
