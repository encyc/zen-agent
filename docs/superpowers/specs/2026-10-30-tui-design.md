# ZenAgent TUI 设计（2026-10-30）

状态：待审批
作者：Lead（与用户讨论后整理）
相关：`docs/architecture.md`、`zenagent/cli/repl.py`、`zenagent/cli/rpc.py`

## 1. 意图与为什么

现在的交互是「prompt_toolkit 单行输入 + print 直写终端」：流式文本、工具输出、状态行
互相挤占；没有固定状态区；工具结果只有一行 `[read_file: ok]`；运行中只能 steer，没有
follow-up 队列。用户把它总结为「交互很差」。

目标不是把命令变多，而是把终端从「打印流水线」变成「有固定布局的应用」：
底部状态栏常驻、消息与工具结果结构化、模型与会话可以浏览和切换、运行中可排队。

同时，用户明确规划了下一步：REPL 要独立成一块，未来做 Tauri 2 桌面端。
因此本次必须先建**与 UI 无关的应用层**，TUI 只是第一个富客户端；REPL 降级为另一个
前端；RPC 成为第三个前端（未来给 Tauri sidecar 用）。

## 2. 目标 / 非目标 / 反目标

**目标**
1. `zen`（TTY 下）进入全屏 TUI：顶部标题/状态、中部对话区（内部滚动）、底部输入 + 状态栏。
2. 对话区结构化：用户消息、assistant 消息（markdown + thinking 折叠）、工具调用卡片
   （默认单行摘要，可展开看参数/结果/diff）。
3. 底部状态栏常驻：模型、thinking、本轮 tokens/cost、context 占用、队列数、运行状态。
4. 模型管理：浏览 catalog、查看上下文/价格/能力、切换（下一轮生效）。
5. 会话管理：列出会话（时间/消息数/大小）、恢复、新建、fork、删除、导出。
6. 运行中：Enter = follow-up 排队；Ctrl+S = steer；Ctrl+C = 中断；队列可见、可清空。
7. REPL 保持可用（`zen --repl`），headless（`-p`）与 RPC 行为不变。
8. 应用层可被未来的 Tauri/其他前端复用，不依赖 Textual。

**非目标（本期不做）**
- 鼠标、动画、3D、图片渲染、音视频、TTS/STT。
- 多 agent/subagent UI、plan mode、LSP/DAP 面板、浏览器面板。
- 编辑 provider/密钥（只读展示 + 指向 config.toml）。
- 会话重命名、会话搜索、跨机器同步。
- Tauri 实现（本期只保证接口边界）。

**反目标（出现即视为失败）**
- TUI 逻辑渗进 `zenagent/agent/*` 或 `zenagent/providers/*`。
- 因为加 TUI 而破坏 565 个既有测试、headless、`--json`、RPC 的 stdout 纯净性。
- 把 Textual 变成内核依赖（必须只在 `[cli]` extra）。
- 做出一个「只是彩色 print」的半成品却宣称是 TUI。

## 3. 约束

- Python >=3.11；内核仍只依赖 `httpx`。
- Textual 只在 `[project.optional-dependencies].cli`，版本 `textual>=8.2,<9`。
- TUI 只在 TTY 且显式/默认启用时运行；非 TTY 自动回落到行式 REPL（打印一条说明）。
- 所有 UI 测试必须能在无终端环境跑（Textual `run_test()` pilot + app 层 fake stream）。
- 不引入新的事件循环框架；复用现有 `Agent`/`AgentEvent`。
- 退出 TUI 后终端必须恢复；会话内容已在 JSONL，退出时打印一行摘要（session 路径 + 用量）。

## 4. 方案对比与选择（用户已选 B）

| | A. prompt_toolkit 全屏 | **B. Textual** | C. 内联增强 |
|---|---|---|---|
| 新依赖 | 0 | +textual（0.7MB，6 个依赖，rich 已有） | 0 |
| 侧边栏/滚动/列表/模态 | 手写 | 现成组件 | 不支持侧栏 |
| 估计代码量 | 2000–3000 行 | 1200–1800 行 | 600–900 行 |
| 与现有 prompt_toolkit REPL | 可共用 | 二选一（不混用） | 共用 |
| 长期（Tauri 桌面前端） | app 层复用 | app 层复用 | app 层复用 |

