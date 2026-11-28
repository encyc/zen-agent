# Session 归档（archive）设计

日期：2026-11-28
状态：已批准（对话中确认方案 A）
规模：小特性——内核 sidecar + 列表过滤 1 处 + agent 工具 3 处传参 + RPC op/字段 + 设置页归档面板 + sidebar 菜单项 + 双语 i18n + 测试。

## 意图与为什么

一个 project 用久了 sidebar 里堆满过时的 session：翻找成本高，但删掉又怕丢上下文。
给 session 加一个**归档**操作：归档后 JSONL 文件原样保留（路径不变），只是从 sidebar
消失；在 **Settings → Projects** 里按项目查看已归档 session 并一键**恢复**。
归档的 session 对 agent 的会话检索工具（`list_sessions` / `search_sessions` /
`read_session`）**依然可见**——归档是"收进抽屉"，不是"藏起来"。

## 方案取舍

| 方案 | 机制 | 取舍 |
|---|---|---|
| **A. `.archive.json` sidecar（选定）** | 与 pins/titles 同构：每个 sessions 目录旁一个 sidecar，`文件名 → 归档时间戳` | 文件不动、路径不变，恢复=删一行；lease/live 行/fork 携带/`_index.json` 全部不受影响；改动集中在 `list_sessions` 一个过滤参数 |
| B. 移入 `sessions/.archive/` 子目录 | 删除的 trash 模式复用 | 隐藏"免费"，但改路径：打开中的会话写错位、标题 sidecar 需迁移、恢复要处理重名；复杂度全在边缘，无对应收益 |

B 被否：archive 是可逆操作，不该承担移动文件的全部副作用；trash 模式的本质是
"销毁前的缓冲"，与归档语义不同。

## 设计

### 1. 内核（`zenagent/agent/session.py`）

- `ARCHIVE_FILENAME = ".archive.json"`；sidecar 形态 `{"<文件名>": <归档时间戳 epoch 秒>}`。
- `read_session_archived(directory) -> dict[str, float]`：缺失/损坏/形态不对 → `{}`（与 pins/titles 同一容错约定）。
- `set_session_archived(session_file, archived: bool) -> float`：校验是存在的 `.jsonl`（同 pin 的 `ValueError`）；写入走原子替换模板 `_write_session_...`；返回归档时间戳（取消归档返回 `0.0`）。
- `list_sessions(..., archived: str = "exclude")`：`exclude`（默认，现状行为）/ `include` / `only`；非法值 `ValueError`（同 `_SCOPES` 校验风格）。归档判定在"信封空文件跳过"逻辑**之前**：归档的会话一律按归档语义处理。
- `SessionInfo` 加 `archived: bool = False`、`archived_at: float = 0.0`，由 `list_sessions` 填充（`SessionIndex.read()` 不感知 sidecar）。
- `latest_session()`：跳过归档文件——`--resume latest` 不静默跳进归档会话。

### 2. agent 工具（`zenagent/tools/sessions.py`）

- 三件套全部传 `archived="include"`：归档会话照常列出、可搜、可按 id 读——满足"工具仍能找到"。
- 列表行与命中头对归档会话追加 ` (archived)` 标记，模型能看到状态。

### 3. RPC（`zenagent/cli/rpc/`）

- 新 op `archive`（`kind=session`）：请求 `{path: str, archived: bool}`，host 侧处理
  （纯 sidecar 写，不依赖 slot；项目未打开也能操作，Settings 页依赖这一点）。
  目标会话正处于运行中的 run（busy）→ `session_busy` 拒绝（同 delete 先例）。
- `session list` 请求加 `archived: "exclude"|"include"|"only"`（默认 `exclude`）：
  sidebar 用默认；Settings 用 `only`。
- `SessionSummary` 加 `archived: bool`、`archived_at: float`；重新生成 `wire.gen.ts`
  （`scripts/gen_rpc_ts.py`），契约测试同步。
- 桌面端归档**当前**会话时先发归档、成功后切新会话（客户端两步；先归档才能让 busy 拒绝对在屏会话生效）；恢复不触碰任何 runtime。

### 4. 桌面（`desktop/`）

- sidebar 行菜单加"归档"（置顶项之后、删除之前）；**单步、无二次确认**（可逆操作，
  与删除的两步确认区分）。无文件的占位/live 行不显示归档项。
- Settings → Projects：每个项目行可展开"已归档 (N)"区块，列出标题、最后活动时间、
  归档时间，每行一个"恢复"按钮。项目行显示归档计数徽标。
- 归档会话不进 sidebar、不进命令面板（两者共用 exclude 后的列表数据，天然满足）。
- i18n：`session.archive` / `session.restore` / `settings.projects.archived` 等键，
  中英两份目录都加（`i18nKeys` 测试与 `check_i18n.py` 把关）。

### 5. CLI（`zenagent/cli/sessionscmd.py`）

- `zen sessions list` 默认排除归档（免费，来自 `list_sessions` 默认值）；
  加 `--archived` 旗标列出**仅归档**会话（镜像 Settings 视图）。REPL `/sessions` 不变（默认排除）。

## 边界与不变量

- 归档优先于置顶：归档的置顶会话同样隐藏，恢复后置顶状态原样保留（pins sidecar 不动）。
- legacy 目录（`~/.zenagent/sessions`）只读：拒绝归档（先 adopt 进项目）。
- 显式 resume 一个归档会话（按路径）可用，但**不**自动取消归档——只有显式 archive/restore 改状态。
- `.trash/` 与 `_index.json` 不受影响（glob 本来就只看顶层 `*.jsonl`）。
- 归档会话正被其他 slot 打开：文件继续正常写入（路径没变），该 slot 的 sidebar 由
  "current 占位行"兜底显示；这是可接受的行为。

## 测试

- `tests/test_session.py`：sidecar 读写/损坏容错/非法文件拒绝；`list_sessions` 三种
  archived 过滤；`latest_session` 跳过归档。
- `tests/test_app_sessions.py`：`SessionManager` 归档/恢复、entries 默认排除。
- `tests/test_tools_sessions.py`：三件套能看到并标记归档会话、只读不写。
- RPC：契约测试（新 op + 新字段 + wire 重新生成）、busy 拒绝。
- 桌面：Settings 归档面板渲染与恢复动作、sidebar 菜单项、i18n 键。

## 留给实现者的细节

- sidecar 时间戳格式、错误文案的具体措辞、Settings 展开交互的具体样式按现有代码风格定。
- 文档同步：`docs/frontend-matrix.md` 的 session op 表加 `archive`；CHANGELOG 一条。
