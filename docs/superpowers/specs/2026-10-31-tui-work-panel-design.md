# ZenAgent TUI 工作面板设计（2026-10-31）

状态：待审批
作者：Lead（与用户讨论后整理）
基线：v0.4.2（`ba18b72`，2026-10-31）
相关：`zenagent/cli/tui/app.py`、`zenagent/cli/tui/theme.tcss`、`zenagent/cli/tui/widgets/sidebar.py`、
`zenagent/cli/tui/widgets/todo_hud.py`、`zenagent/cli/tui/screens/modals.py`、
`zenagent/app/chat.py`、`zenagent/app/views.py`、`zenagent/app/runtime.py`、`zenagent/app/sessions.py`、
`zenagent/tools/task.py`、`zenagent/tools/todo.py`、`zenagent/tools/jobs.py`、
`zenagent/agent/artifacts.py`、`zenagent/agent/session.py`、
`docs/research/omp-cli-study/02-layout.md`（omp 的 dock HUD 带先例）

## 1. 意图与为什么

v0.4.x 让 agent 有了三种"同时发生的事"：`todo` 清单、`bash async` 后台任务（`bg_N`）、
`task` 子代理；另外还有扩展注册的事件钩子。但 UI 只把 todo 暴露成一行 HUD
（`widgets/todo_hud.py`），后台任务和子代理在转录里只留一张工具卡——跑起来之后，
没有任何地方能回答"还有几个在跑、哪个卡住了、子代理到底在做什么"。

侧栏的情况更糟：`1e5663b`（omp 单列 dock 重构）删掉了 `Sidebar.refresh_contents()` 和
`Sidebar.apply_width()` 的调用点，只留下 `ctrl+b` 切 `display`。所以今天按 `ctrl+b`
打开的是 **26 列空白面板**，Session/Model/Runtime 三段是死代码，也不再按宽度自动隐藏。

用户的判断：侧栏应该是**工作面板**（正在同时发生的事的家），不是装饰。本设计把它做实，
并补上"点开子代理看它干了什么、resume 之后也能回看"这条链路。

## 2. 目标 / 非目标 / 反目标

**目标（逐条可验收）**

1. 侧栏（`ctrl+b`，宽 ≥100 列）分区显示：Session / Model / Runtime / Todo / Subagent / Jobs；
   窄于 100 列自动隐藏。
2. todo **单家不变式**：任一时刻只有一个可见的 todo 面——侧栏可见时在侧栏，否则回落 dock
   HUD（现状位置与展示不变，只新增点击）。点击任一处打开只读详情浮层。
3. 后台任务在侧栏列出：id、截断命令、状态、耗时；运行中每秒刷新，结束后 ≤1s 反映。
   `wait` 取走的 job 从列表消失（沿用 `JobManager` 生命周期）。
4. subagent 在侧栏列出"当前 + 最近几个"（名字/状态/耗时/成本）；点击进入**只读**子转录
   视图（占用主区），`esc` 返回；查看期间禁用输入。
5. 子转录持久化到会话级目录；`--resume` / 重启后可回看；无转录的旧卡片显示"无转录"且不可点。
6. 侧栏关闭且后台有活运行时，dock 出现一行可点提示条，点它打开侧栏。
7. 全部离线可测（Textual `run_test()` + fake stream）；headless / REPL / RPC 行为不变，
   `bash scripts/check.sh` 全绿。

**非目标（本期不做）**

- 多个 subagent 并发（`task` 仍同步单发，最多 1 个在跑）。
- 与 subagent 对话、给 todo 勾选/编辑、kill 后台 job、job 输出查看器。
- 侧栏拖宽、多栏布局、鼠标选中文本之外的新交互。
- 子转录进 `/sessions` 列表、独立导出命令、进入父模型上下文。
- hook 的可见化（用户明确本轮不做）。

**反目标（出现即视为失败）**

- `zenagent/app/*` import Textual，或 TUI 直接操作 runtime 内部对象（必须经
  `ChatController` / `RunStatus`）。
- 子转录写进父 session JSONL，破坏"session 文件 = 公开消息模型、可 jq/回放"的契约。
- 每个流式 delta 都写盘；或任务文件无限增长不清理。
- 侧栏与 dock 同时显示 todo（双家）。
- 会话删除/切换后留下孤儿转录，或把别的会话的转录带进来。
- 破坏"输入行贴终端底部"的 dock 不变式（`#transcript` 1fr → `#status-band` → `#prompt-row`）。

