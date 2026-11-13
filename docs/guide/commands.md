# 斜杠命令

TUI 与 REPL **共用同一个** `CommandRegistry`（`zenagent/app/commands.py`，48 条内置命令）：
`/help` 列出全部命令，裸命令（如 `/model`）在 TUI 打开选择器、在 REPL 打印文本。
不认识的 `/xxx` 会得到**未知命令**提示（REPL 打印 `未知命令：/xxx（试 /help）`，TUI 在转录区
给一条 notice），**不会**被当作普通提问发给模型；如果名字匹配 prompt 模板
（`.zenagent/commands/*.md` 的 `/name args`）或扩展注册的命令，则展开模板 / 执行扩展命令。

表格里的命令名都可以直接配 Tab 补全；带参数的命令见每节末尾的说明。

## 通用

| 命令 | 作用 |
|---|---|
| `/help` | 列出全部命令 |
| `/quit` | 退出应用（Ctrl+D 也可以） |
| `/exit` | 退出应用（`/quit` 的别名） |
| `/settings` | 打开设置 |
| `/fresh` | 重置临时显示状态（不清会话） |

## 会话与历史

| 命令 | 作用 |
|---|---|
| `/new` | 开始新会话 |
| `/clear` | 清空可见对话 |
| `/sessions` | 浏览、恢复或 fork 会话（`all` 含跨项目与 legacy） |
| `/session` | 显示本会话的模型、上下文与花费 |
| `/rename` | 重命名当前会话 |
| `/pin` | 置顶/取消置顶当前会话 |
| `/fork` | 把本会话复制成分支并切换过去 |
| `/import` | 加载会话文件并继续它 |
| `/compact` | 立即压缩对话（写新会话文件，旧文件不动） |
| `/undo` | 撤回上一轮（同样写新文件，可恢复） |
| `/retry` | 重跑上一轮 |
| `/handoff` | 生成交接文档后压缩会话 |
| `/shake` | 丢弃工具结果/图片/思考，不重新总结 |
| `/export` | 把当前会话导出为 markdown |

`/sessions`、`/fork`、`/import`、`/shake` 会写**新的**会话文件：`/undo`、`/fork`、`/compact` 都不改写历史。

## 模型与运行模式

| 命令 | 作用 |
|---|---|
| `/model` | 浏览并切换模型（下一轮生效） |
| `/thinking` | 查看或设置推理等级：`off`｜`low`｜`medium`｜`high`｜`max` |
| `/mode` | 设置运行模式：`plan`｜`ask`｜`auto` |
| `/plan-review` | 复核 plan 模式下产出的计划，批准后切到 `auto` |
| `/lang` | 设置界面语言：`auto`｜`en`｜`zh` |
| `/theme` | 设置配色主题：`auto`｜`dark`｜`light`（REPL 另接受 legacy 主题名或 JSON 路径，如 `/theme solarized`） |

`/mode`、`/thinking`、`/model` 的参数都支持 Tab 补全；不带参数时 TUI 打开选择器、REPL 打印当前值。

## 输入与附加

| 命令 | 作用 |
|---|---|
| `/image` | 为下一条提示附加图片（`/image <path>`） |
| `/images` | 列出已附加图片，或 `/images clear` |
| `/add-dir` | 记录一个额外工作目录 |
| `/remove-dir` | 取消记录额外目录 |
| `/dirs` | 列出工作目录与额外根 |
| `/copy` | 复制文本、上一条回答、或其中第 `<n>` 条链接 |
| `/urls` | 列上一条回答里的链接（编号 + 长度） |
| `/open` | 在浏览器里打开上一条回答的第 `<n>` 条链接 |
| `/btw` | 旁路提问，不污染主上下文 |

`/copy` 有三种形态：不带参数复制上一条回答、纯数字（`/copy 2`、`/copy #2`）复制第 2 条链接的**原始字节**、
其它参数按字面文本复制。链接是从原始 markdown 文本里抽的（`zenagent/app/links.py`），不是渲染层，
所以代码块里的长 URL 也能拿到完整版本。

## 诊断与工具

| 命令 | 作用 |
|---|---|
| `/doctor` | 运行环境检查 |
| `/tools` | 列出模型可调用的工具（含 MCP、skill、扩展注册的） |
| `/skills` | 列出发现的 skills（会重新扫描目录） |
| `/reload` | 重新发现 skills、模板与扩展，当场生效 |
| `/mcp` | 重连 MCP server 并刷新工具 |
| `/usage` | 本会话的 token 与花费总计 |
| `/context` | 上下文窗口占用情况 |
| `/todo` | 查看 todo 列表：TUI 打开可编辑浮层（与模型 `todo` 工具共用 `<session>.todo.json`），REPL 打印只读文本 |
| `/memory` | 查看与编辑项目记忆库（TUI 面板 / REPL 文本） |
| `/jobs` | 列出后台作业，或 `/jobs kill <id>` 终止一个 |
| `/hotkeys` | 显示当前键位 |
| `/trace` | 显示 trace 文件路径（启动时带 `--trace` 才有） |
| `/search` | 联网搜索，或打开搜索设置 |
| `/restore` | 恢复上一次 agent 运行改动的文件（`/restore list` 列历史运行） |

## 执行中插话（steering）

agent 正在跑的时候，输入框里的内容**不是**新的一轮：它作为插话排队，在当前轮结束时交给模型，所以不会
并发跑两个 run。Ctrl+C 才是协作式中止。若一轮因为 `max_turns`/`max_cost` 结束，没送达的插话降级为下一条
排队输入，不自动重试。

## 源码对照

- 注册与派发：`zenagent/app/commands.py::CommandRegistry`、`Command`、`_command`、`dispatch`
- 链接抽取（`/urls`、`/copy <n>`、`/open <n>`）：`zenagent/app/links.py::extract_links`、`parse_index`
- 命令文案（中英）：`zenagent/i18n/{en,zh}.py` 的 `cmd.<name>.description`
- 前端渲染差异：`zenagent/cli/repl.py`（文本）与 `zenagent/cli/tui/app.py`（选择器/浮层）
- 对应测试：`tests/test_app_commands.py`、`tests/test_app_links.py`、`tests/test_docs_commands.py`
