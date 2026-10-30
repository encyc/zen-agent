# 项目与全局作用域

ZenAgent 区分两类数据（对齐 omp/pi 的做法）：

- **项目输入**：应该跟仓库走、可提交、可共享的配置与扩展 → `<项目根>/.zenagent/`
- **本机运行态**：历史、隐私、密钥、缓存 → `~/.zenagent/`

## 项目根怎么认

从 cwd 向上找最近的 `.git`（普通 checkout 是目录，worktree 是文件）；找不到 `.git` 时把 cwd 当作一个独立项目。`zen --cwd PATH` 先切换目录再解析。

项目 key 形如 `<目录名>-<sha256(项目根)[:8]>`，例如 `py-agent-3f2a9c1d`：可读、同名目录不冲突。

## 目录布局

```text
<项目根>/.zenagent/            # 可提交、可共享（不放密钥）
  config.toml                  # 项目配置，覆盖全局
  mcp.json                     # 项目 MCP 定义（secret 走 env/settings）
  skills/ commands/ AGENTS.md  # 项目 skills / prompt 模板 / 指令

~/.zenagent/                   # 本机状态与全局默认
  config.toml  settings.json   # 全局配置与密钥
  mcp.json  themes/ keybindings.json
  sessions/                    # legacy 扁平会话（只读保留）
  projects/<key>/
    meta.json                  # 项目根、名称、最后使用时间
    sessions/*.jsonl           # 会话 transcript
    sessions/.titles.json      # 会话显示名 sidecar
```

## 会话作用域

- 新会话写入**当前项目**的 `projects/<key>/sessions/`；
- `/sessions`、`zen --list-sessions`、`--resume latest`、`zen export latest` **默认只看当前项目**；
- 显式跨项目视图：`/sessions all`（REPL）、TUI 会话选择器里 `tab`（或 `t`）切换 all/project、`zen sessions list --all`、`zen --list-sessions --all-projects`；
- all 视图里每条会标注 `[项目名]`，旧扁平会话标注 `[legacy]`。

**legacy 会话**（升级前 `~/.zenagent/sessions/*.jsonl`）不迁移、不删除，只在 all 视图出现。想把某一条归到当前项目：

```bash
zen sessions adopt ~/.zenagent/sessions/20260101-000000.jsonl
```

迁移后文件名冲突会自动加 `-1/-2`，标题 sidecar 一并迁移。

## 项目配置与 MCP

- `load_user_config` 先读 `~/.zenagent/config.toml`，再读 `<项目根>/.zenagent/config.toml` 覆盖；`[tools].deny` 两层取并集（项目不能放宽全局拒绝），`tool_allow` 追加，provider 表按 id 合并；
- `load_mcp_config` 先读全局 `~/.zenagent/mcp.json`，再合并项目 `.zenagent/mcp.json`，同名 server 项目覆盖；
- **密钥永不进项目层**：凭据走环境变量或 `~/.zenagent/settings.json`；项目 MCP 里只写 `secret_ref` / 环境变量名。

## 跨项目恢复的边界

通过 all 视图恢复另一个项目的会话时，本期**不会**自动切换 cwd（omp 会切到那个项目目录）：会提示「跨项目会话，工具仍在当前 cwd」。要完整切项目，重新在该项目目录启动 `zen --resume PATH`。

## 源码对照

- 项目身份/布局：`zenagent/project.py`（`find_project_root`、`project_key`、`project_sessions_dir`、`all_session_dirs`）
- 会话作用域：`zenagent/agent/session.py`（`sessions_dir`、`list_sessions`、`latest_session`）
- 项目配置：`zenagent/config.py`、`zenagent/mcp/config.py`
- 对应测试：`tests/test_project.py`、`tests/test_session.py`、`tests/test_config.py`、`tests/test_project_scoping.py`
