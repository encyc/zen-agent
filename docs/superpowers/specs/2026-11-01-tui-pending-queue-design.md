# TUI 待发队列与插话设计（2026-11-01）

状态：已实现（分支 `feat/tui-pending-queue`，计划见 `docs/superpowers/plans/2026-11-01-tui-pending-queue.md`）
作者：Lead（与用户讨论后整理）
基线：main `86f32ea`（hand-slip guardrails 之后）

相关：
`zenagent/app/{views.py,chat.py}`、`zenagent/agent/agent.py`、
`zenagent/cli/tui/{app.py,widgets/pending_band.py(新),widgets/sidebar.py,status/segments.py}`、
`zenagent/i18n/{zh.py,en.py}`、`tests/{test_agent.py,test_app_chat.py,test_tui_controls.py,test_tui_layout.py,test_tui_pending_band.py(新)}`、
`docs/feature-map.md`、`CHANGELOG.md`

## 1. 意图

运行中用户输入的文本会进 follow-up 队列，但界面上只有状态栏右端的 `排队 1` 和侧栏
Runtime 的计数：文字从输入框消失后没有任何"它去哪了"的痕迹，也没法在提交前或提交后
选择"让正在跑的这轮马上看到"。本轮给待发消息一个可见的家，并给"插话"一条一次性、
键盘和鼠标都能触发的通道。

用户已定的语义：

- 运行中按 Enter 默认**排队**：本轮结束后发送，可撤回（Alt+↑ 取回、Ctrl+X 清空）。
- **插话**（steer）= 不打断本轮；当前工具轮结束、下一次模型请求前注入，模型必须马上
  回应。默认不选，由用户显式触发。
- 不做"中断重发"（abort-and-resend）。

顺带修掉现状里两个静默行为：run 结束时未送达的插话会一直挂着（状态栏显示"插话 1"
但什么都不发生）；而下一次提交时 loop 起始会把它们**塞在新用户消息之后**注入，顺序
和意图都不对。

## 2. 目标 / 非目标

**目标**

1. 输入框正上方加"待发区"：分 `排队` / `插话` 两组，每条一行、截断、超限折叠；空时
   整块隐藏。
2. 插话入口：提交时 Ctrl+S；排队中的文本行点击升格为插话；键盘等价路径 Ctrl+S
   （输入框为空时升格最新一条排队消息）。
3. 补两个洞：run 正常结束（`stop`）时仍未送达的插话 → 自动继续一轮，在下一个边界
   注入；异常结束/中断时把未送达插话退回排队区（不静默滞留、不被塞进下一次提交）。
4. 现有计数/快捷键/状态栏/侧栏语义保持：`排队 N · 插话 N`、Alt+↑、Ctrl+X、Esc 中断。
5. i18n 中英双语；离线测试覆盖。

**非目标**

- 待发队列持久化到会话文件或 resume 后恢复。
- 逐条编辑（取回输入框后可改，不在待发区就地编辑）。
- 队列重排、优先级、部分发送。
- 工具级可打断（interruptible tools）、live steering、provider 内注入。
- transcript 里的灰色 pending 气泡（消息未真正进入对话，不伪装成已发出）。
- REPL 与 headless 前端的行为变更（REPL 现有的"运行中输入即 steer"保持）。

**反目标**

- 每次中途 Enter 弹窗让用户二选一。
- 把待发队列塞进内核当第二事实源（见 §5 C）。
- `zenagent/app/**` import textual/rich。
- 待发区挤掉输入行或无限增高。

## 3. 约束

- 基线 main `86f32ea`；实现从 main 建工作树，合并回 main。
- app 层 UI 无关；Textual 只在 `[cli]` extra。
- 底部输入行贴底、窄屏规则（`NARROW_WIDTH = 100`）不变；待发区最多 6 条消息行，
  超出折叠。
- 离线测试 `bash scripts/check.sh` 为闸门；无新依赖。
- 不用 Ctrl+Enter：带修饰的 Enter 在部分终端（Windows Terminal）不转发（omp 研究
  结论），复用已有的 Ctrl+S。

## 4. 方案

### 4.1 app 层数据模型

`zenagent/app/views.py`：

