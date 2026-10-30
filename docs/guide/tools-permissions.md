# 工具与权限

## 内置工具

| 工具 | 能力 | 默认权限 |
|---|---|---|
| `read_file` | 读文本，带行号；有大小上限 | 只读，免确认 |
| `write_file` | 覆盖写文件 | 需确认 |
| `edit_file` | 唯一匹配替换（old→new） | 需确认 |
| `list_dir` | 列目录（标记目录/文件） | 只读 |
| `bash` | 执行命令，禁止危险模式 | 需确认 |
| `glob` | 按模式找文件 | 只读 |
| `grep` | 正则搜内容（忽略二进制/大文件） | 只读 |
| `web_search` | 联网搜索，返回标题/URL/摘要（Brave → Bing → DuckDuckGo） | 只读，免确认 |
| `fetch` | 抓取 URL 并转成可读文本（剥 HTML，12k 字符上限） | 只读，免确认 |
| `read_skill` | 读本地 SKILL.md 正文 | 只读 |

每个工具的签名都是：

```python
async def execute(tool_call_id, args, abort, on_update=None) -> ToolResultAgent
```

- `abort`：长任务（bash）可提前退出。
- `on_update`：流式部分结果（目前没有内置工具用它，留给扩展）。
- 返回值里 `content` 给模型，`details` 给 UI/日志。

## 权限判定顺序

`PermissionGate.check(tool_name, args)`（`zenagent/cli/permissions.py`）：

```
危险命令？        ── 是 ──> 拒绝（除非 --allow-dangerous）
   │否
deny 模式命中？    ── 是 ──> 拒绝（原因里带命中的 pattern）
   │否
--read-only？      ── 是且非只读工具 ──> 拒绝
   │否
allow 模式命中？   ── 是 ──> 允许（不再询问）
   │否
只读工具？         ── 是 ──> 允许
   │否
--yes 或交互确认？ ── 是 ──> 允许
   │否
拒绝
```

关键点：

1. **allowlist 救不了危险命令**：`allow = ["*"]` 也不能放行 `rm -rf /`。
2. `--read-only` 下，`write_file`、`bash`、MCP 工具全拒；`read_file/grep/glob/list_dir/web_search/fetch/read_skill` 放行。
3. 拒绝不是异常：工具调用收到 `is_error=True` 的结果，模型能看到原因并调整。
4. 交互模式下 REPL 会问 `allow? [y/N]`；headless 没有交互，默认拒绝。

## 工具结果的形状

```python
ToolResultAgent(
    content=[TextContent(text="...")],  # 发给模型
    details={"path": "..."},            # 只给 UI/日志
    is_error=False,
    terminate=False,                    # True 表示这一轮后结束 run
)
```

- 参数解析失败：`is_error=True`，内容为 "arguments are not valid JSON"。
- schema 校验失败：`is_error=True`，错误里带字段路径（`arguments.text: expected string`）。
- 工具抛异常：loop 捕获，`is_error=True`，异常文本截断到 500 字符。
- 并行执行时，所有结果按 assistant 请求顺序写回，`id` 一一对应。

## 危险命令

`zenagent/tools/bash.py` 会拒绝：

- `rm -rf /`、`rm -rf /*` 等根路径删除；
- `curl … | sh` 一类「下载即执行」；
- fork bomb 形态；
- 其他明显破坏性模式。

拦截发生在权限门之前（permission gate 里 `dangerous_command_reason`），`--yes` 不放行，
只有显式 `--allow-dangerous` 才允许。

## 源码对照

- 工具实现：`zenagent/tools/{fs,bash,search,web}.py`，清单 `zenagent/tools/__init__.py::ALL_TOOLS`
- 权限：`zenagent/cli/permissions.py::PermissionGate`、`READ_ONLY_TOOLS`、`dangerous_command_reason`
- 校验：`zenagent/agent/validate.py::validate_arguments`
- 执行与错误隔离：`zenagent/agent/loop.py`（工具 round）
- 对应测试：`tests/test_tools_fs.py`、`test_tools_bash.py`、`test_tools_search.py`、`test_tools_web.py`、`test_permissions.py`、`test_validate.py`
- `web_search` 的 `BRAVE_API_KEY` 存在时优先 Brave；否则依次试 Bing、DuckDuckGo（受限网络里通常只有 Bing 可用）。
