# 手滑防护（第一层）设计

日期：2026-11-01
状态：待用户批准
范围：威胁模型 #1「防模型手滑写坏本机」。#2「恶意仓库 / prompt injection」不在本设计内（用户另行调研）。

## 意图与动机

上一轮讨论把 coding agent 的安全问题拆成两种失败：

1. **手滑**：模型没有恶意，但会算错路径、拼错 `rm`、覆盖未提交的改动。防御靠**确认 + 可回滚**，同进程策略够用。
2. **恶意内容 / prompt injection**：模型是被利用的代理人，攻击者控制它读到的内容。防御必须**移除能力**（文件、凭据、网络边界），不在本设计内。

现在的问题是：zenagent 的门只做了「要不要问」，没做「问什么值得警惕」和「出事了能不能恢复」。具体缺口：

- `write_file` / `edit_file` 用进程 cwd 解析路径，绝对路径任意可达、父目录自动创建；没有任何 workspace 限制。
- `/undo`（`app/sessions.py::SessionManager.undo`）只回退会话 transcript，**不恢复文件**。
- `dangerous_command_reason` 只拦「灾难级」；`git reset --hard`、`git clean -fdx`、`git push --force` 全部放行，文档声称拦截的 `curl … | sh` **代码里其实没实现**（文档/代码不一致，顺手修）。
- 全模式的承诺是「不问」，所以一旦用户图省事开了 full，手滑就没有最后一道拦截。
- 文件工具对 `~` 的处理与权限门不一致：`write_file` 用 `Path(raw)` 写字面 `~` 目录，`tool_target` 却 `expanduser()`——审批展示的路径和实际落盘路径可能不是同一个（现存 bug，本设计顺手修）。

## 目标

- 写类工具默认限制在 workspace 内；越界、受保护路径、破坏性 shell 命令都有明确的差异化处理。
- 每一次成功的文件写入都可回滚，且恢复不会覆盖用户之后的手动修改。
- 策略在 TUI / REPL / headless 三个有会话的前端一致；RPC 只继承路径围栏（见「前端覆盖」）。
- 保持内核契约不变：没有权限概念进内核，全部在 harness 层（`app/`）。

## 非目标

- OS 级隔离（seatbelt/bwrap/容器）、网络出口控制、凭据不进进程环境——属于 #2，后续单独设计。
- 读路径的围栏（`read_file`/`grep`/`glob` 仍可读任何位置）；读敏感文件不弹确认（读 `.env` 调试是合法行为）。
- bash 的写入围栏：shell 不可静态解析，本层只做**破坏性命令识别**（best-effort），真正兜底要等 #2 的隔离方案。
- Windows 支持；现有平台约定不变（macOS 主力，Linux 可用）。
- 文件权限位、ACL、xattr 的恢复（只恢复内容）。
- 项目层配置给自己开权限的问题（`[tools] allow` 可被项目层追加，这是 #2 的信任模型议题，本设计只保证新开关不允许项目层设置）。

## 反目标

- 引入「安全剧场」：新机制不能只是把英文警告加长；每一条判定都要有测试，且能在 `--yes`/full 下说清语义。
- 让日常开发变慢：项目内普通写、`rm -rf ./build`、`git status` 不许多弹一次确认。
- 把备份做成第二套会话存储：按项目滚动一份写入日志，不做通用版本库。
- 恢复操作默认覆盖用户之后的手动修改；宁可跳过并报告，也不静默回滚。

## 已考虑的方案

**方案 A：只做围栏策略（无状态）**——路径限制、受保护路径、破坏性命令识别，不加回滚。
优点：改动集中、无新存储。缺点：只降低概率，不兜底；误删/覆盖一旦发生仍不可逆。否决：用户明确要「防手滑」，恢复才是关键。