```python
PendingLane = Literal["queue", "steer"]

@dataclass(frozen=True)
class PendingMessage:
    id: int              # ChatController 内递增，升格/点击的稳定标识
    lane: PendingLane
    text: str
    images: int = 0      # 附带图片数；插话通道只收文本（见 4.4）
    created_at: float = 0.0
```

`RunStatus` 增 `pending: list[PendingMessage] = field(default_factory=list)`；
`follow_ups` / `steering` 两个计数保留（状态栏、侧栏、既有测试不受影响）。

`ChatController`：

- 内部待发改成单一列表 `_pending: list[_PendingItem]`（`_PendingItem` 持有
  `PendingMessage` + 实际 `ImageContent` 列表）；对外快照 `status.pending`。
- `queue_follow_up(text, images=None)`：追加 `lane="queue"`；`images=None` 时照旧吃掉
  `_attachments`。
- `steer(text)`：`agent.is_streaming` 为真时追加 `lane="steer"` 并调 `agent.steer(text)`；
  否则退化为 `queue_follow_up`（压缩期/刚结束的竞态不产生幽灵插话）。
- `promote(pending_id) -> "ok" | "blocked_images" | "not_running" | "missing"`：**只对
  `lane == "queue"` 的条目生效**——改为 steer lane（顺序保持）+ `agent.steer(text)`；
  条目带图片返回 `blocked_images`；agent 已不 streaming 返回 `not_running`；插话条目
  或找不到 id 返回 `missing`（插话行不进入点击映射，避免二次 `agent.steer` 产生重复
  用户消息）。
- `dequeue_follow_up()`：仍取**最后一条排队**条目，文本返回、图片放回 `_attachments`。
- `clear_pending()`：清空两条 lane；steer lane 先 `agent.take_steering()` 防止后续静默
  注入。`clear_follow_ups()` 保留为兼容别名。
- 待发生命周期：任何替换/回退对话的操作（`/clear`、`/new`、会话切换/resume、`/undo`、
  `/fork`、`/retry`）都清空两条 lane，有内容被丢弃时经现有 `_notices`
  （`TranscriptNotice`）留一条提示。理由：旧会话的排队文本不能被发进新会话；
  `agent.reset()` 清掉 `_steering` 后，镜像若不清理会变成点不掉的死行。挂载点放在
  `ChatController.clear()` 与 `reload_transcript()`（会话切换/回退都会走后者），
  `/retry` 走 `prepare_retry` 后的同一条重建路径。
- 镜像回收：`_on_event` 每次处理事件后，若 `agent.pending_steering == 0` 则删除全部
  steer lane 条目。loop 在注入前已清空 agent 队列，事件顺序保证同一 tick 内无竞态；
  `_drain_steering` 把多条合成一条 user 消息，因此多条插话会同时从待发区消失、以一条
  合并消息进入 transcript。
- run 结束（`_run` 的 finally）分三步：
  1. **未送达插话的归置**：`termination == "stop"` 且 `agent.pending_steering > 0`
     → 起"延续运行" `_run(None)`（loop 起始注入 steering，随即请求模型；`continue_`
     抛错即空 transcript 时退化为排队条目）。其他非 aborted 结局（`error`/`no_stream`/
     `tool_terminate`/`max_turns`/`max_cost`）用 `agent.take_steering()` 把未送达插话
     **降级为排队条目**并让控制器回到空闲——**不自动起下一轮**，错误横幅留在屏上、
     条目留在待发区，由用户决定何时重跑（评审修正：原稿"再走现有排队语义"会立刻续跑
     并清空横幅，与 §7.1 冲突，以 §7.1 为准）。`aborted` 同样降级为排队，不自动运行。
  2. **自动压缩**：仅当两条 lane 都为空时执行；有待发（排队或插话）就跳过，让延续运行/
     排队轮次跑在未压缩的转录上（把现有"排队优先压缩"的不变量扩展到插话）。
  3. **续跑**：延续运行优先；否则**仅当 `termination == "stop"`** 时取排队第一条
     `_run(text, images)`，其他结局停在空闲（待发条目可见，等用户下一轮再处理）。
     延续运行会在 loop 起始排空 steering，一批插话最多触发一次延续；用户在延续期间
     继续插话时按同一规则重复即可，无需额外上限。