**选择 B**。理由：模型/会话管理本质是列表+选择+表单，Textual 组件直接覆盖；UI 是长期产品面，
组件化投入可复用；依赖成本可接受且隔离在 cli extra。放弃「零新依赖」以换开发速度和成品度。

## 5. 架构

```
┌──────────────────────────────────────────────────────────────────┐
│ kernel  zenagent/{types,events,providers,agent,tools,...}         │ 不变
└───────────────────────────────┬──────────────────────────────────┘
                                │ Agent / AgentEvent / Session
┌───────────────────────────────▼──────────────────────────────────┐
│ app（新增，禁止 import textual）                                  │
│  chat.py       ChatController: 一次 run 的状态机 + 视图模型        │
│  models.py     ModelManager: catalog 列表/当前/切换               │
│  sessions.py   SessionManager: list/resume/new/fork/delete/export │
│  runtime.py    Runtime: 装配 Agent（MCP/skills/extensions/guard/   │
│                gate/session/metrics），会话/模型切换时重建          │
│  commands.py   CommandRegistry: 名称→描述→async handler           │
│  permissions.py PermissionBroker: before_tool_call 的交互实现      │
│  views.py      UI 无关的视图模型（dataclass，可 JSON 化）+          │
│                messages_to_views() 历史消息→卡片                  │
└───────┬───────────────────┬──────────────────────┬───────────────┘
        │                   │                      │
  cli/repl.py        cli/tui/                cli/rpc.py
  行式前端（改造）    Textual 前端（新）       JSONL（未来 Tauri）
```

### 5.1 app 层接口草案

```python
# views.py
@dataclass
class MessageView:      kind: Literal["user","assistant","tool"]; text: str; thinking: str|None
@dataclass
class ToolCallView:     call_id: str; name: str; args_preview: str; status: Literal["running","ok","error"]
                        result_text: str; duration_ms: int|None
@dataclass
class RunStatus:        model_id: str; thinking: str|None; running: bool
                        input_tokens: int; output_tokens: int; cache_read: int; cost: float
                        context_used: int; context_window: int
                        follow_ups: int; steering: int

# runtime.py（前端无关的装配层，REPL/TUI/RPC 共用）
class Runtime:
    @classmethod
    def bootstrap(cls, *, model_id: str | None, resume: Path | None,
                  extensions: list[str], mcp: bool, skills: bool,
                  templates: bool, gate: PermissionGate) -> "Runtime"
    @property agent(self) -> Agent
    @property session(self) -> Session
    @property mcp(self) -> McpManager | None
    async def switch_session(self, path: Path | None) -> None   # 重建 Agent，见 §7.2
    async def switch_model(self, model_id: str) -> SwitchResult  # 见 §7.3
    async def refresh_mcp(self) -> None        # 复用现有 retry + 工具热替换
    async def close(self) -> None              # MCP/扩展清理

# chat.py
class ChatController:
    def __init__(self, runtime: Runtime, *, on_change: Callable[[], None]) -> None
    async def submit(self, text: str) -> None          # 新 run 或排队/steer
    def queue_follow_up(self, text: str) -> None
    def steer(self, text: str) -> None
    def abort(self) -> None
    def clear(self) -> None
    async def switch_session(self, path: Path | None) -> None  # 运行中禁止
    @property
    def status(self) -> RunStatus
    @property
    def transcript(self) -> list[MessageView | ToolCallView]

# models.py
class ModelManager:
    def entries(self) -> list[ModelEntry]     # id/provider/context/cost/reasoning/vision/current
    def current(self) -> str
    def switch(self, model_id: str) -> None   # 下一轮生效

# sessions.py
class SessionManager:
    def entries(self) -> list[SessionEntry]   # path/created/messages/size/current
    def resume(self, path: Path) -> None
    def new(self) -> None
    def fork(self) -> Path
    def delete(self, path: Path) -> None      # 需二次确认
    def export(self, path: Path, fmt: str) -> Path

# commands.py
@dataclass
class Command:
    name: str            # 不带斜杠
    description: str
    handler: Callable[[CommandContext], Awaitable[CommandResult]]
@dataclass
class CommandResult:
    message: str | None = None       # 显示在对话区
    screen: str | None = None        # "model-picker" / "session-picker" / "help" ...
    quit: bool = False
```