**方案 B（选定）：围栏 + 文件级回滚**——A 之上加快照日志（`/restore`），TUI 确认框展示风险与 diff。
优点：手滑从「防不住」变成「可恢复」，与 omp/Claude Code 的 checkpoint/rewind 方向一致；全部落在 harness 层。缺点：存储与轮转策略、bash 的 `rm/mv` 仍不可回滚。

**方案 C：B + bash 子进程限制**（`sandbox-exec`/`bwrap`）——唯一能真正堵住 bash 的方案，但平台特定、兼容性风险大，且属于 #2 的隔离技术。推迟，等 #2 调研结论一起设计。

## 关键决策

用户已定：做第一层；选 B；workspace 语义沿用现有 project 规则。

我的裁定（可推翻）：

1. **workspace 根 = project root**（`project.find_project_root(cwd)`，最近 `.git`，否则 cwd），与刚确定的 project 规则一致。`--cwd` 先切目录再解析。
2. **风险判定是独立层**：新增 `Verdict`（allow / confirm / refuse）。full 与 `--yes` **不能**自动放行 confirm 级风险——这是有意的行为变更，CHANGELOG 要写。
3. **`[tools] allow` 与工具级 always 授权不能绕过风险判定**；只有「针对该次目标」的显式用户授权（对话框里的 always）或 CLI/config 的显式放行才算数。
4. **受保护路径**覆盖整个 `~/.zenagent/`（config/settings/mcp/sessions/backups——能改这里等于能改安全策略本身）加家目录凭据存储与私钥模式；项目里的 `.env` 不算——改 `.env` 是日常开发行为。项目内的 `.zenagent/` 不算受保护（那是可提交的项目配置，允许正常编辑）。
5. **`/tmp` 与系统临时目录默认在 allow_outside 里**，避免 full 模式下每个临时文件都弹一次。
6. **备份是项目级、不是会话级**（见下）：`/undo`、`/compact`、`/fork` 都会换会话文件，会话级备份会跟着丢，而恰好这些操作之后用户最想回滚。项目级日志天然存活。
7. **备份 best-effort 但可见**：超过大小上限时跳过并在工具结果里写明，不阻塞写入。
8. **恢复不覆盖用户后续修改**：当前内容与 agent 写后哈希不一致就跳过并报告，不提供 force。
9. **`~` 路径统一**：`read_file`/`write_file`/`edit_file`/`list_dir` 允许并展开 `~`（工具侧 `expanduser()`），策略、备份、审批预览都用同一解析结果。
10. **RPC 只继承围栏、不做备份**：`cli/rpc.py` 没有会话（`session_path=None`），也没有 `/restore` 面；嵌入方自己承担恢复语义。

## 设计

### 1. 风险判定模型

新增 `zenagent/app/safety.py`（只依赖 stdlib 与 `zenagent.project`）：

```python
PathRisk = Literal["outside", "protected", None]
RiskKind = Literal["outside", "protected", "destructive"]

@dataclass(frozen=True)
class GuardPolicy:
    workspace: Path                    # 解析后的 project root
    allow_outside: tuple[Path, ...]    # CLI --allow-outside + [safety] allow_outside + 临时目录
    allow_protected: bool              # CLI --allow-protected + [safety] allow_protected
    allow_destructive: bool            # CLI --allow-destructive + [safety] allow_destructive

@dataclass
class Verdict:
    kind: Literal["allow", "confirm", "refuse"]
    reason: str                        # 给模型/用户看的一句话（英文，沿用现有门文案风格）
    risk: RiskKind | None = None       # confirm 时用于 UI 标签
    target: str | None = None          # 解析后的路径或命令摘要
```

`assess(tool_name, args, tool, policy, *, deny, mode, read_only) -> Verdict` 的固定顺序：

```
灾难命令（现有）       拒绝（--allow-dangerous 才放行）
[tools] deny 命中      拒绝
plan / --read-only     拒绝（非只读工具）
破坏性 bash 命令       confirm(risk=destructive)   ← 新增
受保护路径写入         confirm(risk=protected)     ← 新增
workspace 外写入       confirm(risk=outside)       ← 新增
其余                   allow
```