## 3. 约束

- 基线 v0.4.2；单列 dock 结构与 `#status-band`（1 行）→ `#prompt-row`（`╰─ ` + 输入）顺序不变。
- 侧栏固定宽 26（`theme.tcss` 的 `#sidebar { width: 26; padding: 1 2 }`），内容宽 22；
  窄屏阈值 `NARROW_WIDTH = 100`（现定义在 `widgets/sidebar.py`）。
- app 层与 UI 无关：`zenagent/app/*` 不 import Textual；TUI 只读快照。
- 已有事件通道：`tool_execution_start/update/end`；`task` 的 `on_update` 参数已声明但从未调用；
  `tools/ask.py` 的 `set_ask_handler` 是"工具侧留钩子、前端挂 handler"的既有先例。
- 写盘 best-effort、绝不致命（artifact spill 已确立该语义）；新文件 `0600`。
- 离线测试，不新增网络依赖；`bash scripts/check.sh` 是唯一闸门。

## 4. 方案对比与选择

### 4.1 侧栏状态从哪来 → A 快照流（用户已选）

| | A. 快照流（选） | B. 侧栏自取 | C. 事件推送 |
|---|---|---|---|
| 数据来源 | `ChatController.status`（`RunStatus`）多带 `jobs`/`subagents` | 组件自己问 job manager、自己订阅 | job manager / task tool 加 observer，chat 订阅后发事件 |
| 重绘路径 | 仍只有 `_refresh_status` 一处 | 第二条重绘路径 | 事件 + 原有重绘 |
| 层级 | TUI 只读快照，app 层组装 | 组件穿透到 runtime 层 | 新增一层事件契约（jobs 属工具层） |
| 测试 | `RunStatus` 是纯数据，可 headless 测 | 从 UI 里挖 | 要测事件时序 |
| 代价 | `RunStatus` 多一块字段；job 结束最多晚 1s | 分层被破坏 | 为省 1 秒加一层契约 |

选 A：`todo_hud.py` 的注释已把"全部经 `_refresh_status` 同一条状态路径"写成原则；
job 没有事件源，用 1 秒轮询即可（人眼够用），不必为它引入事件层。

### 4.2 侧栏关着时如何察觉后台有活 → A dock 自清提示条（用户已选）

| | A. dock 提示条（选） | B. 状态带加段 | C. 自动开侧栏 |
|---|---|---|---|
| 形态 | dock 新行 `◐ 2 jobs · 1 task · ctrl+b`，可点 | `status/segments.py` 新 segment | 有活时自动展开侧栏 |
| 问题 | 只在需要时占一行（自清） | 状态带是单行 `Static`、窄屏丢段，做点击要算 x 区间——看得见点不到 | 布局跳动、窄屏打不开、违背手动关闭意图 |

选 A：与错误横幅 / 附件 chip 同一个位置模式（dock 里可点、自清），不动状态带的信息密度。

### 4.3 子转录存哪 → A 会话级目录（用户已选）

**路径：`<sessions dir>/tasks/<session stem>/<call_id>.jsonl`**，与 artifacts 同构
（`<sessions dir>/artifacts/<stem>/`）。讨论稿写的是 `<session>.jsonl.tasks/`；两者等价，
这里跟随 artifacts 的分组布局，理由：这是"一个会话 ↔ 多个文件"，仓库里唯一先例是
artifacts；分组目录也让 sessions 目录不会每个会话多一个顶层子目录。

| | A. 会话级目录（选） | B. 复用 artifacts 目录 | C. 子 agent 自己落 session |
|---|---|---|---|
| 优点 | 持久、可 jq/read_file 直读、不碰父文件契约 | 少一个目录，复用 0600/清理代码 | 复用现成会话层 |
| 问题 | 新增一类 sidecar + 自己的保留策略 | artifact 是"超大工具结果"语义；64 文件 / 8 MiB 的驱逐会把转录当垃圾清；读侧要扫 artifact 命名 | 污染 `/sessions` 列表、跨项目作用域，推翻 v0.4.1"子 agent 不落 session"的契约 |

选 A，并沿用 artifact 式的保留上限（见 §5.2）。

### 4.4 子转录在哪看 → A 主区切换（用户指定）