约束：
- app 层只依赖 kernel（`agent`/`session`/`catalog`/`config`），不 import textual/rich。
- 所有改变 UI 的动作通过 `on_change` 回调 + 只读快照驱动；Textual 侧做 diff 渲染。
- 命令的「打开某个界面」用 `screen` 字符串表达，不返回 widget。

### 5.2 公开嵌入 API（给"不要 TUI/GUI"的用户）

REPL 是终端前端，**不是**给程序嵌入用的接口。嵌入用户直接消费 app 层的对象：

```python
# 带电池版（推荐给"我想要一个能用的 agent，UI 自己做"）
from zenagent.app import Runtime, ChatController, PermissionBroker

runtime = await Runtime.bootstrap(
    model="deepseek-flash",   # None → config 默认
    mcp=True, skills=True, templates=True, extensions=[],
    resume=None,              # Path | None
    permission=PermissionBroker.deny_all(),   # 或自定义确认回调
)
chat = ChatController(runtime, on_change=lambda: render(chat.status, chat.transcript))
await chat.submit("explain this repo")
assert chat.status.termination in (None, "stop")
await runtime.close()

# 最小内核版（推荐给"我自己搭 loop/UI"）
from zenagent.agent import Agent
from zenagent.tools import ALL_TOOLS

agent = Agent(model=model, system_prompt="...", tools=list(ALL_TOOLS), api_key=key)
agent.subscribe(lambda event: ...)
await agent.prompt("...")
```

分层与稳定性：

| 层 | 导入路径 | 内容 | 稳定性承诺 |
|---|---|---|---|
| 内核 | `zenagent.agent`、`zenagent.types`、`zenagent.providers` | Agent/AgentTool/事件/适配器 | **稳定**（破坏性改动需 major） |
| 应用 | `zenagent.app`（`Runtime`、`ChatController`、`ModelManager`、`SessionManager`、`CommandRegistry`、`PermissionBroker`） | MCP/skills/extensions/会话/权限/命令 | **v1 期间标 experimental**，TUI 稳定后冻结 |
| 前端 | `zenagent.cli.{repl,tui,rpc,main}` | 人/进程直接使用的壳 | 可自由替换 |

不变量（写进代码评审清单）：

1. `zenagent.app` **不得** import `argparse`、`textual`、`prompt_toolkit`、`rich`；
2. `Runtime.bootstrap(...)` 只接受普通 Python 参数（不接收 `argparse.Namespace`），
   路径/开关全部可注入，便于嵌入和测试；
3. 所有 UI 需要的状态都通过 `ChatController.status` / `.transcript` 快照 + `on_change`
   暴露，前端不读 `Agent` 私有字段；
4. `Runtime.close()` 幂等；`ChatController` 不拥有事件循环；
5. **REPL 只是第一个客户端**：任何 REPL 需要的能力都属于 app 层；如果某逻辑只能写在 REPL
   里，说明 app API 缺东西，要补到 app 层而不是留在前端。

`repl.py` 因此从 777 行降到约 200 行，作为**最小嵌入示例**被文档和 `examples/embed.py` 引用；
TUI、RPC 是另外两个客户端。将来 Tauri 桌面端通过 RPC 消费同一个 app 层，不再重写逻辑。

### 5.3 TUI 层（`zenagent/cli/tui/`）

```
app.py           ZenAgentApp(App)：screen 栈、键位、生命周期
screens/         main.py（唯一主屏）、modals/{model,session,confirm,help}.py
widgets/         status_bar.py、sidebar.py、chat_log.py、message.py、tool_card.py、
                 prompt_input.py、spinner.py
theme.tcss       布局与配色（等宽、无色盲风险、暗色/亮色跟随终端）
run.py           run_tui(...) 入口；构造 app 层、执行 app.run()
```

Textual CSS 布局（示意）：

```
Screen
├─ Header               （可选：标题 + 版本）
├─ Horizontal
│   ├─ Sidebar          （可折叠；宽度 24–32）
│   └─ VerticalScroll
│       └─ ChatLog      （Message / ToolCard 组件流）
├─ StatusBar            （模型 · thinking · tokens · cost · context · 队列 · running）
└─ PromptInput          （多行输入 + 自动补全 + 提示行）
```

