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
    timeout_s: float | None = None    # 内核级 deadline（None = 工具自管/无）

@dataclass
class ToolResultAgent:
    content: list[TextContent | ImageContent]   # 模型可见
    details: dict = {}                          # 仅 UI/日志
    is_error: bool = False
    terminate: bool = False                     # 结束本 run
    error_kind: str = ""                        # 机器可读失败类别（循环自动填）
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
- 结果 `details["budget"]` 记录 `truncated`（恒 True）与 `original_bytes`，行截断时另有
  `truncated_lines`；未超预算的结果原样返回、不写 `budget`。

各工具自己的硬上限（bash 30 KB、fetch 12k 字符……）仍然保留，是第一道防线；
这层是兜底，保证"新增工具忘了设上限"不会把上下文打爆。`terminate` 与
`is_error` 语义不变。

## 内置工具

### fs

| 工具 | 关键限制 |
|---|---|
| `read_file` | 默认每次 `DEFAULT_READ_LIMIT = 400` 行、硬上限 `MAX_READ_LIMIT = 2000`；支持 `offset`（0 基行号）/`limit`，截断页尾给出下一页的精确 `offset`；二进制（前若干字节有 NUL）/非 UTF-8 拒绝 |
| `write_file` | 覆盖写，返回摘要；保留已有文件的 CRLF/BOM 约定（新文件 LF/无 BOM）；**拒绝把"截断的 read 页"回写**：内容剥离"截断页脚 / 预算 notice / 行截断标记"后，body 比磁盘文件短 → `is_error`，防止静默截断数据 |
| `edit_file` | old 默认必须唯一匹配（`replace_all=true` 时全替换）；匹配对换行风格不敏感；返回 unified diff（`MAX_DIFF_LINES = 200`）；写回保留 CRLF/BOM |
| `list_dir` | `MAX_LIST_ENTRIES = 500`，目录带 `/` 标记 |

### bash

- 输出上限 `MAX_OUTPUT_BYTES = 30_000`，超限保留**头 + 尾**（中间省略），保证能看到末尾错误。
- 默认超时 120s，可传 `timeout_s`；`cwd` 可指定工作目录（相对/绝对，缺省为进程 cwd，不跨调用泄漏）。
- **非交互环境**：未设置时补 `PAGER/GIT_PAGER/MANPAGER=cat`、`TERM=dumb`、`CI=true`、
  `GIT_TERMINAL_PROMPT=0`、`PIP_NO_INPUT=1`、`npm_config_yes=true` 等（`NON_INTERACTIVE_ENV`，
  用户真实 env 优先），避免 `git log`/`man`/`npm install` 卡在分页或提示上。
- `start_new_session=True`：超时/取消时 `killpg` 杀整个进程组，避免孤儿进程。
- 危险命令（`rm -rf /`、`curl|sh`、fork bomb 等）在权限门里拦截。

### todo（会话级编排）

- 单个工具 `todo`（`tools/todo.py`），`approval="read"`、`execution_mode="sequential"`：
  它只改会话进度状态，不碰工作区文件；串行保证同一轮的多个 todo 变更不交错。
- 9 个 op：`init/start/done/rm/drop/block/unblock/append/view`；任务**按内容寻址**
  （不生成 ID，模型传 `task-N` 会得到专门文案）；一次调用若任一 op 非法，整批丢弃且
  状态不变（原子）。
- 状态写入 `<session>.todo.json`（mkstemp + fsync + `os.replace` 原子替换）；
  无会话文件时仅内存。`details["phases"]` 是 UI/HUD 的 canonical 快照，
  `summarize_todo`/`coerce_todo_phases` 供控制器复用。
- 启用面：TUI 走 `Runtime.bootstrap` 注入；headless/REPL 绑定各自 session；
  RPC 无 JSONL 会话，用内存态。

### 后台作业（`bash async` + `wait`）

- `bash` 的 `async: true` 会通过 `tools/jobs.py` 的 `JobManager` 启动后台作业并立即返回
  `job_id`（`bg_1`…）与 `status="running"`；后台 deadline 默认 600s（`timeout_s` 可覆盖
  1–3600），超时杀整个进程组；输出同样过 30KB head+tail 缓冲。