- 自动压缩期间提交会进排队（现状）；此时没有进行中的 run，插话会退化为排队（见上），
  不存在"压缩中插话"。

### 4.2 内核最小改动

`zenagent/agent/agent.py` 新增：

```python
def take_steering(self) -> list[str]:
    """Remove and return queued steering input (abort / clear cleanup)."""
    texts = self._steering
    self._steering = []
    return texts
```

仅此一处；`zenagent/agent/loop.py` 不改——边界注入点已存在（每轮工具结束后、下一轮
请求前；以及 run 起始，用于延续运行）。

### 4.3 TUI 待发区

位置：`#dock` 内 `StatusBand` 与 `#prompt-row` 之间（贴着输入框），初始 `display=False`。

新 widget `zenagent/cli/tui/widgets/pending_band.py`：`PendingBand(Static)`，
id `#pending-band`，`height: auto; background: $surface; padding: 0 1`。

渲染（宽度由 app 的 `_band_width()` 传入；文本用 `clip_cells` 截断，换行折成 ` ↵ `）：

```
排队 · 2
  1. 把 A 文件里的 handler 改成异步…
  2. 顺便跑一下测试
插话 · 1
  1. 停，不是那个文件
└ alt+↑ 取回 · ctrl+x 清空 · 点击行插话
```

- 组头 `排队 · N` / `插话 · N` 用 accent；条目行 `  1. <text>` muted；带图片的行尾追加
  ` · 图 N`；提示行 dim。
- 最多 6 条消息行，超出折叠为 `… 还有 N 条`；提示行始终最后一行。
- 空 lane 不渲染组头；两 lane 都空 → `display=False`、`update("")`。
- `set_pending(pending, width)` 重建内容并记录 `{内容行号: pending id}` 供点击；
  `on_click(event)` 用 `event.y` 映射（Static 无纵向 padding，行号即内容行）；
  点中**排队**条目 → app 的 promote 回调。组头、插话条目、提示行不进入点击映射。

app 接线（`zenagent/cli/tui/app.py`）：

- `compose()`：`StatusBand()` 之后 `yield PendingBand()`。
- `_refresh_status()`：`for band in self.query(PendingBand): band.set_pending(status.pending, width)`。
- `promote_pending(id)`：调 `chat.promote(id)`；`ok` → `notify(t("tui.queue.promoted"))`；
  `blocked_images` → `notify(t("tui.queue.blocked_images"))`；其余静默；再
  `_refresh_status()`。
- `action_steer`（Ctrl+S）：输入框有文本 → 现有 steer；输入框为空且 run 中且有排队条目
  → `promote(最新排队条目)`；否则 no-op。
- `action_clear_queue`（Ctrl+X）→ `chat.clear_pending()`；清掉任何条目时 toast
  `tui.queue.cleared`。
- 状态栏 `queued` 段与侧栏 Runtime 行原样保留：它们回答"有没有"，待发区回答"是什么"。

### 4.4 边界与不变量

- 带图片的排队条目不支持升格（steer 通道只收文本）：点击/Ctrl+S 时提示并保持排队。
- 多行文本：换行折成 ` ↵ ` 后单行渲染，避免撑高。
- 窄屏/侧栏开合：待发区宽度跟随 `_band_width()`；`on_resize` 已走 `_refresh_status()`。
- 子代理视图打开时待发区照常显示（父运行仍在跑）。
- idle 时排队条目不会自动运行（现状）；Esc 中断后保留在队列里，下一次运行结束后再
  处理（现状语义），期间在待发区可见。
- 超过 6 条的排队仍完整保留在数据结构里，只是折叠显示；Alt+↑ / Ctrl+X 作用于全部。

### 4.5 i18n（zh/en 同 key 集）

- `tui.queue.title.queued`：排队 / Queued
- `tui.queue.title.steer`：插话 / Steering
- `tui.queue.hint`：`└ alt+↑ 取回 · ctrl+x 清空 · 点击行插话` / `└ alt+↑ edit · ctrl+x clear · click a row to steer`
- `tui.queue.more`：`… 还有 {n} 条` / `… {n} more`
- `tui.queue.images`：`图 {n}` / `img {n}`
- `tui.queue.promoted`：`已转为插话，下一步立即送达` / `Now steering; delivered at the next step`
- `tui.queue.blocked_images`：`带图片的排队消息不能插话，本轮结束后发送` / `A queued message with images can't steer; it sends after this run`
- `tui.queue.cleared`：`已清空待发消息` / `Pending messages cleared`
- `tui.queue.dropped`：`已丢弃 {n} 条待发消息（会话已切换）` / `Dropped {n} pending messages (session changed)`
- 现有 `tui.status.queued/steering`、`tui.sidebar.queue` 不动。