## 6. UX 规格（键盘 only）

### 主界面

- 输入区：单行起始，Shift+Enter 换行（Textual TextArea 自带）；Enter 发送。
- 运行中：
  - Enter → follow-up 队列（状态栏显示 `queued 2`，可 Ctrl+Q 清空）
  - Ctrl+S → steer 当前轮
  - Ctrl+C → 中断 run（不退出应用）；输入框为空时连按两次退出
- Esc → 焦点回输入框；`/` 开头触发斜杠命令补全；`@` 触发文件补全（复用现有逻辑）。
- Ctrl+P → Textual 命令面板（列出注册的 Command + 内置命令），支持模糊搜索。
- Ctrl+B → 切换侧边栏（窄终端 <100 列默认隐藏）。
- Ctrl+L → 清屏（只清显示，不清会话）。

### 侧边栏（可折叠，默认宽屏显示）

三段：
1. **Session**：当前文件名、消息数、创建时间；`/sessions` 打开选择器。
2. **Model**：当前模型、context 用量条、本轮花费、thinking 等级；`/model` 打开选择器。
3. **Runtime**：运行状态、follow-up/steer 队列数、MCP servers 数量、可用 tools 数。

### 对话区

- 用户消息：左侧竖线 + 文本。
- Assistant：Markdown 渲染；thinking 默认折叠为 `▸ thinking (1.2k chars)`。
- 工具卡片：默认一行 `▸ read_file src/x.py · ok · 12ms`；Enter 展开参数/结果；
  `edit_file` 展开显示彩色 diff；错误卡片红色，默认展开输出。
- 每轮结束追加 usage 行：`done · 3.2s · in 2.1k / out 180 · cache 1.8k · $0.0003`。
- cache 失效提示：上一轮 cache_read > 0 且本轮骤降 ≥2048 tokens 时插入
  `⚠ prompt cache lost · reprocessed ~2.3k tokens`（数据已有）。

### 模型选择器（ModalScreen）

- ListView：`provider / model id`，右侧显示 context、thinking 支持、vision、价格。
- 当前项标记 `●`；Enter 切换并关闭；Esc 取消；`/` 过滤。
- 底部提示：修改 provider/密钥请编辑 `~/.zenagent/config.toml`（不做内联编辑）。

### 会话选择器（ModalScreen）

- 列表：时间、文件名、消息数、大小、当前标记。
- 键位：Enter 恢复；`n` 新建；`f` fork 当前；`d` 删除（二次确认）；`e` 导出 md；
  `/` 过滤；Esc 关闭。

### 帮助

- `?` 或 `/help` 打开 HelpScreen：键位表 + 所有命令 + 版本。
- `/doctor` 在对话区打印检查结果（复用现有 doctor）。

## 7. 运行中的数据流

```
用户 Enter
  └─ PromptInput.Submitted
       └─ ChatController.submit(text)
            ├─ running? → queue_follow_up(text)
            └─ idle?    → 创建 asyncio task 跑 agent.prompt(text)
AgentEvent 流
  └─ ChatController._on_event → 更新 transcript / status → on_change()
       └─ ZenAgentApp 调度重绘（Textual 组件更新，Markdown.append / ToolCard.update）
run 结束
  └─ ChatController 发下一队列项（如果有）→ on_change()
```

- `on_change` 会被高频调用（text_delta）；Textual 侧合并到下一帧（用 `call_after_refresh`
  或 dirty-flag），避免每个 delta 都触发布局。
- 流式 markdown：assistant 消息用 Textual `Markdown` 组件，delta 累积后按「新行边界」喂给
  `Markdown.append`，避免每字符重建整个文档。

## 8. 权限与错误

- `PermissionBroker` 实现 `before_tool_call`：TUI 下弹 ConfirmScreen（工具名、参数预览、
  Allow / Allow for session / Deny），带超时（默认 120s，超时视为 Deny）。
- headless：沿用现有 `PermissionGate`（拒绝）。
- REPL：沿用现有交互确认。
- 工具/模型错误：卡片显示 is_error，不弹窗、不中断 run。
- 初始化失败（catalog/config 损坏）：TUI 启动前打印错误并退出，退出码 2。