只有 `write_file` / `edit_file` 参与路径判定；`bash` 只参与破坏性命令判定。读类工具不受影响。

**放行规则**：`confirm` 级风险不经过 `[tools] allow`、工具级 always、`--yes`、mode=full。能被放行的只有三种：

- 目标被 `allow_outside` / `allow_protected` / `allow_destructive` 覆盖（CLI 或全局配置）；
- 交互式前端里用户对**该目标**选了 always（会话级、target 精确）；
- 非交互前端：拒绝，原因里点名需要的 flag。

**模式矩阵**（新增行为加粗）：

| 判定 | plan / --read-only | ask（交互） | full（交互） | headless `--yes` | headless 无 `--yes` |
|---|---|---|---|---|---|
| allow，只读工具 | 放行 | 放行 | 放行 | 放行 | 放行 |
| allow，写/执行 | 拒绝 | 确认（现状） | 放行（现状） | 放行（现状） | 拒绝（现状） |
| confirm 风险 | 拒绝 | 确认 + 风险标签 | **确认 + 风险标签** | **拒绝 + 提示 flag** | 拒绝 |
| refuse（灾难/deny） | 拒绝 | 拒绝 | 拒绝 | 拒绝 | 拒绝 |

### 2. 路径策略

- `classify_write_path(path, policy)`：`expanduser()` + `resolve()`（跟符号链接，防 symlink 逃逸）后判断：
  - 受保护路径优先（即使也在 workspace 内）：`~/.zenagent/` 整目录 + 家目录凭据存储 + 私钥模式；
  - 在 workspace 下（或等于）→ inside；
  - 在某个 `allow_outside` 条目下（或等于）→ inside；
  - 否则 outside。
- 受保护清单（代码常量，便于审查与扩展）：
  - 目录：`~/.zenagent`（整棵）、`~/.ssh`、`~/.aws`、`~/.azure`、`~/.config/gcloud`、`~/.kube`；
  - 文件：`~/.docker/config.json`、`~/.git-credentials`、`~/.netrc`、`~/.npmrc`、`~/.pypirc`；
  - 文件名模式：`id_rsa*`、`id_dsa*`、`id_ecdsa*`、`id_ed25519*`、`*.p12`、`*.pfx`。
- 默认 `allow_outside` 追加：系统临时目录（`tempfile.gettempdir()` 与 `/tmp`，两者都 resolve）。
- `--allow-outside PATH` 可重复；目录条目覆盖其下所有路径，文件条目只覆盖自身。配置条目相对路径按配置文件所在目录解析（与 extensions 现有约定一致）。
- 工具侧统一：`tools/fs.py` 的四个路径工具改用 `Path(raw).expanduser()`；策略/备份/审批预览接受同一解析结果，消除 `~` 的判定-落盘不一致。

### 3. bash 破坏性命令层

新增 `destructive_command_reason(command, policy) -> str | None`，与现有 `dangerous_command_reason` 并列（灾难级仍然直接拒绝、`--allow-dangerous` 才放行；破坏性级是可确认的）。

先修缺口，再加入下列模式（大小写/空白归一后匹配；每条都要有正反测试）：

- 下载即执行：`curl|wget … | sh|bash|zsh|python*`（现有文档声称拦截但未实现）；
- `git reset --hard`、`git clean -f/-fd/-fdx`、`git checkout -- .`、`git restore .`、`git stash drop|clear`、`git push --force`（不含 `--force-with-lease`）；
- `sudo …` 任意命令（提权本身就是高后果动作）；
- `rm -rf`：字面目标（`~…`、`/…`、相对路径）**按进程 cwd 解析**后，若目标等于 workspace、是 workspace 的祖先、或在 allow_outside 之外 → 破坏性。`rm -rf ./build`、`rm -rf node_modules`、`rm -rf .` 中前两个安静、`.`（等于 cwd）触发；
- `find … -delete`、`find … -exec rm …`；
- `truncate -s 0 …`、`shred …`、`dd … of=<非设备路径>`。