### 4.6 测试

- `tests/test_agent.py`：`take_steering` 返回顺序、清空、空队列 no-op。
- `tests/test_app_chat.py` / 新增 `tests/test_app_pending.py`：待发快照 lane/text/顺序；
  steer 镜像在注入后消失；promote 成功、带图片拒绝、not_running；dequeue/clear；
  run 结束滞留插话自动延续一轮且 transcript 出现该消息；非 `stop` 结局（`error`）把
  插话降级为排队、**不续跑且错误横幅保留**（TUI 级断言）；`aborted` 降级为排队但不
  自动跑；替换会话的操作（命令级与 session picker 级）清空待发并留 notice；压缩保留
  待发；有待发时跳过自动压缩。
- `tests/test_tui_pending_band.py`（新）：分组/顺序/截断/折叠/空隐藏/宽度；点击排队行
  触发 promote；组头、插话行、提示行点击无效。
- `tests/test_tui_layout.py`、`tests/test_tui_controls.py`：中途提交后待发区可见；
  Ctrl+S 空输入升格最新排队；Ctrl+X 清空两条 lane。
- i18n key 集/占位符测试自动覆盖。

### 4.7 文档与变更记录

- `docs/feature-map.md`：TUI dock 一节补待发区与插话语义。
- `CHANGELOG.md` Unreleased 增条目。
- 不发版；版本号留到用户决定发版时。

## 5. 方案取舍

- **A 只做计数条（最小）**：输入框上方只读一行计数 + 键位提示，不显示文本、不能逐条
  操作。成本半天，但看不见"排了什么"、插队要两步（取回再 Ctrl+S），不满足"看得见 +
  能选"。
- **B 带文本的待发队列（选定）**：app 层统一待发模型 + 新 widget，内核只加
  `take_steering()`。改动集中在视图层与 TUI，一次解决可见性与选择。
- **C 内核级收件箱**：把插话做成 Agent 的一等队列、带 id、发事件，能精确追踪每条；
  但排队属于 run 语义、仍在 app 层，两套要合并渲染，收益只在将来做"可打断工具"时
  兑现，本次属于过度设计。

## 6. 留给实现者的判断

- 待发区 CSS 间距/颜色细节（沿用 `palette`，不新增颜色 token）。
- 折叠阈值（暂定 6 行）与"更多"行的措辞。
- 点击行号映射的实现细节（Static 的 padding/行高），以测试行为为准。
- `promote` 返回值的具体命名；toast 文案微调。
- `clear_follow_ups` 兼容别名是否保留（建议保留，供测试与将来前端）。

## 7. 评审后由 Lead 补定的判断（用户可推翻）

fresh reviewer 读稿后提出四处需要拍板的地方，以下是已并入正文的决定：

1. **延续与续跑都只对正常结束（`stop`）触发**。`error`/`no_stream`/`tool_terminate`/
   `max_turns`/`max_cost` 一律把未送达插话降级为排队并回到空闲：不自动重试、不吞掉
   错误横幅、也不让 `max_*` 天花板被悄悄重置（评审确认：这条与 §4.1 原稿的"走现有
   排队语义"矛盾，实现已按本条修正）。
2. **待发不跨会话**：`/clear`、`/new`、会话切换/resume、`/undo`、`/fork`、`/retry`
   清空两条 lane，丢弃时经 `TranscriptNotice` 提示。避免旧会话文本发进新会话，也避免
   `agent.reset()` 后的 steer 死行。
3. **有待发（排队或插话）时跳过自动压缩**；延续运行先跑。这样"下一步立即送达"的
   承诺不会排在一次摘要往返之后，也与现有"排队优先压缩"的不变量一致。
4. **插话行只读**：只有排队行可点击升格；`promote` 收到插话 id 时按 `missing` 处理，
   杜绝二次 `agent.steer` 造成重复用户消息。

