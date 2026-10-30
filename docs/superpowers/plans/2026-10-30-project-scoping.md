# 项目/全局作用域 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 引入 project 作用域：会话默认按项目隔离、显式 all 视图、legacy 保留；项目配置/MCP 层叠；统一的项目身份与目录布局。

**Architecture:** `zenagent/project.py`（纯 stdlib）定义项目根（最近 `.git`，否则 cwd）、key（`<basename>-<sha8>`）、`~/.zenagent/projects/<key>/` 布局与项目内 `.zenagent/` 路径。`agent/session.py` 的会话默认落到当前项目；`list_sessions(scope="project"|"all")` 提供显式跨项目视图（legacy 只在 all）。`config.py` / `mcp/config.py` 增加 global→project 层叠。CLI/REPL/TUI 默认当前项目，`all` 显式开启。

**Tech Stack:** Python 3.11+、tomllib、pytest；内核仍只依赖 httpx。

**Spec:** 用户 2026-10-30 会话确认：「按照 omp 和你推荐的」——项目根=最近 `.git`；key=`basename-sha8`；默认仅当前项目；项目配置覆盖、deny 并集、密钥不进项目层；legacy 保留 unscoped + 显式 adopt。

## Global Constraints

- 内核（types/events/providers/agent/tools）不得 import cli/app；项目路径模块只能依赖 stdlib。
- 不改 JSONL 格式；titles 仍走 sidecar（现在每个 sessions 目录一份）。
- 旧会话不迁移、不删除：legacy 目录只读出现在 all 视图；`zen sessions adopt` 才移动。
- 项目层配置只允许可共享键；密钥/账号永不进项目层。
- 所有新逻辑离线单测；`bash scripts/check.sh` 全绿才算完成。

## Review Focus

1. 项目根判定：嵌套目录、worktree（`.git` 是文件）、非 git 目录、`--cwd` 切换后都要稳定。
2. 会话默认视图不能泄漏其他项目/legacy；all 视图必须可区分来源。
3. 项目配置合并：deny 并集不可被项目放宽；相对路径按各自配置文件解析；坏配置给清晰错误。
4. legacy 会话 adopt 的移动/标题迁移必须无损，重复 adopt 幂等报错。
5. 跨项目恢复（all 视图）不能静默把工具 cwd 搞错——本期先显式提示，不自动切 cwd。

---

### Task 1: 项目身份（已完成）

`zenagent/project.py` + `tests/test_project.py`：根发现、key、布局、meta、all_session_dirs、session 归属分类。

### Task 2: 会话存储按项目隔离（teammate: storage-core）

**Files:** `zenagent/agent/session.py`、`zenagent/app/sessions.py`、`tests/test_session.py`、`tests/test_app_sessions.py`

**Interfaces（不得改名）:**
- `sessions_dir() -> Path`：当前项目的会话目录（`project.project_sessions_dir()`，不在函数内创建）。
- `all_session_dirs() -> list[Path]`：legacy + 所有项目 sessions 目录。
- `SessionInfo.project: str = ""`（项目 key；legacy 为空）。
- `list_sessions(limit=20, *, scope="project")`：`scope="all"` 时合并 legacy+projects，按目录读各自的 `.titles.json`，按 mtime 倒序，带 project 标签。
- `latest_session(*, scope="project")`。
- `read_session_titles(directory=None)`、`set_session_title(path, title)`、`session_title(path)` 全部按「会话文件所在目录」读写 sidecar。
- `Session.read_info(*, titles=None)`：titles 未传时读自身目录。
- `SessionManager.entries(scope="project")`、`SessionEntry.project`。
- `SessionManager.adopt(path) -> Path`：把 legacy 文件移入当前项目 sessions（重名加后缀），迁移标题；非 legacy 或目标同项目 raise ValueError。
- 创建新会话/切换时 best-effort `project.write_project_meta()`。

### Task 3: 项目配置/MCP 层叠（teammate: config-layer）

**Files:** `zenagent/config.py`、`zenagent/mcp/config.py`、`tests/test_config.py`、`tests/test_mcp_client.py`、`docs/guide/config.md`、`docs/guide/mcp.md`

**Interfaces:**
- `load_user_config(path=None, *, cwd=None)`：path 显式时只读该文件（兼容测试）；默认 global `~/.zenagent/config.toml` → project `<root>/.zenagent/config.toml` 覆盖。
- 合并：default_model 项目覆盖；provider_overrides/custom_providers 按 key 合并、项目胜；extensions 追加；tool_allow 追加；tool_deny 并集（保持顺序去重）。
- `load_mcp_config(path=None, *, cwd=None)`：path 显式时只读该文件；默认 global `~/.zenagent/mcp.json` → project `<root>/.zenagent/mcp.json`，同名 server 项目覆盖。
- 项目层不得引入密钥字段；文档写清 secret 走 env/settings。

### Task 4: 前端与 CLI（Lead）

- TUI SessionPicker：默认 `scope=project`，`tab` 切换 all/project（omp 语义），all 视图行内标注项目；`/sessions all`。
- REPL `/sessions [all]`：默认当前项目，all 带项目标签。
- CLI：`--list-sessions` 默认项目 + `--all-projects`；`zen sessions list [--all]` / `zen sessions adopt PATH`。
- `usage` 扫描所有目录；`doctor` 报当前项目目录 + legacy；`export latest` 默认项目；`--resume latest` 默认项目。
- 技能/模板/AGENTS.md 发现以项目根为准（不再只看 cwd）；Runtime 创建会话时 touch meta、`resume` 跨项目仅提示不切 cwd（本期）。

### Task 5: 文档与闸门（Lead）

- `docs/guide/projects.md` 新增概念说明；sessions/config/mcp/tui/cli/README/feature-map 同步。
- `bash scripts/check.sh` 全绿；审查 teammate diff；提交。

## Self-Review

1. **Spec 覆盖**：布局/默认视图/legacy/配置层叠/CLI 均有任务；跨项目切 cwd 明确列为后续。
2. **接口一致**：Task 2/3 的签名被 Task 4 消费；key 由 Task 1 唯一提供。
3. **Review Focus→测试**：#1 project tests；#2 session scope tests；#3 config merge tests；#4 adopt tests；#5 前端仅提示（不做自动切换）。