## 9. 测试策略

- **app 层**：fake `StreamFn` + `run_test()` 之外直接单测（无 Textual）：
  队列/steer、状态聚合、模型切换、会话操作（临时目录）、命令注册与结果。
- **TUI 层**：Textual `App.run_test()` pilot：
  - 启动 → 输入文本 → Enter → 断言对话区出现消息与工具卡片（fake stream）。
  - 运行中 Enter → 状态栏队列 +1；run 结束后自动发送。
  - Ctrl+C → 中断，应用不退出。
  - 打开模型选择器 → 选第二项 → 状态栏模型变化。
  - 打开会话选择器 → 断言列表项数；Esc 关闭。
  - 权限弹窗 → Deny → 工具结果 is_error。
  - 窄屏（80 列）→ 侧边栏默认隐藏。
- **回归**：现有 565 测试必须全绿；`-p`/`--json`/RPC 输出不变。
- **真机**：tmux 里跑一遍（滚动、中断、退出恢复、窗口 resize）。

## 10. 迁移与兼容

- `zen`（无参数、TTY）→ TUI。`zen --repl` → 旧行式 REPL（保留，用于管道/习惯/回退）。
- `zen -p`、`--json`、`zen rpc`、`zen export/usage/doctor/evals` 不变。
- `zenagent/cli/repl.py` 改为基于 `ChatController`（行为不变），降低逻辑重复。
- 未来 Tauri：`zen rpc` 已具备 prompt/cancel/status/shutdown；后续按 Tauri 需求补
  `sessions.list`、`models.list`、`models.switch` 等 RPC 方法，复用 app 层。

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Textual 与现有异步/事件模型冲突 | 只在 TUI 层用 Textual 的 async；app 层纯 asyncio；Agent 事件在 app 层消费 |
| 终端大小/宽字符导致布局错乱 | 只支持等宽；窄屏折叠边栏；Textual 负责 wrap；tmux 真机验证 |
| 流式 markdown 性能 | 按行 append；限制重绘频率（每帧最多一次） |
| 权限弹窗阻塞工具执行 | 弹窗 await；无 TTY 回落自动拒绝；超时 Deny |
| scope 膨胀（会话重命名/搜索/多 agent） | 明确列为非目标，v1 不做 |
| Textual 升级破坏 | pin `>=8.2,<9`；UI 测试覆盖关键交互 |

## 12. 我做的决定（用户可推翻）

1. 默认 `zen` 进 TUI，`--repl` 保留旧前端；不做「首次启动询问」。
2. 侧边栏可折叠，宽 <100 列默认隐藏（用户说侧边栏可取舍）。
3. v1 模型管理 = 浏览 + 切换 + 展示能力/价格；不编辑 config、不管理密钥。
4. v1 会话管理 = 列表/恢复/新建/fork/删除/导出；不做重命名/搜索。
5. 权限确认用模态框，提供「本次会话内总是允许」。
6. 不做鼠标、不做动画（仅等待 spinner）。
7. 退出 TUI 时打印一行摘要（session 路径 + 用量），不打印完整 transcript。
8. Textual 固定在 `[cli]` extra，pin `>=8.2,<9`。
9. app 层包名 `zenagent/app/`，禁止 import textual。
10. 先建 app 层（T1）再建 TUI 壳（T2+），保证 REPL/RPC 可先迁移验证。

## 13. 留给 builder 的

- 具体 widget 组合方式（用 Textual 内置 `ListView` 还是自绘）、CSS 细节。
- 键位冲突排查（Textual 默认绑定 vs 我们的绑定）。
- 状态栏信息密度（窄屏显示哪些字段）。
- Markdown 流式实现的精确策略（append 粒度、代码块边界）。
- 测试里 fake stream 的组织方式。
- 任务拆分粒度与顺序（详细计划阶段决定）。

## 14. 规模与里程碑

规模：**项目级**（多天）。估计 6–9 个专注工作日，约 2500–3500 行（含测试）。

里程碑（详细任务在计划文档中展开）：

1. **M1 app 层**：Runtime 装配 + views + ChatController + ModelManager + SessionManager +
   CommandRegistry + 单元测试；REPL 迁移到 app 层（行为不变）；公开 API 定稿 +
   `examples/embed.py` + README 嵌入段。
