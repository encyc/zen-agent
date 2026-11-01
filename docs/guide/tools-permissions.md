# 工具与权限

## 内置工具

| 工具 | 能力 | 默认权限 |
|---|---|---|
| `read_file` | 读文本；默认 400 行/次，可用 `offset`/`limit` 续读（硬上限 2000 行） | 只读，免确认 |
| `write_file` | 覆盖写文件；拒绝用"截断的 read 页"回写（防数据丢失） | 需确认 |
| `edit_file` | 唯一匹配替换（old→new）；`replace_all` 可替换全部匹配 | 需确认 |
| `list_dir` | 列目录（标记目录/文件） | 只读 |
| `bash` | 执行命令（非交互环境、`cwd`、`async` 后台作业），禁止危险模式 | 需确认 |
| `wait` | 等待 `bash async` 启动的后台作业并取回结果（只读、串行） | 只读 |
| `task` | 把自包含子任务交给只读子代理（独立上下文/转录与成本，深度 1）；父上下文只收 `<task-result>` envelope | 只读，免确认 |
| `ask` | 向用户发起结构化提问（1–5 题、每题 2–6 个选项，`header`/`multi`/`recommended` 可选）；只有当前端注册了交互 handler 时才弹对话框，否则降级为「按最保守选项继续」 | 只读，免确认 |
| `glob` | 按模式找文件；`limit`（默认 500）、`include_hidden`（.git 永远跳过） | 只读 |
| `grep` | 正则搜内容（忽略二进制/大文件）；按文件分页：每页 20 个文件 × 每文件 20 条，`skip` 续页 | 只读 |
| `web_search` | 联网搜索，返回标题/URL/摘要（DeepSeek → Tavily → Brave → Bing → DuckDuckGo） | 只读，免确认 |
| `fetch` | 抓取 URL 并转成可读文本（优先 article/main、去页面 chrome，12k 字符上限；可选 `timeout_s` 1–45s；`details.method` 报告抽取方式） | 只读，免确认 |
| `read_skill` | 读本地 SKILL.md 正文 | 只读 |
| `todo` | 会话级任务清单（phases / 按内容寻址 / 9 个 op）；状态写 `<session>.todo.json`，不碰工作区（plan/`--read-only` 下也会写这个会话 sidecar） | 只读 |

每个工具还声明一个能力级（`AgentTool.approval = "read" | "write" | "exec"`）：
权限门读声明决定「plan 是否放行 / 是否需要确认」，而不是维护一份硬编码工具名表；
没声明的工具按 `exec` 处理（最保守）。MCP 工具如果服务器声明了
`annotations.readOnlyHint`，也会被当成 `read`。

装配层还有两个声明位（内核只声明、`ToolRegistry` 决定是否暴露）：
`hidden` 的工具永不进 prompt / 模型可调用集；`default_inactive` 的工具默认不暴露，
可由前端或配置激活。热更新（`/reload`、MCP 后补连接、扩展变更）都走
`Runtime.reconcile()` / `reconcile_async()` 这一条路径，工具集合变化只重建一次 prompt。

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

**"always" 的记忆粒度是目标，不是工具**：文件类工具按解析后的路径记
（`write_file:/abs/x.py` 不会放行 `y.py`）；目录授权覆盖**该目录的直接子项一层**，
不覆盖更深层，也不跨工具泄漏。`bash` 按**完整命令**记（`git status` 不覆盖
`git status --short`、多行脚本或 `git status && …`；安全 > 少问一次）。没有自然目标的
工具才按工具名记；有自然目标但参数为空时不会退化成工具级放行。审批框会显示这次
"always" 实际授权的目标。

## 路径围栏与风险确认

写类工具的 `path` 先 `expanduser()`（`~`）再 `resolve()`（跟随符号链接），然后按
workspace 判定；workspace = 项目根（最近 `.git`，否则 cwd，与 project 规则一致）。

