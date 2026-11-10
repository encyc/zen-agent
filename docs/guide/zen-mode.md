# Zen 模式：单会话统筹整个项目

Zen 模式是 zen-agent 的第二种交互形态：每个项目只保留**一个交互会话**
（Router），项目下其余会话成为持有任务焦点的 worker，由 Router 异步派工、
收报告、汇总进度。你只面对一个对话窗口，多会话的上下文切换交给系统。

设计依据见
[`docs/superpowers/specs/2026-11-10-zen-mode-design.md`](../superpowers/specs/2026-11-10-zen-mode-design.md)
（参考 DeepSeek dsh super-agent 原型的双层架构）。

## 打开与关闭

sidebar 右上角（⌘B 按钮旁）的 ✨ 按钮在 **Coding ↔ Zen** 之间切换；选择
持久化在 `zen.ui.mode`，重启后自动恢复。切换要求当前会话空闲（正在运行时
按钮会失败并回退，`zen_busy`）。

- **进入 Zen**：当前会话切换为项目的 Router 会话（有历史则复用上次的
  Router，延续全局记忆；没有则新建），其余会话进入名册。
- **退出 Zen**：恢复进入前的会话；在跑的 worker 会被停止并标记受阻。

## 在 Router 会话里工作

Router 拥有普通会话的全部能力，外加六个 fleet 工具：

| 工具 | 作用 |
|---|---|
| `fleet_list` | 列出项目名册：Router + 全部 worker（焦点、状态、最近报告） |
| `fleet_dispatch` | 把一个具体任务派给某个 worker，立即返回 |
| `fleet_status` | 查单个（或全部）worker 的状态与最近报告 |
| `fleet_stop` | 停止某个正在运行的 worker |
| `fleet_focus` | 设定/修改 worker 的任务焦点 |
| `fleet_spawn` | 新建一个带焦点的 worker（可带首个任务直接派工） |

Router 的纪律（系统提示词强制）：只做中枢，不亲自实现；派工后立即结束回合，
**结果会以 `[zen:worker …]` 消息自动回到会话**，永远不要等待或轮询。

## Sidebar 名册与 Worker 抽屉

Zen 开启时，会话区渲染当前项目的名册：

- **Router 行**：当前交互会话（高亮徽标）。
- **Worker 行**：状态点（绿=执行中/排队，红=受阻/失败，灰=空闲/待命）+
  焦点标签 + 状态词。点击打开**只读抽屉**查看该 worker 的 transcript；
  输入永远只在 Router 会话。

状态词表：`idle 空闲 · queued 排队 · working 执行中 · waiting 待命 ·
blocked 受阻 · failed 失败`。

## 持久化与边界

- 名册（谁 Router、谁的焦点是什么）存在项目 sessions 目录的 `.zen.json`
  侧车里，与会话文件同寿命；worker 的 transcript 就是普通 session JSONL，
  退出 Zen 后仍可按普通会话浏览。
- fleet 并发上限默认 3（`config.toml [zen] max_concurrent` 预留）；超过的
  派工排队。
- Zen 是**每项目**的：切换项目后由前端按持久化开关自动重新启用；一个项目
  的 fleet 不影响另一个。
