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

- 默认拒绝需要确认的工具（写文件、bash）。headless 下没有交互，所以要么 `--yes`，要么用
  `[tools] allow` 放行，要么 `--read-only` 只读。
- `--json` 时 stdout 只有一段 JSON（文本、工具调用、用量、termination），适合被脚本消费。
- 会话仍然会写盘（`~/.zenagent/sessions/`），除非你另有 HOME。
- 退出码：0 成功；1 用法/参数错误；2 缺少 key 或模型未知；130 Ctrl+C。

## 参数一览

| 参数 | 作用 |
|---|---|
| `-p/--prompt` | headless 单次运行 |
| `-m/--model` | 模型 id（catalog 或配置里的自定义模型） |
| `--thinking` | `off/low/medium/high/max` |
| `--yes` | 自动批准需要确认的工具 |
| `--read-only` | 拒绝所有非只读工具（含 MCP） |
| `--allow-dangerous` | 允许 `rm -rf` 等危险命令（`--yes` 也不够） |
| `--max-turns` / `--max-cost` | 硬上限；超限终止并记录在 termination |
| `--retries` | provider 瞬时错误的额外重试次数（默认 0） |
| `--resume [SESSION]` | 继续会话（缺省 `latest`） |
| `--list-sessions` | 列出会话 |
| `--session`? | 无此参数；用 `--resume` 或 REPL 的 `/session` |
| `--cwd` | 运行前切换工作目录（工具相对路径基于它） |
| `--image` | 附加图片（可重复；5MB、magic-byte 判断、非 vision 模型提前报错） |
| `--trace` | 写 JSONL trace（turn/tool 序列、耗时） |
| `--metrics` | 每个 run 追加一行 JSON 指标 |
| `--no-auto-compact` | 关掉自动压缩 |
| `--no-context-files` | 不读 AGENTS.md |
| `--no-completion` | 关掉 REPL 补全 |
| `--no-mcp` / `--mcp-server NAME` / `--mcp-file PATH` | MCP 开关与过滤 |
| `--no-skills` / `--no-templates` | 不加载 skills / 模板 |
| `--extension PATH` | 加载扩展（可重复） |
| `--theme NAME` | REPL 主题 |
| `--json` | headless 输出 JSON |

## 子命令

```bash
zen doctor                  # 环境自检：python、key、catalog、config、sessions、终端
zen doctor --report         # 脱敏诊断报告（--report-out PATH 写文件）
zen export [SESSION] --format md|html [-o PATH]
zen usage [--json] [--since YYYY-MM-DD]
zen rpc                     # JSONL RPC 服务
zen evals [--model ...] [--filter ...] [--report ...] [--baseline ...]
```

- `export` 缺省导出 latest，默认写会话旁边。
- `usage` 扫描 `~/.zenagent/sessions`，按模型聚合 tokens/cost/cache。
- `evals` 转发给 `scripts/eval.py`，支持 `--baseline` 回归对比（有回归 exit 1）。
- `rpc` 的 stdout 只有协议 JSON；日志去 stderr。

## 环境变量

| 变量 | 作用 |
|---|---|
| `DEEPSEEK_API_KEY` / `ZAI_CODING_CN_API_KEY` / … | 由 catalog 的 `api_key_env` 决定 |
| `ZENAGENT_METRICS` | 等价于 `--metrics`（当前实现以 CLI 参数为准） |

## 源码对照

- 参数与分支：`zenagent/cli/main.py::build_parser`、`main`、`run_headless`
- 子命令实现：`zenagent/cli/{doctor,exportcmd,usagecmd,rpc}.py`
- 对应测试：`tests/test_cli.py`、`tests/test_doctor.py`、`tests/test_export.py`、`tests/test_usage.py`、`tests/test_rpc.py`