| | A. 主区切换（选） | B. 全屏 ModalScreen | C. 侧栏内展开 |
|---|---|---|---|
| 优点 | 就是"主对话框"；宽度足够；侧栏仍在可继续切换；父转录后台继续同步 | 隔离干净、实现最省 | 不占布局 |
| 问题 | 两个 `TranscriptView` + 模式状态 | 盖住侧栏，失去导航；不像"主对话框" | 26 列放不下任何转录 |

### 4.5 todo 详情 → A 只读浮层（用户同意）

A 只读 `DialogScreen`（选）：复用现成框与 `esc` 语义，能显示完整条目。
B dock 原地长高：顶转录、输入区抖动，否决。C 可编辑列表：todo 由模型维护，用户只读，否决。

## 5. 详细设计

### 5.1 数据模型（`zenagent/app/views.py` 增量）

```python
JobStatusView = Literal["running", "ok", "failed", "timed_out"]

@dataclass(frozen=True)
class JobView:
    id: str
    command: str          # 原样；截断是 UI 的事
    status: JobStatusView
    started_at: float     # time.time()，UI 算实时耗时
    duration_ms: int | None
    exit_code: int | None

@dataclass(frozen=True)
class SubagentView:
    call_id: str
    name: str
    status: Literal["running", "ok", "error", "aborted", "interrupted"]
    duration_ms: int
    cost: float
    tokens: int
    has_transcript: bool   # False → 侧栏行置灰不可点
```

`RunStatus` 新增三个字段（保持纯数据、可 headless）：

```python
jobs: list[JobView] = field(default_factory=list)
subagents: list[SubagentView] = field(default_factory=list)
active_subagent: str | None = None    # 正在跑的 task 的 call_id
```

`ToolCallView` 新增 `started_at: float | None = None`（`time.monotonic()`，仅活视图用，
不持久化；用于算 active subagent 的实时耗时）。

来源：
- `jobs`：`ChatController.status` 里 `from zenagent.tools.jobs import get_job_manager`，
  `try/except RuntimeError`（无运行中的 loop 时给空列表）；`Job → JobView` 纯转换。
  排序 = 运行中在前、再按 `started_at` 倒序；UI 只取前 6 条。
- `subagents`：会话内内存条目 + `SubagentStore.list()`（磁盘 meta）按 `call_id` 去重合并，
  **只保留当前 transcript 里仍引用得到的 `call_id`**（即 `task` 工具卡的 id；`/undo`、`/fork`
  截断后留下的孤儿文件不显示），活动条目在前、其余按 `started_at` 倒序；UI 取前 5 条。
  为避免每次 `status` 扫盘，`ChatController` 缓存 `_subagent_entries`，在 task 结束 /
  `reload_transcript` / `clear` 时失效。
- `active_subagent`：`_cards` 中 `name == "task" and status == "running"` 的 `call_id`。

### 5.2 存储模块（新 `zenagent/agent/subagents.py`，UI 无关）

放在 `agent/` 而不是 `app/`：写者 `zenagent/tools/task.py` 必须能 import，而 tools 不能
import app；`agent/session.py`、`agent/artifacts.py` 已确立这个位置。

```python
TASKS_DIRNAME = "tasks"
MAX_TASK_FILES = 32                      # 每会话任务文件数上限
MAX_TASK_TOTAL_BYTES = 8 * 1024 * 1024   # 每会话任务转录总量上限
MAX_TASK_FILE_BYTES = 2 * 1024 * 1024    # 单文件上限，超出写截断标记后停止追加
TRANSFER_CHUNK = 64 * 1024

def task_dir(session_path: Path) -> Path                       # <sessions dir>/tasks/<stem>/
def carry_task_dir(old_session: Path, new_session: Path) -> None   # best-effort 复制
def trash_task_dir(session_path: Path, trash_dir: Path) -> Path | None  # best-effort 挪走

@dataclass(frozen=True)
class SubagentMeta:
    call_id: str; name: str; model: str; started_at: float
    status: str = "running"; duration_ms: int = 0
    cost: float = 0.0; tokens: int = 0; truncated: bool = False

class SubagentStore:
    """One session's subagent transcripts (memory-only when session_path is None)."""
    def __init__(self, session_path: Path | None) -> None
    def bind_session(self, session_path: Path | None) -> None
    def begin(self, call_id: str, name: str, model: str) -> None
    def append(self, call_id: str, messages: list[Message]) -> None   # 只追加未写过的尾部
    def finish(self, call_id: str, *, status: str, duration_ms: int,
               cost: float, tokens: int) -> None
    def list(self) -> list[SubagentMeta]        # 读每个文件的首行 meta + 末行 end
    def load(self, call_id: str) -> list[Message] | None
    def prune(self) -> None                     # 32 文件 / 8 MiB，删最旧
```