边界：命令内先 `cd`、变量拼接、子 shell 等无法可靠判断，**明确是 best-effort**，文档写清。

### 4. 快照与恢复（`zenagent/app/journal.py`）

**落盘布局**（项目级；`project.py` 增加 `project_backups_dir()`）：

```
~/.zenagent/projects/<project-key>/backups/
  manifest.jsonl               # 追加写；目录 0700、文件 0600
  blobs/<sha256[:2]>/<sha256>  # 旧内容，内容寻址去重
```

**manifest 每条**：

```json
{"run": "9f2c…", "turn": 2, "seq": 17, "session": "20261101-031500",
 "tool": "edit_file", "path": "/abs/file.py", "kind": "modified|created",
 "blob": "<sha256>|null", "before_bytes": 1234,
 "after_sha256": "<sha256>", "skipped": null, "time": "…"}
```

`run` 是一次 `agent_start` 的 uuid；`turn` 从 1 递增；`seq` 是 manifest 全局序号（恢复分组与排序都靠它）。读取时跳过坏行。

**捕获/提交**（由各前端组合进 Agent 钩子，内核零改动）：

- `before_tool_call`：先跑权限门（含确认）；通过后，若工具是 `write_file`/`edit_file`，把**当前内容**（或「不存在」）暂存到内存 pending（按 call id）。超过 `max_file_bytes`（默认 2 MiB）只记 `skipped="too_large"`，不读内容。
- `after_tool_call`：结果 `is_error=True` 丢弃 pending；成功则补 `after_sha256`（写后文件哈希）、落 blob、追加 manifest。`skipped` 时在工具结果 content 末尾追加一行说明（模型和用户都看得见）。
- 并发：pending/manifest 用 `asyncio.Lock` 串行；blob 先写 tmp 再 `os.replace`。
- 任何日志失败都不许把 run 弄炸：捕获异常，降级为「本次备份不可用」并写进工具结果。

**恢复语义**：

- 默认恢复**最近一次 run**（项目内跨会话的最新区块；输出里带 session 标签，list 里可见）。
- 对每个被改的路径取该 run 内**最早**的快照作为目标状态（run 前状态）；校验当前内容哈希等于该路径**最后一次写入**的 `after_sha256`——不一致（用户后来手动改过）就跳过并报告。`created` 的文件在哈希一致时删除。
- 不提供 force；跳过项在报告里列出，让用户自己用 git 处理。
- 轮转：journal 初始化时按 `keep_runs`（默认 50）裁剪最老的 run，重写 manifest 并删除无引用 blob。

**命令**（TUI 与 REPL 共用核心逻辑，见「前端覆盖」）：

- `/restore`：恢复最近一次 run，输出 restored / skipped 清单；
- `/restore list`：最近 10 个 run（run、时间、session、文件数、前几个路径）；
- `/restore <n>`：恢复列表里第 n 个 run（1 = 最近）；
- run 进行中拒绝；`/undo` 语义不变（只动 transcript），文档说明两者配合构成完整回退（顺序：先 `/restore` 再 `/undo`）。

### 5. 前端覆盖（Builder Check 追加）

现有前端各自建 `Agent`，不是都走 `Runtime.bootstrap`。明确接线点：

| 前端 | 入口 | 权限门 | journal |
|---|---|---|---|
| TUI | `Runtime.bootstrap`（`cli/main.py`） | `PermissionBroker.wrap(PermissionGate)` | ✅ 由 bootstrap 创建 |
| REPL | `cli/repl.py::repl`（自己建 Agent） | `PermissionGate(interactive=True)` | ✅ 建 session 后创建 |
| headless | `cli/main.py::run_headless` → `_build_agent` seam | `PermissionGate(interactive=False)` | ✅ 建 session 后创建 |
| RPC | `cli/rpc.py` | `PermissionGate`（路径围栏生效） | ❌ 无会话/无 `/restore`，明文豁免 |

