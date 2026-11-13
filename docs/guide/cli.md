# CLI 使用指南

## 安装

```bash
uv tool install "zenagent-py[cli]"   # 发布版；import 名是 zenagent
# 或在仓库里开发：
uv sync --extra cli
```

两个入口都指向同一实现：`zen`（推荐）和 `zenagent`。

## 三种运行方式

```bash
zen                                  # 交互 REPL
zen -p "explain this repo"           # headless 单次运行
printf '%s\n' '{"type":"status","id":"1"}' '{"type":"shutdown","id":"2"}' | zen rpc
```

## headless（`-p`）

```bash
zen -p "create /tmp/x/hello.txt, then read it back" -m deepseek-flash --yes --thinking high
zen -p "what is in this picture?" --image shot.png
zen -p "say hi" --json
zen -p "analyze" --read-only --trace run.jsonl --cwd /path/to/project
```

行为要点：

- 默认是 `ask` 模式：写文件/bash 需要确认；headless 下没有交互，所以要么 `--mode auto`/`--yes`，
  要么用 `[tools] allow` 放行，要么 `--read-only`/`--mode plan` 只读。
- `--json` 时 stdout 只有一段 JSON：`ok`、`text`、`usage`、`messages`、`error`、`termination`，
  没有工具调用列表（要看逐次工具调用用 `--trace`），适合被脚本消费。
- 会话仍然会写盘（`~/.zenagent/projects/<slug>-<hash8>/sessions/`，项目作用域见
  [项目作用域](projects.md)），除非你另有 HOME。
- 退出码：0 成功；1 运行期错误——缺 key、模型未知、provider 失败、`--json` 不带 `-p`、
  `-p` 与 `--list-sessions` 同用、`--cwd` 不存在，以及 REPL/headless 的 Ctrl+C；
  2 用法错误（argparse 未知参数/非法 choices）、缺 CLI extras、TUI 启动失败；
  `zen rpc` 的 Ctrl+C 是 130，RPC 启动期缺 key/模型未知是 2。

## 参数一览

| 参数 | 作用 |
|---|---|
| `-p/--prompt` | headless 单次运行 |
| `-m/--model` | 模型 id（catalog 或配置里的自定义模型） |
| `--thinking` | `off/low/medium/high/max` |
| `--mode` | `plan` / `ask` / `auto`（默认 ask；`--yes`≈auto，`--read-only`≈plan；`zen` 是旧别名） |
| `--yes` | 任何操作都不再确认（等价 `--mode auto`） |
| `--read-only` | 拒绝所有非只读工具（含未声明 `readOnlyHint` 的 MCP；等价 `--mode plan`） |
| `--allow-dangerous` | 允许 `rm -rf` 等危险命令（`--yes` 也不够） |
| `--max-turns` / `--max-cost` | 硬上限；超限终止并记录在 termination |
| `--retries` | provider 瞬时错误的额外重试次数（默认 0）。启用后每次尝试先缓冲，**关闭 token 流式**（文本在调用结束时出现） |
| `--resume [SESSION]` | 继续会话（缺省 `latest`，只续当前项目） |
| `--list-sessions` | 列出当前项目的会话（不能与 `-p` 同用） |
| `--all-projects` | 配合 `--list-sessions` 列出所有项目与 legacy |
| `--session`? | 无此参数；用 `--resume` 或 REPL 的 `/session` |
| `--cwd` | 运行前切换工作目录（工具相对路径基于它） |
| `--image` | 附加图片（可重复；5MB、magic-byte 判断、非 vision 模型提前报错） |
| `--trace` | 写 JSONL trace（turn/tool 序列、耗时） |
| `--metrics` | 每个 run 追加一行 JSON 指标 |
| `--no-auto-compact` | 关掉自动压缩 |
| `--no-context-files` | 不读 AGENTS.md |
| `--no-completion` | 关掉 REPL 补全 |
| `--no-mcp` / `--mcp-server NAME` / `--mcp-file PATH` | MCP 开关与过滤 |

环境变量补充：`ZENAGENT_SHELL=posix|powershell|pwsh|cmd|<绝对路径>` 选择 bash 工具的
执行 shell（默认 POSIX `/bin/sh -c`；Windows 下默认探测 pwsh→powershell→cmd，
旧行为是 cmd.exe，可用 `ZENAGENT_SHELL=cmd` 保持）。
| `--no-skills` / `--no-templates` | 不加载 skills / 模板 |
| `--extension PATH` | 加载扩展（可重复） |
| `--theme auto\|dark\|light` | 界面外观（TUI + REPL）；REPL 另接受 `default\|mono\|solarized\|<path.json>` legacy 主题 |
| `--lang auto\|en\|zh` | 界面语言（TUI + REPL），覆盖 `prefs.language`；见 [i18n](i18n.md) |
| `--json` | headless 输出 JSON |