**文件格式**（JSONL，与 session 同一种消息序列化）：

```jsonl
{"type":"meta","call_id":"call_ab12","name":"research","model":"deepseek-flash","started_at":1761...}
{"role":"user","content":[...]}          ← Message.to_json()，原样
{"role":"assistant","content":[...]}
{"role":"tool","tool_call_id":"...","content":[...]}
{"type":"end","status":"ok","duration_ms":18400,"cost":0.0031,"tokens":812}
```

- 缺 `end` 行 = 中断（`status="interrupted"`，仍可读已写部分）。
- 未知/损坏行跳过并计入 `truncated`，与 `Session.load` 同样的宽容策略。
- 文件权限 `0600`；目录 `0700`（`mkdir(mode=0o700)`）。
- 超 `MAX_TASK_FILE_BYTES` 时追加 `{"type":"truncated"}` 后停止写该文件，`meta.truncated=True`。
- 写失败（权限/磁盘）静默降级为 memory-only，绝不冒泡到 agent 循环。

### 5.3 子代理事件与转发（`zenagent/tools/task.py`）

```python
SubagentObserver = Callable[[str, str, dict], None]   # (call_id, kind, payload)
# kind: "start" | "event" | "end"

def set_subagent_observer(observer: SubagentObserver | None) -> None
def get_subagent_observer() -> SubagentObserver | None

@dataclass
class TaskTool(AgentTool):
    bind_session: Callable[[Path | None], None] = _noop_bind

def build_task_tool(..., session_path: Path | None = None) -> TaskTool
```

- `bind_session` 与 `TodoTool.bind_session` 同形，由 `Runtime.session` setter 调用（§5.7）。
- child 的 listener：保留 `guard.on_event`，再加一个；它做三件事：
  1. **进度转发**（节流 ≥100ms，文案形如 `turn 2 · read_file src/x.py`）→ 构造
     `ToolResultAgent(content=[TextContent(text=line)])` 调 `on_update`，走现有
     `tool_execution_update` → `ToolCallView.progress` → 卡片 `⚙` 行（顺带修好现存缺口：
     task 卡的 progress 今天永远是空的）。无 observer 也照常转发。
  2. **落盘**：child 的 `message_end` / `tool_execution_end` 后 `store.append(call_id, child.messages)`。
  3. **广播**：`observer(call_id, "event", {"event": event})` 给 TUI 的活缓冲。
- start 时：`store.begin(...)` + `observer("start", {"name","model","started_at"})`。
- end（含异常 / abort）：`store.finish(...)` + `observer("end", meta_dict)`；`finish` 幂等。
- 四个 `build_task_tool` 调用点（`app/runtime.py`、`cli/main.py`、`cli/repl.py`、`cli/rpc.py`）
  都传各自的 `session_path`；无会话的前端传 `None`（memory-only，行为同今天）。

### 5.4 视图构建重构（`zenagent/app/views.py`）

从 `ChatController._on_event` 抽出纯视图层（父、子共用）：

```python
class TranscriptBuilder:
    """AgentEvent → MessageView / ToolCallView（不碰控制器状态）。"""
    views: list[MessageView | ToolCallView]
    cards: dict[str, ToolCallView]
    current: MessageView | None
    def load(self, messages: list[Message]) -> None      # messages_to_views 起步
    def apply(self, event: AgentEvent) -> bool           # True = 有可见变化
```

`ChatController` 保留 `transcript` / `_cards` 的对外语义，但内部改为持有一个 builder
（父链）与若干子 builder；计划/补丁里的 `messages_to_views` 保持不变（`load` 内部调用它）。

**顺序要求**：Slice 2 第一步只做这个纯重构（父路径行为逐字节不变），现有 TUI 测试全绿后
再接子路径，避免"重构 + 新功能"混在一个 diff 里。

### 5.5 ChatController 增量（`zenagent/app/chat.py`）

- `self._subagents: dict[str, _SubagentBuffer]`（builder + meta + name），`_subagent_entries`
  缓存。
