# 早上好 — 多会话分屏 + 拖出独立窗口 已完成

Branch `feature/multi-session-panes`（在 worktree
`.worktrees/multi-session-panes`，基于 main @ e3aaf2e），全部验证绿灯，**可运行的
App 已打包好**。

## 一分钟速览

- **编程模式分屏**：侧栏任意会话行 → `⋮` 菜单 → 「在旁边打开」→ 旁边多出一个
  会话面板；最多 3 个面板并排（含当前会话），各自实时流式渲染（助手增量、工具
  卡、diff 全部照常）。点击某个面板即聚焦（成为当前会话，顶栏/侧栏/状态轮询跟随）。
- **每个面板有自己的输入框**：消息直达该面板的会话，`/命令` 也可以；其他面板的
  会话照常运行、互不打断。Esc 停止对应面板，头部有运行点、停止、拖出、关闭按钮。
- **拖出独立窗口**：拖住面板头拖出窗口外（或点「拖出为独立窗口」按钮）→ 弹出一个
  独立 OS 窗口，继续显示并可继续对话。关闭独立窗口不会停止会话；后台仍在跑，侧栏
  里也还在。所有窗口共用同一个 `zen rpc` 后端进程（绝不出现第二个运行时）。
- 不开分屏时，界面与之前完全一致（单栏 + 完整 composer dock）。

## 怎么试用

```bash
# 方式一：直接打开打包好的 App（自带 zen 运行时）
open /Users/mac/zen-agent/.worktrees/multi-session-panes/desktop/src-tauri/target/debug/bundle/macos/ZenAgent.app
# 或挂载 dmg: desktop/src-tauri/target/debug/bundle/dmg/ZenAgent_0.13.13_aarch64.dmg

# 方式二：开发模式
cd /Users/mac/zen-agent/.worktrees/multi-session-panes/desktop && pnpm tauri dev
```

试用路径：打开 App → 侧栏选一个会话 → `⋮` → 在旁边打开 → 再开第三个 →
拖住任一面板头拖到窗口外 → 独立窗口出现。

## 验证状态（全绿）

| 门 | 结果 |
|---|---|
| Python 全量 pytest | 3187 passed（含新增 5 个会话路由测试）；审查后回归 23 passed |
| 桌面单测 node --test | 481 passed（新增 panes/frameRouter/detach/sessionBoot/clientTag/chatStoreFactory） |
| Playwright e2e | 143 passed（新增 panes.spec.ts 4 条） |
| tsc + vite build | ✓ |
| style tokens lint | ✓ |
| 架构预算门（800 行/文件） | ✓（appStore 反而缩小：1195 → 1046 行） |
| cargo check | ✓ |
| 原生打包 | ✓ ZenAgent.app + dmg（PyInstaller 冒烟通过） |

## 独立审查与修复

全分支由独立审查代理复核（设计文档 Amendments、5 条 Review Focus、跨窗口风险、
测试诚实度）。审出 2 个 Critical、3 个 Important，均已修复并回归：

- 独立窗口"领养"已运行后端时不再失聪（帧监听在领养路径也挂载）——修复前拖出的
  窗口会停在快照。
- 关闭面板回到单栏时，面板内容交还给主视图、注册表清空——修复前会话帧会被
  隐形面板吞掉、界面像卡死。
- 关闭"Enter 发送"设置后，面板也有 ⌘/Ctrl+Enter 与发送按钮。
- 会话被淘汰后的提示词自动恢复重试，不再报错卡死。
- 两处测试诚实度补齐（状态轮询降级、领养路径监听）。

遗留小项（Minor，未修，详见 ledger）：session_invalid 文案、静态面板上的
/retry 恢复、同毫秒拖出窗口标签碰撞、rpc_start 竞态守卫、轮询未错峰等。

## 关键设计（为什么这样建）

内核本来就能一个项目并行跑多个会话（RPC host 的 slot 表，最多 4 个），GUI 从来没
把它露出来。这次的核心改动只有一条线上规则：**请求带上 `session` 标签就路由到持有
该会话的 slot**（没有就拒绝，绝不串话）。前端据此做会话级帧路由 → 每面板独立
transcript store；拖出的窗口复用同一个前端、认领同一后端进程。appStore 只减不增
（帧处理挪进新模块 frameRouter.ts）。

设计文档：`docs/superpowers/specs/2026-11-17-multi-session-panes-design.md`
实现计划：`docs/superpowers/plans/2026-11-18-multi-session-panes.md`

## 已知边界（有意为之，见设计文档 Amendments）

- 分屏面板只开**当前项目**的会话（线上帧按项目过滤）；拖出的独立窗口可以开任意
  项目的会话。
- 面板内 composer 是精简版（无图片/提及/模型选择）；关掉分屏回到完整 dock。
- 一个项目最多 4 个 slot；开满 3 面板 + zen 路由器时再开会话会收到明确提示。
- 空闲面板的 slot 可能被 LRU 淘汰 → 面板自动转静态快照，点「载入会话」恢复。
- 提交分支未合并到 main，等你审阅：`git checkout main && git merge feature/multi-session-panes`
  （或在 worktree 里继续改）。