共享装配（`app/journal.py`）：

```python
def load_backup_policy(config: UserConfig) -> BackupPolicy
def journal_for(session_path: Path | None, *, project_root: Path | None = None) -> SnapshotJournal | None
def attach_journal(agent, gate, journal) -> None
    # before = compose(gate.before_tool_call, journal.before_tool_call)
    # agent.after_tool_call = journal.after_tool_call
    # agent.subscribe(journal.on_event)   # agent_start/turn_start 推进 run/turn
```

`_build_agent` seam 增加 `before_tool_call` / `after_tool_call` 参数（测试注入 fake stream 的路径不变）。`/restore` 的核心（参数解析 + 调 journal + 格式化 + i18n）放 `zenagent/app/restore.py`；TUI 注册到 `app/commands.py`，REPL 在 `handle_slash` 的 if/elif 里加一个分支（REPL 没有共享命令注册表，这是现状，不为此重构整个 REPL）。

### 6. CLI 与配置

```
--allow-outside PATH     # 可重复；目录覆盖其下，文件只覆盖自身
--allow-destructive      # 放行破坏性 bash 级
--allow-protected        # 放行受保护路径写入
```

```toml
[safety]                 # 仅全局层有效；项目层出现即报错（避免仓库给自己开权限）
allow_outside = ["~/scratch", "/tmp/work"]
allow_destructive = false
allow_protected = false

[backup]
enabled = true
max_file_bytes = 2097152
keep_runs = 50
```

- 合并：全局配置 → CLI flag 取并集（flag 只能加不能减）；项目层禁止 `[safety]`/`[backup]`，解析时报错并提示放进全局配置。
- 装配：新增 `build_permission_gate(...)` helper（app 层），TUI/REPL/headless/RPC 都走它，避免四处复制策略构造；`RuntimeOptions` 增加对应字段。
- **受影响的前端文件**：`cli/main.py`（flags、TUI broker、headless）、`cli/repl.py`（gate + journal + `/restore` 分支）、`cli/rpc.py`（用 helper 建 gate，不建 journal）。

### 7. 影响面

新增：`app/safety.py`、`app/journal.py`、`app/restore.py`、`tests/test_safety.py`、`tests/test_journal.py`、`tests/test_restore.py`、本设计。

修改：`app/permissions.py`（Verdict/风险评估/ask 标签/broker）、`app/runtime.py`（policy、journal、钩子组合、`runtime.journal`）、`app/commands.py`（`/restore`）、`tools/fs.py`（`~` 展开）、`project.py`（backups 目录 helper）、`config.py`（`[safety]`/`[backup]` 解析与项目层限制）、`cli/main.py`（flags 与装配）、`cli/repl.py`、`cli/rpc.py`（gate helper）、`cli/tui/app.py`、`cli/tui/screens/modals.py`、`i18n/en.py`、`i18n/zh.py`、`tests/test_permissions.py`、`tests/test_app_permissions.py`、`tests/test_config.py`、`tests/test_tui_approval.py`、`tests/test_tools_fs.py`、文档四篇（tools-permissions/config/guardrails/feature-map）、`CHANGELOG.md`、`README.md`。

不改：内核（`agent/`、`types`、`providers`）零改动（只使用既有 `after_tool_call` 钩子）；`write_file`/`edit_file` 除 `~` 展开外逻辑不动。

## 兼容性与行为变更

- full / `--yes` 不再自动放行 confirm 风险：可能出现新的确认框（交互）或拒绝（非交互）。写进 CHANGELOG。
- headless 从本版本起会写备份（磁盘占用）；`[backup] enabled = false` 可关闭。
- 新命令 `/restore`；`/undo` 不变；REPL 与 TUI 都可用。
- 文件工具的 `~` 由字面路径改为展开（修 bug；原来会写到 `./~/...`）。
- 无数据迁移；旧配置不受影响（新 key 都是可选的）。