## 参数值补全

REPL 里命令参数也能补全（`Tab`）：`/mode ` → `plan` / `ask` / `auto`，`/thinking ` →
`off` / `low` / `medium` / `high` / `max`，`/theme ` → `auto` / `dark` / `light`，`/lang ` → `auto` / `en` / `zh`，`/model ` → 当前 catalog
里的模型 id（带 ready / no key 标记）。值表在 `zenagent/app/slash_args.py`，TUI 与 REPL 共用。

## 运行模式（plan / ask / auto）

`--mode plan|ask|auto` 选择启动模式；显式 `--mode` 优先于 `--yes` / `--read-only`，
后两者只是兼容映射（`--yes` → auto，`--read-only` → plan），都不传则 `ask`。

- **plan**：只读。写/执行工具（含未声明只读的 MCP 工具）被拒绝；`web_search`/`fetch`
  与声明 `readOnlyHint` 的 MCP 工具可联网调研。system prompt 要求模型先调研并给出计划。
- **ask**：写/执行需要确认。REPL 在终端问 `[y/n/a]`；TUI 弹审批框（Allow / Allow for
  session / Deny，120 秒无响应视为 Deny）；headless 无交互，默认拒绝，需显式放行。
- **auto**：全部放行、不弹任何确认——破坏性命令（`git reset --hard`、`sudo …`）、凭证路径、
  workspace 外写入都直接执行，headless 同样放行；system prompt 会告诉模型直接执行、不要请示。
  仅灾难命令仍拒绝（`--allow-dangerous` 可再放行），`[tools] deny` 永远优先。
  风险分级详见[工具与权限](tools-permissions.md)。

会话中可随时用 `/mode` 切换，下一次工具调用立即生效；TUI 状态带会显示当前模式。

## 子命令

```bash
zen sessions list [--all]   # 列出当前项目会话（--all 含所有项目与 legacy）
zen sessions adopt PATH     # 把 legacy 会话归入当前项目
zen doctor                  # 环境自检：python、key、catalog、config、sessions、终端
zen doctor --report         # 脱敏诊断报告（--report-out PATH 写文件）
zen export [SESSION] --format md|html [-o PATH]
zen usage [--json] [--since YYYY-MM-DD]
zen rpc                     # JSONL RPC 服务
zen evals [--model ...] [--filter ...] [--report ...] [--baseline ...]
```

- `export` 缺省导出 latest，默认写会话旁边。
- `usage` 扫描当前项目与所有已知项目的会话目录（含 legacy 的 `~/.zenagent/sessions`），
  按模型聚合 tokens/cost/cache。
- `evals` 转发给 `scripts/eval.py`，支持 `--baseline` 回归对比（有回归 exit 1）。
- `rpc` 的 stdout 只有协议 JSON；日志去 stderr。

会话内的斜杠命令由 TUI 与 REPL **共用同一个 `CommandRegistry`**（`app/commands.py`）：
`/help` 列出全部；`/tools` 打印模型当前可调用的工具清单（含 MCP、skill 与扩展注册的工具）；
`/retry` 重跑上一轮（截断结果写入新会话文件）。TUI 把命令结果渲染成选择器/浮层，REPL 渲染成
文本（例如裸 `/sessions` 打印会话列表、裸 `/model` 打印当前模型）。`/usage` `/context`
`/export` `/plan-review` 在两端都可用。

TUI 与 REPL 的启动 flag 现在都真实生效：`--thinking`、`--trace`、`--metrics`、
`--no-auto-compact`、`--image` 由 `RuntimeOptions` 统一承载；REPL 也会转发
`--max-turns`/`--max-cost`/`--retries`。RPC 有对应的一组（thinking/trace/metrics/
no-auto-compact/no-templates/extension），但没有 `--theme`/`--image`——图片走
`prompt.images`（见 [RPC 与 SDK](rpc-sdk.md)）。headless `-p` 不走 `RuntimeOptions`
——它自己装配 Agent，这些 flag 由 `run_headless` 的参数直接承载。

## 环境变量

| 变量 | 作用 |
|---|---|
| `DEEPSEEK_API_KEY` / `ZAI_CODING_CN_API_KEY` / … | 由 catalog 的 `api_key_env` 决定 |
| `ZENAGENT_METRICS` | 等价于 `--metrics`（当前实现以 CLI 参数为准） |

## 源码对照

- 参数与分支：`zenagent/cli/main.py::build_parser`、`main`、`run_headless`
- 子命令实现：`zenagent/cli/{doctor,exportcmd,usagecmd,rpc}.py`
- 对应测试：`tests/test_cli.py`、`tests/test_doctor.py`、`tests/test_export.py`、`tests/test_usage.py`、`tests/test_rpc.py`
