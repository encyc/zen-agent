# RPC 与 Python SDK

## RPC 模式（编辑器/桌面壳嵌入）

```bash
zen rpc [--model ID] [--yes] [--mode plan|ask|auto] [--read-only]
        [--thinking LEVEL] [--max-turns N] [--max-cost X] [--retries N]
        [--resume [SESSION]] [--no-mcp] [--mcp-file PATH] [--mcp-server NAME]
        [--no-skills] [--no-templates] [--no-auto-compact] [--extension PATH]
        [--no-context-files] [--trace PATH] [--metrics PATH]
        [--cwd PATH] [--allow-dangerous] [--allow-outside PATH]
        [--allow-destructive] [--allow-protected]
```

`--mode`/`--thinking` 是 argparse choices，非法值由 argparse 以退出码 2 拒绝；不传
`--mode` 时按 `--read-only`（plan）/`--yes`（auto）/默认 ask 解析，与 TUI/REPL/headless
同一套 `resolve_mode`。启动期模型未知或缺少 API key 打一行 stderr 后以 2 退出，
`--cwd` 不是目录是 1，Ctrl+C 是 130。没有 `--theme`/`--image`：图片走 `prompt.images`。

协议：**一行一个 JSON**；stdout 只写协议，日志走 stderr。`zen rpc` 启动的是宿主
`RpcHost`：先为一个项目创建 Runtime slot，再按需为其他项目或并行会话开 slot。每个
slot 仍是完整的 `Runtime`，所以会话持久化、MCP、skills、扩展、工具集和审批与
TUI/REPL 是同一套实现（headless `-p` 例外，它自己装配 Agent，见
[功能地图](../feature-map.md) §6.5）。当前 `PROTOCOL_VERSION = 3`，握手与桌面契约由
`tests/test_rpc_contract.py` 双向钉住。

### 请求

| type | 字段 | 作用 |
|---|---|---|
| `prompt` | `text`, `images?` | 跑一轮；`images` 是图片路径列表（同 `--image` 的校验：非 vision 模型/坏文件报 `error`）。运行中再发返回 `error`（`message: "busy"`） |
| `cancel` | — | abort 当前 run |
| `status` | — | running / busy / messages / model / session / mode / tools / usage / termination / follow_ups / steering / cost，以及 TUI reducer 字段：thinking / context_used / context_window / cache_hit_ratio / turn_ms / todos / last_error / pending / jobs / subagents / active_subagent / subagent_cost / subagent_tokens |
| `shutdown` | — | 等运行中的 run abort 后退出 |
| `steer` | `text` | 插话；空闲时进入 follow-up 排队 |
| `tools` | — | 模型可见工具 `[{name, description, approval}]` |
| `model` | `model_id` | 切模型（key / thinking / session id / 窗口一起更新） |
| `mode` | `mode` | `plan` / `ask` / `auto`（旧客户端可继续发 `zen`，服务端归一为 `auto`） |
| `usage` | — | tokens / cache / cost 汇总 |
| `approval` | `request_id`, `decision` | 回答 `approval_request`：`allow` / `always` / `deny` |
| `session` | `op`, `path?`, `scope?`, `format?` | `current` / `list` / `new` / `fork` / `resume` / `import` / `undo` / `delete` / `compact` / `retry` / `export` |
| `command` | `name`, `argument?` | 跑**内置**斜杠命令（48 条；模板与扩展命令只在 TUI/REPL 可用），返回 `message` / `screen` / `target` / `show_help` / `reset_transcript` / `quit` / `appearance` / `language` / `model`；`/lang` 会在服务器侧生效 |

### 响应与事件

- `result` / `error` 的既有键与 v1 形状一致；v2 只做**增量字段**，v1 客户端忽略未知键即可。
- `prompt` 的 `result`：`ok`（provider 失败或提前终止时为 `false`）、`text`（流式 delta；
  provider 只发终止消息时回退到该条 assistant 消息）、`usage`、`termination`、`error`
  （`ok:false` 时的原因），有 Runtime 时再带 `session`。
- `event`：`serialize_event` 的投影，带 `message` / `new_messages`（最多 20 条的
  bounded 投影）、`args`（JSON 文本）、`result_text`、工具 `details`（diff/exit_code/
  task_id/todo phases/子代理花费等结构化字段）、`duration_ms`、`tool_stats`，以及
  assistant 失败时的 `assistant_event.error`；`partial_result` 仍是有界尾段。所有文本
  字段上限 4000 字符。
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
{"type":"prompt","id":"p1","text":"what is in this picture?","images":["shot.png"]}
{"type":"session","id":"s1","op":"new"}
{"type":"session","id":"s2","op":"list","scope":"all"}
{"type":"command","id":"c1","name":"mode","argument":"plan"}
{"type":"command","id":"c2","name":"lang","argument":"zh"}
{"type":"tools","id":"t1"}
{"type":"model","id":"m1","model_id":"deepseek-flash"}
```

### 宿主级请求

宿主本身处理少量跨 slot 的请求；没有 `project` 字段时落在当前激活 slot 的项目上。
其余请求原样转发给目标 slot，并且 slot 发出的帧都带 `project`。

| type | 字段 | 作用 |
|---|---|---|
| `hello` | — | 协议版本握手：返回 `protocol_version`、当前 `active` 项目和全部常驻 `projects` |
| `project` | `op`, `root?`, `resume?`, `background?` | `open` / `switch` / `close` / `relocate`；打开项目或并行会话 slot |
| `workspace` | `op`, `mode?`, `routerSession?` | `get` / `set` 项目模式（`coding` / `zen` / `swarm`） |
| `supervisor` | `op`, `key?`, `mode?`, `prompt?`, `session?`, `agent?`, `role?`, `ref?`, `images?`, `requestId?`, `decision?` | `status` / `dispatch` / `approve` / `cancel`；live agent 注册表与统一派工入口 |
| `runs` | `op`, `runId?`, `kind?`, `agent?`, `workItem?`, `session?`, `status?`, `since?`, `limit?` | `list` / `get` 项目 run 账本（只读） |
| `zen_fleet` | `op` | `enable` / `disable` / `state` / `transcript`；Zen 名册与 worker 只读转录 |
| `swarm` | `op` | 房间与任务：`list` / `post` / `cancel` / `retry` / `reset` / `roles` / `reload` |
| `terminal` | `op` | 桌面终端会话 |

```json
{"type":"workspace","id":"w1","op":"get","project":"/repo"}
{"type":"supervisor","id":"s1","op":"status","project":"/repo"}
{"type":"supervisor","id":"s2","op":"dispatch","project":"/repo","mode":"swarm","prompt":"@research 对比这两个方案"}
{"type":"runs","id":"r1","op":"list","project":"/repo","kind":"swarm","limit":20}
```

`supervisor op=dispatch` 的 `mode` 省略时读 `workspace.json`；coding 不派工（直接
`prompt`），zen 需要 `session` 或 live `agent`，swarm 可带 `role` / `ref` / `images`。
回执只确认“已交给编排器”，不会伪造 `runId`；用 `runs` 查真正的 run。

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

- RPC：`zenagent/cli/rpc/`（`RpcHost`、`RpcServer`、`serialize_event`、`ops_*`、审批往返）
- SDK：`zenagent/agent/agent.py::Agent`；app 层：`zenagent/app/{runtime,chat}.py`
- 示例：`examples/`
- 对应测试：`tests/test_rpc.py`、`tests/test_rpc_v2.py`、`tests/test_examples.py`
