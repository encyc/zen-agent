# 工具内核

## 契约

```python
@dataclass
class AgentTool:
    name: str
    description: str
    parameters: dict                  # JSON Schema（子集校验）
    execution_mode: str = "parallel"  # or "sequential"
    execute: Callable[[str, dict, AbortToken|None, Callable|None], Awaitable[ToolResultAgent]]

@dataclass
class ToolResultAgent:
    content: list[TextContent | ImageContent]   # 模型可见
    details: dict = {}                          # 仅 UI/日志
    is_error: bool = False
    terminate: bool = False                     # 结束本 run
```

不变量：

1. `execute` 必须返回 `ToolResultAgent`，不允许抛异常（抛了会被 loop 转成错误结果，但那是兜底）。
2. `content` 是唯一发给模型的内容，不要往 `details` 里塞模型需要的信息。
3. 长时间操作应检查 `abort.aborted` 并提前返回。

## 参数校验（`agent/validate.py`）

手写 JSON Schema 子集，**故意不递归**：

- 顶层 `type: "object"` 必须是 dict；
- `required` 里的字段必须存在；
- 声明了 `type` 的属性检查 string/number/integer/boolean/object/array；
- `bool` 不能当 integer/number（Python 里 bool 是 int 子类，专门排除）；
- 不处理 anyOf/oneOf/format/default/额外字段。

为什么窄：工具自己会防御内部；保持内核零依赖。错误信息带字段路径，模型能自己修。

## 内置工具

### fs

| 工具 | 关键限制 |
|---|---|
| `read_file` | `MAX_READ_LINES = 2000` 行，超出截断提示；二进制（前若干字节有 NUL）拒绝 |
| `write_file` | 覆盖写，返回摘要 |
| `edit_file` | old 必须唯一匹配；返回 unified diff（`MAX_DIFF_LINES = 200`） |
| `list_dir` | `MAX_LIST_ENTRIES = 500`，目录带 `/` 标记 |

### bash

- 输出上限 `MAX_OUTPUT_BYTES = 30_000`，超限保留**头 + 尾**（中间省略），保证能看到末尾错误。
- 默认超时 120s，可传 `timeout_s`。
- `start_new_session=True`：超时/取消时 `killpg` 杀整个进程组，避免孤儿进程。
- 危险命令（`rm -rf /`、`curl|sh`、fork bomb 等）在权限门里拦截。

### search

- `glob`：按模式找文件（不跟随符号链接逃逸）。
- `grep`：正则搜索；跳过二进制/超大文件；结果行数有上限。

## 并发执行模型

loop 里对每个 call：

1. 解析 JSON 参数（失败 → error 结果，不执行）；
2. `validate_arguments`（失败 → error 结果）；
3. `before_tool_call`（返回 `{"block": True, "reason": ...}` → error 结果，不执行）；
4. 执行（并行组 gather / 串行组顺序）；
5. `after_tool_call`（可替换结果或 patch content/is_error/terminate）；
6. 追加 `ToolResultMessage`，发出 `tool_execution_end`。

结果顺序与请求顺序一致，id 一一对应；并行只是执行顺序，不影响写回顺序。

## 写一个新工具

```python
from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.types import TextContent

async def hello(call_id, args, abort, on_update=None):
    return ToolResultAgent(content=[TextContent(text="hello")])

HELLO = AgentTool(name="hello", description="say hello", parameters={"type": "object"}, execute=hello)
```

加进 `zenagent/tools/__init__.py::ALL_TOOLS` 或通过扩展 `api.register_tool`。
写工具的检查清单：

- 参数校验失败/文件不存在等 → 返回 `is_error=True` 而不是抛异常；
- 输出有上限，避免把超大内容喂给模型；
- 破坏性操作先检查 `abort`；
- 相应测试覆盖边界（空文件、二进制、超长、唯一匹配失败）。

## 源码对照

- 契约与调度：`zenagent/agent/loop.py`
- 校验：`zenagent/agent/validate.py`
- 内置工具：`zenagent/tools/{fs,bash,search}.py`、`zenagent/tools/__init__.py`
- 测试：`tests/test_tools_fs.py`、`test_tools_bash.py`、`test_tools_search.py`、`test_validate.py`