| 风险 | 触发 | 交互式 | headless |
|---|---|---|---|
| `outside` | 解析后不在 workspace 内 | 弹确认，带 `⚠ outside workspace` | 拒绝，提示 `--allow-outside PATH` |
| `protected` | 目标是 `~/.zenagent/**`、`~/.ssh`、`~/.aws`、`~/.config/gcloud`、`~/.kube`、`~/.docker/config.json`、`~/.git-credentials`、`~/.netrc`、`~/.npmrc`、`~/.pypirc` 或 `id_rsa*`/`id_dsa*`/`id_ecdsa*`/`id_ed25519*`/`*.p12`/`*.pfx` | 弹确认，带 `⚠ protected path` | 拒绝，提示 `--allow-protected` |
| `destructive` | bash 命中破坏性命令（见下节） | 弹确认，带 `⚠ destructive command` | 拒绝，提示 `--allow-destructive` |

- `/tmp` 与系统临时目录默认放行，避免临时文件也弹窗。
- **full / `--yes` 不会自动放行 confirm 级风险**：它们只表示"普通写/执行不用问"。
- `[tools] allow` 与工具级 always 不能绕过风险判定；能放行的只有：目标的会话级 always、
  `--allow-outside PATH` / `--allow-destructive` / `--allow-protected`、
  `[safety]` 全局配置里的对应项。
- 项目内 `.env` 等日常文件不受保护（改 `.env` 是正常开发）；受保护清单在 `zenagent/app/safety.py`，
  按调用时的 `HOME` 解析（便于测试与容器）。
- 配置放行项**只允许写在全局层**：项目层出现 `[safety]` 会启动即报错，避免仓库给自己开权限。
- bash 的破坏性识别是 **best-effort**：命令内先 `cd`、变量拼接、子 shell 无法可靠判断；
  真正的兜底要等部署层隔离。

## 快照与 /restore

每次成功的 `write_file` / `edit_file` 在写入前把旧内容存进**项目级**快照日志：

```
~/.zenagent/projects/<project-key>/backups/
  manifest.jsonl                  # 每条：run / turn / path / after sha256 / 是否跳过
  blobs/<sha256[:2]>/<sha256>     # 旧内容，按内容寻址去重；目录 0700、文件 0600
```

- 恢复单位是**一次 run**（一次用户 prompt 触发的整段执行，含中间多个工具轮次）；
  `/restore` 恢复最近一次，`/restore list` 列最近 10 次，`/restore <n>` 恢复列表第 n 次。
- 恢复前校验当前文件哈希等于 agent 写后的哈希：**用户之后手动改过的文件会跳过并报告**，
  不覆盖；没有 force 选项。
- 单文件超过 `[backup] max_file_bytes`（默认 2 MiB）时只记一条"跳过"，工具结果里会写明该文件不可恢复。
- bash 的 `rm`/`mv` 不在快照范围内；只有文件工具的写入有快照。
- `/undo` 只回退会话记录，`/restore` 只回退文件。完整回退一次操作：先 `/restore`，再 `/undo`。

## 权限判定顺序

`PermissionGate.check(tool_name, args, tool=None)`（`zenagent/app/permissions.py`，
`zenagent/cli/permissions.py` 是兼容导入）。`tool` 为解析后的 `AgentTool`
时，其 `approval` 声明优先；`tool=None`（直接按名字调用）时退回
`READ_ONLY_TOOLS` 名表。

`check()` 先调用 `assess()` 得到三档判定（`allow` / `confirm` / `refuse`）：

```
assess()（固定顺序）
灾难命令          ──> refuse（除非 --allow-dangerous）
deny 模式命中      ──> refuse
plan / --read-only ──> refuse（非只读工具）
破坏性 bash 命令   ──> confirm(destructive)
受保护路径写入     ──> confirm(protected)
workspace 外写入   ──> confirm(outside)
其余               ──> allow
```

`confirm` 的处理：

```
目标已在会话内被 always 放行 / CLI、全局配置显式放行？ ── 是 ──> 允许
   │否
交互式前端？ ── 是 ──> 确认（REPL 提示 / TUI 审批框，带风险标签）
   │否
拒绝（原因里点名需要的 flag）
```

`allow` 时继续旧流程：allow 模式 → 放行；只读工具 → 放行；目标已授权 → 放行；
full / `--yes` → 放行；交互式 → 确认；否则拒绝。