## 测试策略

全部离线（`tmp_path`、`monkeypatch` HOME/cwd、fake stream_fn）：

- safety：路径分类矩阵（workspace 内/外、allow 目录/文件、symlink 逃逸、`..`、`~`、临时目录）；受保护清单正反例（含 `~/.zenagent` 整目录与项目内 `.zenagent/` 的反例）；破坏性命令正反表（既有「正常命令」用例必须仍然正常）。
- 门/前端矩阵：plan/ask/full × interactive/headless × `--yes` × allow flags；`[tools] allow` 不能绕过 confirm 风险；always 授权按 target 生效。
- journal：成功提交、失败丢弃、created/modified、超限跳过、哈希不一致跳过恢复、并发写同一文件、轮转清理、manifest 追加与坏行跳过、目录/文件权限位、项目级跨会话恢复。
- 命令：`/restore` 三种用法、运行中拒绝、REPL 分支与 TUI 注册结果一致。
- 前端装配：TUI（bootstrap）、REPL、headless 各一条最小用例确认 journal 生效；RPC 确认不落备份但仍受围栏约束。
- i18n：catalog key/placeholder 一致性测试保持通过。
- 门禁：`bash scripts/check.sh` 全绿。

## 验收标准

1. 在仓库内 `zen -p "..."`（无 `--yes`）写项目内文件：与现状一致（拒绝，因为非交互）。
2. `zen --yes -p` 写 `/tmp/x`：放行（默认允许临时目录）；写 `~/scratch/x`：拒绝并提示 `--allow-outside`。
3. TUI full 模式写 workspace 外：弹确认框，带 outside 标签；写 `~/.ssh/config`：弹 protected 标签；写 `~/.zenagent/config.toml`：protected。
4. `git reset --hard` 在 TUI full 下弹 destructive 确认；headless `--yes` 下拒绝并提示 `--allow-destructive`；`curl … | sh` 被识别为破坏性；`rm -rf ../sibling` 触发、`rm -rf ./build` 不触发。
5. 写坏文件后 `/restore` 恢复原内容；用户随后手动改过的文件被跳过并报告；`/undo` 换会话文件后 `/restore` 仍能看到之前的 run。
6. REPL 里 `/restore`、`/restore list` 可用；`zen -p` 写入的 run 在之后打开的 REPL 里可恢复。
7. `bash scripts/check.sh` 通过。

## 交给实现者的决定

- 模块内函数拆分与私有 helper 命名；diff 预览的截断/着色细节。
- `Verdict` 是否用 Enum（不改变对外行为即可）。
- 破坏性命令正则的具体组织（表驱动 vs 逐条）；新增模式的测试数据组织。
- journal 文件 I/O 是否用 `asyncio.to_thread`（文件 ≤ 2 MiB，同步也可接受）。
- `/restore list` 的排版与条数上限。
- `attach_journal` 里 gate 返回 `{"block": ...}` 与 journal 捕获的先后（建议：block 优先，绝不备份被拒绝的调用）。

## 风险与后续

- 破坏性命令识别一定有漏报（变量、cd、子 shell），文档与报错文案必须诚实；真正的兜底是 #2 的隔离。
- full 模式下新增确认框可能让老用户意外：CHANGELOG 明示，`/mode` 帮助里补一句。
- 备份可能包含工作区里的敏感内容（如 `.env`）：目录 0700/文件 0600、限制轮转数量；不加密（与 git 同级的本地信任模型）。
- 项目级备份是跨会话共享的：`/restore` 默认恢复项目内最近一次 run，可能来自另一个会话——输出里标注 session，list 可见；这是为让 `/undo`、`/compact`、`/fork` 之后仍可回滚而做的取舍。
- 后续候选（不在本设计）：读敏感文件确认、`/restore` 与 `/undo` 合并成 `/rewind`、bash 子进程隔离（方案 C，随 #2 一起评估）。