- `on_subagent_event(call_id, kind, payload)`：`start` 建 buffer/条目；`event` →
  `builder.apply`；`end` 更新条目。**会话内保留 views**，点击优先读内存，无需读盘。
- `subagent_transcript(call_id) -> list[MessageView | ToolCallView] | None`：
  内存优先，否则 `SubagentStore(session).load(call_id)` → `messages_to_views`。
- `subagent_entries()`：内存 + `store.list()` 合并（§5.1）；`has_transcript` =
  内存有 buffer 或磁盘有文件。
- `status` 增加 §5.1 字段。
- 读侧不持有 store 状态：`subagent_entries()` / `subagent_transcript()` 每次用
  `SubagentStore(self._session_path())` 现构造（只是路径对象，无 IO 直到 `list()`/`load()`），
  于是会话切换天然生效，不需要额外的重绑逻辑。写侧 store 归 `task` 工具所有（§5.3）。
- `reload_transcript()` / `clear()`：清子 buffer 与缓存即可。
- `retry()`：新 session 落定后 `carry_task_dir(old, new)`。

### 5.6 TUI 布局与状态（`zenagent/cli/tui/app.py`、`widgets/sidebar.py`、`theme.tcss`）

**布局（侧栏打开，宽 ≥100）**

```
┌ 转录 (#transcript, 1fr) ───────┬ sidebar 26 ──────────┐
│  …                             │ Session   …          │
│  …                             │ Model     …          │
│                                │ Runtime   running    │
│                                │ ─────────────        │
│                                │ Todo      3/7  ←点击 │
│                                │ ─────────────        │
│                                │ Subagent             │
│                                │  ◐ research 12s  ←   │
│                                │  ✓ summary $0.003    │
│                                │ ─────────────        │
│                                │ Jobs                 │
│                                │  bg_1 ● 4s           │
│                                │  bg_2 ✓ 1.2s         │
├────────────────────────────────┴──────────────────────┤
│ todo HUD / work strip（互斥）                          │
│ status band（1 行）                                    │
│ ╰─ 输入…                                               │
└───────────────────────────────────────────────────────┘
```

**侧栏**：`Sidebar(VerticalScroll)`，id 不变，内含 6 个 section widget
（`SidebarSection`：标题 + 行容器）。行组件 `SidebarRow(Static)` 支持 `on_click`；
按 key（`call_id` / `job.id`）做增量对账，1 秒轮询时只更新变化的行，不整树重建。
Todo 区整块可点；Subagent 行可点（`has_transcript=False` 置灰）；Jobs 行不可点。

**chrome 决策集中在一处**（替代被删掉的 `apply_width`）：

```python
# ZenApp
self._sidebar_open: bool = False
def _sync_chrome(self) -> None:
    visible = self._sidebar_open and self.size.width >= NARROW_WIDTH
    sidebar.display = visible
    todo_hud  -> 隐藏当 visible（单家不变式）
    work_strip.set_work(running_jobs, active_task, sidebar_hidden=not visible)
```

`_sync_chrome()` 由 `_refresh_status()`、resize、`ctrl+b` 调用。刷新路径仍只有
`_refresh_status` 一个重绘点。

**WorkStrip**（dock 新行，放在 `TodoHud` 之后、`StatusBand` 之前）：显示条件
`not sidebar_visible and (running_jobs or active_task)`；文本 `◐ 2 jobs · 1 task · ctrl+b`；
`on_click` → `_sidebar_open = True; _sync_chrome()`。

**TodoListScreen**（`screens/modals.py` 新 screen，`DialogScreen[None]`）：只读；phase
分组 + 状态符号（`✓ done` / `▶ in progress` / `○ pending` / `⊘ blocked`）+ 完整文本
（换行不截断）；内容超高时内部 `VerticalScroll`（`max-height: 70%`）；`esc` 关闭并归还焦点；
命令面板加一条 "Show todo list"（键盘可达路径）。HUD 与侧栏 Todo 区点击都打开它。

**子转录查看模式**：

- `#main-column` 内新增 `#transcript-stack`（`height: 1fr`）：`SubagentBanner`（默认
  `display=False`）+ 父 `TranscriptView`（保留现有 `#transcript` id 与 CSS）+ 子
  `TranscriptView`（id `subagent-transcript`，默认 `display=False`）。父 view 是现有那个，
  不搬动；`NewContentBar` 与 `#dock` 仍在 `#transcript-stack` 之外、结构不变。
