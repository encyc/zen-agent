# 扩展（Python 模块）

一个扩展就是一个 Python 文件，暴露 `register(api)`。它只能通过 `api` 贡献四类东西，
**不能**碰 agent 循环内部——这是有意的窄接口。

## 最小例子

```python
# ~/work/my-ext.py
from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.types import TextContent


async def echo(call_id, args, abort, on_update=None):
    return ToolResultAgent(content=[TextContent(text=f"EXT: {args.get('text', '')}")])


async def hello(agent, argument):
    return f"hello {argument or 'there'}"


def register(api):
    api.register_tool(AgentTool(
        name="ext_echo",
        description="Echo with an EXT prefix",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        execute=echo,
    ))
    api.register_command("hello", "say hello", hello)
    api.add_system_prompt("PROJECT RULE: end every answer with EXT-OK.")
    api.on("agent_end", lambda event: print("run finished"))
```

启用：

```bash
zen --extension ~/work/my-ext.py
```

或在 config.toml：

```toml
extensions = ["~/work/my-ext.py"]
```

## API

| 方法 | 作用 |
|---|---|
| `register_tool(AgentTool)` | 增加工具（同样过权限门） |
| `register_command(name, description, handler)` | 增加 `/name`；handler `(agent, argument) -> str \| None`，可 async |
| `add_system_prompt(text)` | 追加 system prompt 片段 |
| `on(event_type, callback)` | 订阅 AgentEvent（`agent_start/agent_end/turn_start/...`） |

## 行为与边界

- **隔离**：import 失败、`register()` 抛异常、没有 `register` → 只 warning 并跳过，其他扩展照常加载。
- **命令冲突**：内置斜杠命令优先；扩展之间重名保留第一个并 warning。
- **hook 异常**：被捕获并打印 warning，不会中断 run。
- **权限**：扩展工具和内置工具一样经过 `PermissionGate`；`--read-only` 下非只读工具全拒。
  给只读扩展工具设 `AgentTool(..., approval="read")` 即可免确认。
- **`/reload`**：会重新 import 扩展并替换工具/命令/prompt；但**已订阅的 hooks 不会重复订阅**
  （避免副作用翻倍），hook 变更需要重启会话。

## 源码对照

- 加载与隔离：`zenagent/extensions.py`（`load_extensions`、`ExtensionAPI`、`LoadedExtensions`）
- CLI 接线：`zenagent/cli/main.py`（headless）、`zenagent/cli/repl.py`（命令、hooks、`/reload`）
- 对应测试：`tests/test_extensions.py`