关键点：

1. **allowlist 救不了危险命令**：`allow = ["*"]` 也不能放行 `rm -rf /`。
2. `--read-only` 下，`write_file`、`bash` 等写/执行工具全拒；
   `read_file/grep/glob/list_dir/web_search/fetch/read_skill` 放行；
   MCP 里声明了 `readOnlyHint` 的工具也放行，其余 MCP 工具仍拒绝。
3. 拒绝不是异常：工具调用收到 `is_error=True` 的结果，模型能看到原因并调整。
4. 交互模式下 REPL 会问 `allow? [y/N]`；headless 没有交互，默认拒绝。
5. `full` / `--yes` 只会自动放行 `allow` 档；越界、凭据、破坏性命令仍走上面的 `confirm` 分支。

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
- 所有结果（含 MCP/扩展）在写回前统一过内核结果预算：单行 >2000 字符中间截断，
  文本 >40 KB 保留头尾并附截断提示；详见[工具内核](../internals/tools-internals.md)。

## 危险命令与破坏性命令

bash 命令分两档，都在权限门里判定：

**灾难级**（`dangerous_command_reason`）：根路径递归删除、fork bomb、`mkfs`、`dd` 写块设备、
`chmod -R 777 /`、关机/重启。默认直接拒绝，`--yes` 也不放行；只有显式 `--allow-dangerous` 放行。

**破坏性**（`destructive_command_reason`）：`git reset --hard`、`git clean -fdx`、
`git checkout -- .`、`git restore .`、`git stash drop|clear`、`git push --force`、`sudo …`、
`curl|wget … | sh`、解析后落在项目外/祖先目录的 `rm -rf`（`rm -rf ./build` 不触发）、
`find -delete` / `-exec rm`、`truncate -s 0 …`、`shred …`、`dd of=<文件>`。
交互式会弹确认（full 模式也一样）；非交互拒绝并提示 `--allow-destructive`
（`--allow-dangerous` 同样覆盖这一档）。

两条判定分别在 `zenagent/app/permissions.py` 与 `zenagent/app/safety.py`；命令解析是启发式，
不是 shell 隔离。

## 源码对照

- 工具实现：`zenagent/tools/{fs,bash,search,web}.py`，清单 `zenagent/tools/__init__.py::ALL_TOOLS`
- 权限：`zenagent/app/permissions.py::PermissionGate`（`assess`/`check`）、`PermissionBroker`、
  `zenagent/app/modes.py`（`resolve_mode`/`mode_prompt_note`）、`READ_ONLY_TOOLS`、`dangerous_command_reason`
- 风险模型：`zenagent/app/safety.py`（`GuardPolicy`、`Verdict`、`classify_write_path`、
  `destructive_command_reason`）
- 快照与恢复：`zenagent/app/journal.py`（`SnapshotJournal`）、`zenagent/app/restore.py`
  （`/restore` 命令核心）、`zenagent/project.py::project_backups_dir`
- 对应测试：`tests/test_safety.py`、`test_journal.py`、`test_restore.py`
- 校验：`zenagent/agent/validate.py::validate_arguments`
- 执行与错误隔离：`zenagent/agent/loop.py`（工具 round）
- 对应测试：`tests/test_tools_fs.py`、`test_tools_bash.py`、`test_tools_search.py`、`test_tools_web.py`、
  `test_permissions.py`、`test_modes.py`、`test_app_permissions.py`、`test_tui_approval.py`、`test_validate.py`
- `web_search` 的 provider 链与配置见 [`docs/internals/web-search.md`](../internals/web-search.md)：有 `DEEPSEEK_API_KEY` 时默认走 DeepSeek，无 key 时退到 Bing/DuckDuckGo；顺序可用 `ZENAGENT_SEARCH_PROVIDERS` 覆盖。
- `/search` 查看 provider 状态，`/search <query>` 手动跑一次。
- 搜索走的是"压空白 + 长度上限"，**不改写原文**（与 omp 一致）；剥 markdown 会吃掉代码里的 `# comment`。