2. **M2 TUI 骨架**：Textual app + 布局 + 输入 + 流式消息 + status bar；真机可跑。
3. **M3 富交互**：工具卡片、markdown、thinking 折叠、diff、队列/steer/中断、权限弹窗。
4. **M4 管理界面**：模型选择器、会话选择器、帮助、命令面板、侧边栏。
5. **M5 收尾**：主题/CSS、窄屏、退出恢复、文档、tmux 真机验证、评测回归。

## 15. Builder review 决议（2026-10-30）

reviewer 提了 5 个问题；4 个在此定案，1 个留给你（见 §16）。

### 15.1 runtime 装配归 app 层（回答「M1 要不要抽 bootstrap」）

M1 新增 `zenagent/app/runtime.py::Runtime`，把现在内联在 `repl()` 里的装配逻辑抽出：
catalog/config → model → api_key → Agent → ContextGuard → PermissionGate → Session →
MCP 生命周期 → skills/templates 发现 → extensions 加载与 hooks → metrics/tracer。

- REPL 与 TUI 都通过 `Runtime.bootstrap(...)` 构造，行为不变；`repl.py` 只保留输入/输出。
- MCP 的 start/close 由 Runtime 负责；Textual 侧在 `on_mount` 里 `await runtime.start()`，
  在 `on_unmount` 里 `await runtime.close()`（都要在事件循环内，不能是同步构造）。
- 验收：现有 565 测试全绿 **且** `test_repl_logic.py` 无需改动即通过（行为等价），
  新增 Runtime 单测覆盖装配/关闭/会话切换。

### 15.2 会话切换 = 重建 Agent（回答 Q2）

`Agent` 在构造时绑定 model/api_key/session_id/transform_context/before_tool_call，
`replace_messages` 在运行中 raise，因此：

- `resume/new/fork` **重建 Agent**（走同一套 bootstrap），而不是原地 replace。
- **运行中允许切换：先 abort 当前 run，再切换**（omp 的 `AgentSession.switchSession` 就是
  `await this.abort(...)` → flush → 载入目标）。UI 在切换前弹一句确认「中断当前运行？」。
- fork：复制当前会话全部消息到新文件（`fork_at` 需要 1-based user 消息序号；v1 取
  「最后一个 user 消息之前/之后」= 整段复制，语义为「从这里分开」，实现用 `Session.fork_at(len(user_messages))`）。
  fork 后**切到新会话**。
- delete：不直接 unlink，移动到 `~/.zenagent/sessions/.trash/`（同名冲突加时间戳），
  可手动恢复；选择器里二次确认。
- 「本次会话内总是允许」的授权在**切换会话时清空**（安全默认）。
- 切换会话时**恢复该会话保存的模型**（若可用）；不可用则回退到当前模型并在对话区提示
  `model unavailable in this session, kept <id>`（omp 的 `onModelFallback` 模式）。
- auto-compact 自换 session 文件属于 Runtime 行为：TUI 在状态栏提示
  `compacted -> new session`，不弹窗、不打断。

### 15.3 steering 队列：加一个只读 kernel 属性（回答 Q3）

`RunStatus.steering` 需要真实数字。定案：kernel 增加只读 `Agent.pending_steering: int`
（返回 `len(self._steering)`），并在 loop 消费后自然减少；不改事件协议。

- 这是 §2 反目标「TUI 逻辑不进 kernel」的**唯一例外**：只读可观测性，不含 UI 概念。
- M1 内实现 + `tests/test_agent_class.py` 加 1 条测试（steer 后计数 1，run 中被消费后归 0）。

### 15.4 模型切换规则（回答 Q4）

`Runtime.switch_model(model_id) -> SwitchResult`：

```python
@dataclass
class SwitchResult:
    ok: bool
    reason: str | None = None   # "missing key" / "unknown model"
```

- provider 密钥缺失：选择器把该模型标记为不可用并显示原因（`ModelEntry.available=False,
  reason="no API key for provider/model"`，对应 omp 的 `hasConfiguredAuth()`）；`switch_model`
  也返回 `SwitchResult(ok=False, reason=...)`，不等到下一轮报错。
