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
| `web_search` | 联网搜索，返回标题/URL/摘要（DeepSeek → Tavily → Brave → Bing → DuckDuckGo） | 只读，免确认 |
| `fetch` | 抓取 URL 并转成可读文本（剥 HTML，12k 字符上限） | 只读，免确认 |
| `read_skill` | 读本地 SKILL.md 正文 | 只读 |

每个工具还声明一个能力级（`AgentTool.approval = "read" | "write" | "exec"`）：
权限门读声明决定「plan 是否放行 / 是否需要确认」，而不是维护一份硬编码工具名表；
没声明的工具按 `exec` 处理（最保守）。MCP 工具如果服务器声明了
`annotations.readOnlyHint`，也会被当成 `read`。

工具签名是：

```python
async def execute(tool_call_id, args, abort, on_update=None) -> ToolResultAgent
```

- `abort`：长任务（bash）可提前退出。
- `on_update`：流式部分结果（bash 用它推送运行中输出，TUI 工具卡实时显示）。
- 返回值里 `content` 给模型，`details` 给 UI/日志。

## 运行模式（plan / ask / full）

启动由 `--mode` 决定（`--yes`→full、`--read-only`→plan 只是兼容映射，显式 `--mode` 优先）；
会话中 `/mode` 切换，下一次工具调用立即生效；TUI 状态带显示当前模式。

| 模式 | 写/执行工具 | 说明 |
|---|---|---|
| `plan` | 拒绝 | 只读调研；system prompt 要求先调研并给出计划 |
| `ask` | 需确认 | REPL 终端 `[y/n/a]`；TUI 审批框（y/a/n，120s 超时=拒绝）；headless 默认拒绝 |
| `full` | 自动放行 | 危险命令仍需 `--allow-dangerous`；`[tools] deny` 永远优先 |

优先级：危险命令、`[tools] deny`、plan、`--read-only` 属于**策略层**，优先于 full 和会话内
"always allow"。`PermissionBroker` 先跑 `PermissionGate.policy_refusal`，只有 ask 才弹确认。

## 权限判定顺序

`PermissionGate.check(tool_name, args, tool=None)`（`zenagent/app/permissions.py`，
`zenagent/cli/permissions.py` 是兼容导入）。`tool` 为解析后的 `AgentTool`
时，其 `approval` 声明优先；`tool=None`（直接按名字调用）时退回
`READ_ONLY_TOOLS` 名表：

```
危险命令？        ── 是 ──> 拒绝（除非 --allow-dangerous）
   │否
deny 模式命中？    ── 是 ──> 拒绝（原因里带命中的 pattern）
   │否
--read-only？      ── 是且非只读工具 ──> 拒绝
   │否
allow 模式命中？   ── 是 ──> 允许（不再询问）
   │否
工具声明 read / 名表只读？ ── 是 ──> 允许
   │否
--yes 或交互确认？ ── 是 ──> 允许
   │否
拒绝
```

关键点：

1. **allowlist 救不了危险命令**：`allow = ["*"]` 也不能放行 `rm -rf /`。
2. `--read-only` 下，`write_file`、`bash` 等写/执行工具全拒；
   `read_file/grep/glob/list_dir/web_search/fetch/read_skill` 放行；
   MCP 里声明了 `readOnlyHint` 的工具也放行，其余 MCP 工具仍拒绝。
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
- 权限：`zenagent/app/permissions.py::PermissionGate`（`policy_refusal`/`check`）、`PermissionBroker`、
  `zenagent/app/modes.py`（`resolve_mode`/`mode_prompt_note`）、`READ_ONLY_TOOLS`、`dangerous_command_reason`
- 校验：`zenagent/agent/validate.py::validate_arguments`
- 执行与错误隔离：`zenagent/agent/loop.py`（工具 round）
- 对应测试：`tests/test_tools_fs.py`、`test_tools_bash.py`、`test_tools_search.py`、`test_tools_web.py`、
  `test_permissions.py`、`test_modes.py`、`test_app_permissions.py`、`test_tui_approval.py`、`test_validate.py`
- `web_search` 的 provider 链与配置见 [`docs/internals/web-search.md`](../internals/web-search.md)：有 `DEEPSEEK_API_KEY` 时默认走 DeepSeek，无 key 时退到 Bing/DuckDuckGo；顺序可用 `ZENAGENT_SEARCH_PROVIDERS` 覆盖。
- `/search` 查看 provider 状态，`/search <query>` 手动跑一次。
- 搜索走的是"压空白 + 长度上限"，**不改写原文**（与 omp 一致）；剥 markdown 会吃掉代码里的 `# comment`。