- `wait`（`approval="read"`、`execution_mode="sequential"`）等待指定/任意运行中作业：
  完成返回 `status/exit_code/duration` + 输出；`timeout_s`（默认 120，1–600）到点返回
  still-running 快照；未知 `job_id` 报错并列出已知 id；没有作业时返回 "nothing to wait for"。
  等待过程可被用户 abort（作业继续运行，之后仍可 wait）。
- 作业管理器是**进程内单例**（按事件循环绑定）：`Runtime` 在 bootstrap 时 acquire、
  `close()` 时 release，**最后一个 Runtime 关闭才真正清理**（多 Runtime/嵌入场景不会互相杀作业）。
  未 wait 的已完成作业最多保留 `MAX_RETAINED_JOBS = 32` 个，超出按完成时间回收。
  注意 `wait` 不传 `job_id` 时选择的是**进程内**最早的运行中作业（单 Runtime CLI 下等价于本会话）。
- **shell 抽象**（`tools/shell.py`）：前台 bash 与后台作业共用 `resolve_shell()`；
  `ZENAGENT_SHELL=posix|powershell|pwsh|cmd|<绝对路径>`，Windows 默认探测
  pwsh→powershell→cmd（**这是相对旧 `create_subprocess_shell` 的行为变化**；需要旧行为用
  `ZENAGENT_SHELL=cmd`）；未知值 → bash 返回 is_error。POSIX 用 `/bin/sh -c`
  且 `start_new_session`（超时可 killpg）。

### search

- `glob`：按模式找文件（不跟随符号链接逃逸）；`limit` clamp 到 1–2000、`include_hidden` 控制 dotfile，`.git` 永远跳过；`details` 带 `matches/shown/limit`。
- `grep`：正则搜索；跳过二进制/超大文件；按文件分页（每页 20 个文件、每文件 20 条命中，
  内部扫描上限 2000 条），页脚给出下一页 `skip`，`rg` 与 Python fallback 语义一致。

## 并发执行模型

loop 里对每个 call：

1. 解析 JSON 参数（失败 → error 结果，不执行）；
2. `validate_arguments`（失败 → error 结果）；
3. `before_tool_call(tool_call, args, tool)`（返回 `{"block": True, "reason": ...}` → error 结果，不执行；`tool` 是解析后的 `AgentTool`，权限门靠它读 `approval` / 名称）；
4. 执行（并行组 gather / 串行组顺序）；
5. `after_tool_call`（可替换结果或 patch content/is_error/terminate）；
6. 追加 `ToolResultMessage`，发出 `tool_execution_end`。

结果顺序与请求顺序一致，id 一一对应；并行只是执行顺序，不影响写回顺序。

### 超时与取消

- 内核级 deadline 由 `agent/timeouts.py` 决定：工具自己的 `timeout_s` 优先，其次
  `TOOL_TIMEOUTS` 表（`web_search`/`fetch`/`mcp`），clamp 到 0.1–600s；`bash` 不在表内
  （它自己 `killpg`，避免双重计时）。
- 超时 → `error_kind="timeout"` 的 `is_error` 结果，loop 继续下一轮。

### 错误类别（`error_kind`）

loop 的降级路径都会填一个机器可读类别：`unknown_tool` / `invalid_arguments` /
`policy`（权限或自定义 hook 拦截）/ `tool_error`（工具异常）/ `timeout`；
工具自己返回的错误可以带更具体的类别。当前类别只由 loop 设置、供调试/后续遥测使用；
它还没有被 UI 消费，也没有持久化到 session JSONL，模型仍按文本处理。

### 并发与 exclusive

- 默认 `execution_mode="parallel"`：并行批用 `asyncio.gather` 同时跑。
- **exclusive** = `execution_mode="sequential"` 或 `approval="write"`：先等并行批全部结束，
  再按请求顺序逐个执行。文件写类工具因此不会与读/搜索交错；这不是"两轨并发"。

### 流式进度（partial result）

- 工具的 `execute(..., on_update)` 可以推送 `ToolResultAgent` 快照；loop 把它包成
  `tool_execution_update` 事件（带 `tool_call_id` / `partial_result`）。
- 目前只有 `bash` 发射（4 KB / 0.5s 节流）；TUI 把最后一行显示在运行中的工具卡头部
  （`ToolCallView.progress`），REPL/headless 安全忽略该事件（结束行仍由
  `tool_execution_end` 打印）。
- 工具返回后到达的迟到 update 被丢弃，不会覆盖已结算的卡片。

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
