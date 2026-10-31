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
    approval: str = "exec"            # 工具声明的能力级：read / write / exec

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
4. `approval` 是**声明**不是强制：内核不拦任何调用；权限门（harness）读这个字段决定
   read-only 模式放行、是否需要确认。没声明的工具按 `exec`（最保守）处理。

## 参数校验（`agent/validate.py`）

手写 JSON Schema 子集，**故意不递归**：

- 顶层 `type: "object"` 必须是 dict；
- `required` 里的字段必须存在；
- 声明了 `type` 的属性检查 string/number/integer/boolean/object/array；
- `bool` 不能当 integer/number（Python 里 bool 是 int 子类，专门排除）；
- 不处理 anyOf/oneOf/format/default/额外字段。

为什么窄：工具自己会防御内部；保持内核零依赖。错误信息带字段路径，模型能自己修。

## 结果预算（`agent/result_budget.py`）

每个工具结果在写回 transcript 前都会过一层**内核级预算**（`after_tool_call`
之后执行），不只是内置工具——MCP / 扩展工具同样适用：

- 单行超过 `MAX_LINE_CHARS = 2000` 字符 → 中间截断（保留行首尾），标记
  `…[line truncated]`；
- 文本总字节超过 `MAX_RESULT_BYTES = 40_000` → 保留头部 20 KB + 尾部 12 KB，
  中间换成 `… [truncated N of M bytes: … narrow the tool call …] …`；
- `ImageContent` 原样保留；
- 结果 `details["budget"]` 记录 `truncated / original_bytes / truncated_lines`。

各工具自己的硬上限（bash 30 KB、fetch 12k 字符……）仍然保留，是第一道防线；
这层是兜底，保证"新增工具忘了设上限"不会把上下文打爆。`terminal` 与
`is_error` 语义不变。

## 内置工具

### fs

| 工具 | 关键限制 |
|---|---|
| `read_file` | 默认每次 `DEFAULT_READ_LIMIT = 400` 行、硬上限 `MAX_READ_LIMIT = 2000`；支持 `offset`（0 基行号）/`limit`，截断页尾给出下一页的精确 `offset`；二进制（前若干字节有 NUL）/非 UTF-8 拒绝 |
| `write_file` | 覆盖写，返回摘要；**拒绝把"截断的 read 页"回写**：content 仍带 `[truncated:` 页尾且比磁盘文件短 → `is_error`，防止静默截断数据 |
| `edit_file` | old 必须唯一匹配；返回 unified diff（`MAX_DIFF_LINES = 200`） |
| `list_dir` | `MAX_LIST_ENTRIES = 500`，目录带 `/` 标记 |

### bash

- 输出上限 `MAX_OUTPUT_BYTES = 30_000`，超限保留**头 + 尾**（中间省略），保证能看到末尾错误。
- 默认超时 120s，可传 `timeout_s`；`cwd` 可指定工作目录（相对/绝对，缺省为进程 cwd，不跨调用泄漏）。
- **非交互环境**：未设置时补 `PAGER/GIT_PAGER/MANPAGER=cat`、`TERM=dumb`、`CI=true`、
  `GIT_TERMINAL_PROMPT=0`、`PIP_NO_INPUT=1`、`npm_config_yes=true` 等（`NON_INTERACTIVE_ENV`，
  用户真实 env 优先），避免 `git log`/`man`/`npm install` 卡在分页或提示上。
- `start_new_session=True`：超时/取消时 `killpg` 杀整个进程组，避免孤儿进程。
- 危险命令（`rm -rf /`、`curl|sh`、fork bomb 等）在权限门里拦截。

### search

- `glob`：按模式找文件（不跟随符号链接逃逸）。
- `grep`：正则搜索；跳过二进制/超大文件；结果行数有上限。

## 并发执行模型

loop 里对每个 call：

1. 解析 JSON 参数（失败 → error 结果，不执行）；
2. `validate_arguments`（失败 → error 结果）；
3. `before_tool_call(tool_call, args, tool)`（返回 `{"block": True, "reason": ...}` → error 结果，不执行；`tool` 是解析后的 `AgentTool`，权限门靠它读 `approval` / 名称）；
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

HELLO = AgentTool(
    name="hello",
    description="say hello",
    parameters={"type": "object"},
    execute=hello,
    approval="read",  # 只读工具：plan / --read-only 下放行，免确认
)
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