- `ZenApp._viewing: str | None`。
- `enter_subagent_view(call_id)`：`child.reset(); child.sync(chat.subagent_transcript(call_id), status)`；
  父 view `display=False`；banner 显示 `◐ subagent: <name> · 只读 · esc 返回`；
  `PromptInput.disabled = True`；焦点给子 view（滚轮/翻页可用）。
- `_refresh_status`：看模式下 `child.sync(...)`（活视图持续更新），否则父 view `sync`；
  父 view 即使隐藏也继续 `sync`，返回即最新。
- `action_interrupt`：`if self._viewing: self._exit_subagent_view(); return`；否则原逻辑
  （`escape` 现绑定 `interrupt`，不新增键位）。
- `_exit_subagent_view()`：恢复 display、`PromptInput.disabled = False`、焦点回输入。
  会话切换 / `/clear` / `reload_transcript` 都会调它。
- 侧栏 Subagent 行点击 → `enter_subagent_view(call_id)`；视图中任务结束不退出，banner
  状态变为 `✓ done`。

**定时刷新**：`self._work_timer`（`set_interval(1.0, ...)`）只在 `running_jobs or
active_task` 时运行，空闲即 `stop()`；回调只走 `_schedule_refresh()`（既有 debounce 路径）。

**observer 生命周期**：`ZenApp.on_mount` 里 `set_subagent_observer(self.chat.on_subagent_event)`，
`on_unmount` 里置 `None`（与 `set_ask_handler` 同一注册模型，单进程单交互前端）。

### 5.7 会话生命周期（绑定 / 搬迁 / 删除）

- **绑定**：`Runtime.session` setter 里追加 `task_tool.bind_session(session.path)`
  ——它已经是 artifact sink 与 `todo_tool.bind_session` 的唯一 choke point，覆盖
  bootstrap/resume/fork/undo/retry/compact/新会话全部路径。
- **搬迁**（历史相关转录必须跟着消息走）：`carry_task_dir(old, new)` 调用点 4 处：
  `SessionManager.fork()`、`SessionManager.undo()`、`ChatController.retry()`、
  `ChatController.compact_now()`（`switch_session(path, carry_todos=True)` 处）。
  复制整个目录（bounded：≤32 文件 / 8 MiB），不按 call_id 过滤——未引用文件无害且会被 prune。
- **删除**：`SessionManager.delete()` 除把 `.jsonl` 挪进 `.trash/` 外，把
  `tasks/<stem>/` 挪成 `.trash/<stem>.tasks/`（best-effort，失败不阻断删除）。
- **`/clear`**：新会话，不搬（与 todo 清空一致）。
- **resume 重建**：`ChatController` 在 `__init__` / `reload_transcript` 之后，
  `SubagentStore(session).list()` 得到历史条目；侧栏行点击时 `load(call_id)` →
  `messages_to_views`。历史里 `task` 工具卡的 `call_id` 与文件名一一对应
  （`messages_to_views` 已保留 `ToolCall.id`）。

### 5.8 数据流

```
task.py (child Agent)
  ├─ store.append/finish ──→ <sessions>/tasks/<stem>/<call_id>.jsonl   （持久）
  ├─ on_update(…) ─────────→ tool_execution_update → ToolCallView.progress（卡片 ⚙）
  └─ set_subagent_observer ─→ ChatController.on_subagent_event
                                ├─ 子 TranscriptBuilder（活缓冲）
                                └─ RunStatus.subagents（侧栏）
JobManager.list() ──→ ChatController.status ──→ RunStatus.jobs ──→ 侧栏 Jobs
                                 │
                         _refresh_status（唯一重绘点，1s 轮询触发）
                                 │
                 Sidebar / WorkStrip / TodoHud / TranscriptView
                                 ↑
           点击 Sidebar Todo → TodoListScreen（只读浮层）
           点击 Sidebar Subagent → enter_subagent_view（主区切换，esc 返回）
```

## 6. 验收与测试

**单元（离线，无 Textual）**

- `SubagentStore`：begin/append/finish 往返；缺 `end` → `interrupted`；损坏行跳过；
  单文件超限写 `truncated` 后停止；32 文件 / 8 MiB 驱逐最旧；`carry_task_dir` 复制；
  `trash_task_dir` 挪 `.trash`；无 session（`None`）时 memory-only。