- 切换成功时同步：`agent.model`、`api_key`（`_api_key_for`）、`ContextGuard.context_window`、
  `session_id`（`session_id_for(new_model)` 重算）、thinking（新模型不支持 reasoning → 重置为 `off`，
  否则保留当前等级）。
- 未知模型返回 `ok=False`，不抛异常（UI 层可展示）。

### 15.5 恢复会话的历史渲染（回答 Q5）

`views.messages_to_views(messages)` 把历史转成视图模型：

- 历史工具卡片显示：名字、参数预览、结果文本、`is_error`；`duration_ms` 与 diff 显示 `—`
  （JSONL 没存）。
- 实时工具卡片由事件流填充，字段完整。
- 同一个 `ToolCallView`，`duration_ms: int | None`、`diff: str | None` 均为可选。

## 16. 唯一留给你的问题：TUI v1 的能力范围

现有 REPL 除聊天外还承担：MCP 工具、skills/templates 注入、extensions（工具/命令/hooks）、
图片附件、auto-compact（+ `/compact`）、metrics/trace、`/reload`、`/mcp`、`/skills`、
`/images`、`/theme`、`/hotkeys`、`/import`、`/undo`。

TUI v1 的范围建议如下（默认方案）：

| 能力 | v1 | 说明 |
|---|---|---|
| MCP 工具 + 迟到重连 | ✅ | 否则从 REPL 换过来会"丢工具"；Runtime 负责 |
| skills/templates 注入 | ✅ | `Runtime.bootstrap` 已统一 |
| extensions（工具/命令/hooks） | ✅ | 命令进 Ctrl+P 面板 |
| 图片附件（`@path`/粘贴路径） | ✅（基础） | 不做剪贴板图片读取 |
| auto-compact + 进度提示 | ✅ | 状态栏提示 |
| `/reload` `/mcp` `/skills` `/doctor` | ✅ | 走 CommandRegistry |
| metrics/trace | ✅（沿用参数） | 不影响 UI |
| `/theme` `/hotkeys` `/import` `/undo` | ⏳ v1.1 | REPL 保留，TUI 延后 |

如果你接受这个范围，文档就定稿；如果你想砍（例如 v1 不做图片/compact）或加，告诉我。

### 15.6 omp 的现成对策（决策依据）

reviewer 的问题在 omp 里都有实现，我们直接对齐（不搬代码，借结构）：

| 问题 | omp 的做法 | 我们的对应 |
|---|---|---|
| runtime 装配归属 | `AgentSession` 一个对象统管 models/session/bash/agent；UI 只是控制器；`modes/runtime-init.ts` 负责装配 | `Runtime`（前端无关），REPL/TUI/RPC 共用 |
| 会话切换 | `AgentSession.switchSession(path)`：**先 abort** → flush → 过渡 → 载入；校验目标模型的 key（无 key 抛错）；扩展可取消；可 `keepModel`；模型不可用时回退并提示 | 同左：切换前 abort、恢复会话模型/回退提示、切换后重建 Agent |
| 分支/分叉 | 会话是**树**（entries + parentId + leafId），`branch(id)` 切到某个节点 | v1 用「文件复制 + 切到新文件」；树形会话列为 v1.1 的演进方向 |
| steering 计数 | agent 暴露 `peekSteeringQueue()` / `peekFollowUpQueue()`（只读、不消费） | `Agent.pending_steering` 只读属性（§15.3 的先例） |
| 模型切换 | `setModel(model, role, options) -> {switched}`；`getAvailableModels()` 过滤；`hasConfiguredAuth()` 判可用性；thinking 按模型能力选择 | `Runtime.switch_model -> SwitchResult`；`ModelEntry.available`；thinking 随模型重置/保留 |
| 历史消息渲染 | 会话条目带 `startedAt` 等元数据，可完整重建 | JSONL 只有消息本体 → 历史工具卡降级（duration/diff 显示 `—`） |

omp 还有两个值得记下、但 v1 不做的点：
- **会话树**（branch/leaf）让 fork 变成零拷贝；我们把 fork 做成文件复制，等 v1.1 再考虑树形。
- **`session_before_switch` 扩展钩子**（可取消切换）；我们 v1 不做扩展钩子，但 Runtime 的
  切换方法预留 `before_switch` 回调位，避免以后改接口。