- `TranscriptBuilder`：与 `_on_event` 抽取前行为一致（用现有 chat 事件测试对比）。
- `RunStatus`：`jobs`/`subagents` 组装、排序、去重、无 loop 时空列表。
- `task.py`：observer 三阶段调用；`on_update` 节流；写盘失败不影响 envelope；abort 路径 finish。

**TUI（`run_test()` + fake stream）**

- `ctrl+b` 打开侧栏：6 个区都在；<100 列自动隐藏且 todo 回落 HUD。
- 单家不变式：侧栏可见时 `TodoHud.display is False`，反之亦然。
- 点击 HUD / 侧栏 Todo 区 → TodoListScreen 出现，`esc` 关闭。
- 侧栏 Jobs：注入 fake `Job` → 行显示；job 完成 ≤1s 后状态变 `✓`；`wait` 移除后行消失。
- WorkStrip：侧栏关 + job 在跑 → 出现；点击 → 侧栏打开且条消失；无活 → 不出现。
- 子转录：点击 Subagent 行 → 主区切换到子 view、banner 文案正确、`PromptInput.disabled`；
  `esc` 返回且父转录内容未丢；查看中父转录继续增长，返回后可见。
- resume 集成：tmp session dir 跑一轮 fake `task`（走真实 task 工具 + fake stream）→ 重建
  app → 侧栏出现历史条目 → 点开看到内容；无文件的旧卡片置灰。
- 回归：现有 TUI 测试全过；`bash scripts/check.sh` 全绿。

## 7. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 父路径视图重构破坏现有流式渲染 | Slice 2 第一步只做纯重构，现有 TUI 测试当闸门，单独提交 |
| 1s 轮询让 `_refresh_status` 变贵 | `sync` 是按变化量成比例的（`TranscriptView.sync` 注释）；轮询只在有活时运行 |
| 写盘拖慢子 agent | 轮次边界批量追加、单文件 2 MiB 上限、失败静默降级 |
| 侧栏内容超高（长 todo/多任务） | `Sidebar(VerticalScroll)` + Jobs 6 / Subagent 5 条 cap，Todo 全量 |
| 会话搬迁遗漏导致"转录丢失" | 全部集中在 `carry_task_dir`，4 个调用点有测试；删会话进 `.trash/` 可恢复 |
| observer 是模块级全局（多 runtime） | 与 `set_ask_handler` 同约束：单进程单交互前端；卸载时置 `None` |

## 8. 实现拆分

**Slice 1 — 侧栏底座（无引擎改动）**

1. `views.py`：`JobView`/`SubagentView`/`RunStatus` 字段、`ToolCallView.started_at`。
2. `chat.py`：`status` 组装 jobs/subagents（subagents 先只来自 task 卡片），1s 轮询开关。
3. TUI：侧栏 6 区 + 恢复 Session/Model/Runtime；`_sync_chrome` + 单家不变式；WorkStrip；
   `TodoListScreen` + palette 项 + HUD/侧栏点击。
4. 测试。

**Slice 2 — 子转录**

1. 纯重构：`TranscriptBuilder` 抽取（现有测试全绿，单独提交）。
2. `agent/subagents.py`：`SubagentStore` + carry/trash（纯函数，先测）。
3. `tools/task.py`：`TaskTool.bind_session`、observer、progress 转发、落盘；4 个调用点接线。
4. `Runtime.session` setter 重绑；`fork/undo/retry/compact` 的 `carry_task_dir`；
   `SessionManager.delete` 的 trash 搬运。
5. `chat.py`：`on_subagent_event`、子 buffer、`subagent_transcript`、resume 条目。
6. TUI：子转录视图、banner、输入禁用、`esc` 返回、侧栏行点击。
7. 测试（含 resume 集成）。

## 9. 留给实现者的决定

- Jobs 区显示 6 行、Subagent 区 5 行的具体常数；轮询 1.0s 的具体值（放常量便于测试）。
- WorkStrip 的文案/符号细节（`◐` / `·` / `ctrl+b` 的顺序）。
- 侧栏 section 的行高对齐、分隔线用空行还是 `─`（跟现有 `#sidebar` 视觉走）。
- `SubagentBanner` 的具体样式（复用 `#error-banner` 的底色或更安静的一行）。
- TodoListScreen 的超高处理细节（`VerticalScroll` 还是分页）。
- `_subagent_entries` 缓存失效的最小触发集（建议：task end、bind_session、reload_transcript）。
