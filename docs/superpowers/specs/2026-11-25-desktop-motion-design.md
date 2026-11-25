# 桌面端动效与滚动设计（2026-11-25）

> 起因：评估 shadcn/ui 能否用在 `desktop/`。结论是**不引入**（TSX + CSS Modules +
> token 门禁 + Tailwind 缺失，价值观「源码归你」对已经是全源码的项目等于零），
> 但它有三个机制值得照搬做法：状态驱动的进入/退出动画、统一的动效 token 阶梯、
> 对话滚动的「贴底跟随」状态机（`message-scroller`）。本文件记录这三件事的决定，
> 实施步骤见 [plans/2026-11-25-desktop-motion.md](../plans/2026-11-25-desktop-motion.md)。

## 约束（决定方案的硬边界）

1. **不新增运行时依赖。** `desktop/package.json` 的 dependencies 不动：没有
   Tailwind、没有 Radix/Base UI/React Aria、没有 framer-motion。动效只能用 CSS +
   少量 React 状态。
2. **颜色/时长都不许裸写。** `scripts/check-style-tokens.mjs` 已禁止组件样式表出现
   颜色字面量；本次把**时长与缓动**也纳入同一纪律（新守卫 `scripts/check-style-motion.mjs`）。
3. **全局 reduced-motion 兜底存在。** `styles/base.css` 的
   `@media (prefers-reduced-motion: reduce)` 把 `animation-duration` /
   `transition-duration` 压到 `0.01ms`。因此**新代码不写按文件的 reduced-motion
   覆盖**，依赖这一个兜底；但兜底只压缩时长，不改变「节点什么时候卸载」，
   所以退场动画的实现必须能在 0.01ms 下立刻完成（见下文的 `animationend` 依赖）。
4. **文件预算 800 行**（`scripts/check_architecture.py`），`desktop/src` 在扫描范围。
   `ChatView.tsx` 现在 497 行，只减不增：新的滚动逻辑落在 `state/` 下的纯函数模块。
5. **测试归属**：纯函数与样式契约走 `desktop/tests/*.test.ts`（`node --test`，
   Node 25 可直接跑 `.ts`）；**不写 jsdom 组件测试**——项目没有 jsdom 依赖，
   组件行为一律交给 `desktop/e2e/*.spec.ts`（Playwright，已装 chromium）。

## 决定 1：退场动画由一个 presence 状态机驱动

**现状。** 组件在开关上是「条件渲染即卸载」：`CommandPalette`、`Notifications`
popover、`NoticeBanner`、`ApprovalPanel`、`ContextMeter` 卡片、`RetryRow` 详情。
进入有一半有动画（`contextCardIn`、`settings-enter`），**退出一律瞬切**。

**决定。** 新增 `src/ui/presence.ts`（纯函数状态机 + 一个薄 hook）：

```ts
export type PresencePhase = 'closed' | 'entering' | 'open' | 'exiting'
export function nextPhase(phase: PresencePhase, wantOpen: boolean): PresencePhase
export function isMounted(phase: PresencePhase): boolean   // 非 closed 即挂载
export function presenceState(phase: PresencePhase): 'open' | 'closed'  // 写进 data-state
export function usePresence(open: boolean, leavingMs: number): { mounted: boolean; state: 'open' | 'closed' }
```

相位规则：

| 当前 | `wantOpen` | 下一相位 | 含义 |
|---|---|---|---|
| `closed` | true | `entering` | 第一次提交就挂载，CSS 从 `closed` 样式过渡到 `open` |
| `entering` | true | `open` | 一帧后落定，之后的样式变化不再播放进入动画 |
| `open` | false | `exiting` | 保留挂载，`data-state="closed"` 让 CSS 播退场 |
| `exiting` | true | `entering` | 退场中被打断，反向过渡（CSS transition 天然可中断） |
| `exiting` | false | （等定时器）`closed` | `leavingMs` 后卸载 |
| `entering`/`open` | false 但已 `closed` | — | 不变式：`closed` 只能由定时器到达 |

**为什么是 CSS transition 而不是 @keyframes。** transition 从当前计算值出发，开关
抖动时可以反向；keyframes 会跳回起点。这正是 shadcn 的动画基质。

**为什么用定时器而不是 `animationend`。** 兜底的 reduced-motion 把时长压到
`0.01ms`，`animationend` 在极端情况下可能早于/晚于 React 的提交；定时器由
`leavingMs` 一个数字控制，`prefers-reduced-motion` 时传 `0` 即可瞬时卸载，
行为可测（纯函数 + fake timer），不依赖事件顺序。

**样式契约。** 新增 `src/ui/motion.module.css`，只放两个类：

```css
.leaving { /* 交给调用方的 transition 决定属性 */ }
```

实际做法更简单：调用方自己的样式表用 `[data-state='closed']` 写目标态，
`.root` 上写 `transition: opacity/transform`。**共享的只有时长 token**，
不共享属性列表——浮层从下方浮入、内联卡片从上方压下，属性本来就不同。

**接入范围（6 处）。**

| 组件 | 进入 | 退出 | 备注 |
|---|---|---|---|
| `CommandPalette` | backdrop 淡入 + panel 上移 4px | 反向，`leavingMs=120` | 全屏遮罩，退出时不能吃点击（`pointer-events: none` 于 `closed`） |
| `Notifications` popover | 上移 4px | 反向 | 焦点已在外部点击时移走，卸载不再影响焦点 |
| `NoticeBanner` | 下沉 2px | 反向 | `role="alert"` 保留在 DOM 期间保持可读 |
| `ApprovalPanel` | 上移 6px | 反向 | **裁决点见下** |
| `ContextMeter` 卡片 | 已有 `contextCardIn`，改为 transition | 反向 | 悬停 120ms 宽限期已有，退出动画吃掉的是这 120ms 之后的瞬切 |
| `RetryRow` 详情 | 高度 `grid-template-rows: 0fr → 1fr` | 反向 | 已有 `data-open` 样式钩子，替换成 `data-state` |

**ApprovalPanel 的裁决。** 审批卡是「运行暂停在这里」的安全面。退场动画期间
按钮仍在 DOM 里，用户可能在退场已开始后又点一次 Allow。因此：`answer()` 已有
`answered` ref 守卫（幂等），且退场期间容器加 `pointer-events: none`，
键盘路径由 `answered.current` 提前 return。**不做「退场期间可继续点」**——
那会让一次审批发两次 RPC。

## 决定 2：动效 token 收成一条阶梯

**现状。** 两套并行：

| 体系 | token | 用量 |
|---|---|---|
| 旧 | `--ds-ease-in-out` / `--ds-transition-duration{,-fast,-slow}` | 22 / 23 处 |
| 新 | `--ds-ease-out` / `--ds-ease-spring` / `--ds-motion{,-fast}` | 72 / 0 / 72 |

另有 **29 处裸写**（`transition: opacity 120ms ease` 一类）和 `--ds-transition-duration-slow`
**零引用**。

**决定。** 单一阶梯，保留两套名字但只让一套是「真值」：

```css
/* :root —— 老名字变成别名（不删，避免 22 处调用点一次性爆掉） */
--ds-transition-duration-fast: var(--ds-motion-fast);   /* 140ms */
--ds-transition-duration: var(--ds-motion);             /* 200ms */
--ds-transition-duration-slow: var(--ds-motion-slow);   /* 320ms */
--ds-ease-in-out: var(--ds-ease-out);

/* body —— 真值 + 新增两档 */
--ds-motion-fast: 140ms;
--ds-motion: 200ms;
--ds-motion-slow: 320ms;   /* 新增：整屏切换/页面进入 */
--ds-ease-out: cubic-bezier(0.22, 1, 0.36, 1);
--ds-ease-spring: cubic-bezier(0.34, 1.3, 0.64, 1);
```

注意 `--ds-motion*` 定义在 `body`、`--ds-transition-duration*` 在 `:root`，所以别名
必须同层：把别名也放进 `body`，否则 `:root` 上的 `var(--ds-motion-fast)` 解析为空，
`transition` 整条声明失效——**这是本次最容易踩的坑，B1 的测试专门钉它**。

**裸写清零。** 29 处按属性归类：

- `opacity` / `color` / `background*` / `border-color` / `box-shadow` → `--ds-motion-fast`
- `transform` → `--ds-motion-fast`（90ms 的按压反馈提到 140ms，观感更稳）
- `width` / `height` / `grid-template-*` / `padding` → `--ds-motion`
- 缓动一律 `--ds-ease-out`；只有需要轻微回弹（图标旋转、卡片落位）才用 `--ds-ease-spring`

**守卫。** `scripts/check-style-motion.mjs`：扫 `desktop/src/**/*.module.css` 的
`transition` / `animation` 声明，任何 `\d+(\.\d+)?m?s` 或裸 `ease`/`linear`/`cubic-bezier()`
都算违规；`ease` 只允许出现在 `steps()`/`linear()` 之外——即**只允许 token**。
`infinite` 的循环动画（spin/shimmer/pulse）必须写 `linear` 或 `ease-in-out`，
因此守卫的豁免是：`animation` 声明里的缓动关键字放行（循环动效的节奏靠
`animation-timing-function` 的词法表达，不值得为此新增 token），但**时长仍然必须 token 化**。

## 决定 3：对话滚动收成一个可测的状态机

**现状（`ChatView.tsx`）。** 逻辑内联在 3 个 `useEffect` + 4 个 ref 里，共约 120 行：

- `follow` = `distance < 48`（**硬编码**）；
- `showJump` = `distance > 160`（**硬编码**，且没有「新消息浮标」——用户上滚后
  只知道下面有东西，不知道有几条）；
- `scrollTop <= 8` 触发顶部翻页；
- 滚轮/键盘/程序化滚动**没有区分**：程序化 `scrollTo` 也会走 `onScroll`，
  于是「跳到底部」这个动作自身会先把 `follow` 置 false 再置 true，一次抖动；
- 首次进入会话时依赖 `initialCommit` + `useLayoutEffect` 落到尾部，没有
  「用户上次读到哪」的概念。

**决定。** 抽出 `src/state/scrollFollow.ts`（纯函数，无 DOM，无 React）：

```ts
export type ScrollMode = 'following' | 'free' | 'settling'

export interface ScrollInput {
  scrollTop: number
  scrollHeight: number
  clientHeight: number
  lastScrollTop: number
  programmatic: boolean   // 本次 scroll 事件是由本组件发起的
}

export interface ScrollSnapshot {
  mode: ScrollMode
  showJump: boolean
  atTop: boolean
  /** 用户滚离底部之后，下方新增的节点数（浮标上的数字）。 */
  unseen: number
}

export function nextScrollSnapshot(prev: ScrollSnapshot, input: ScrollInput): ScrollSnapshot
export function countUnseen(nodes: ChatNode[], lastSeenIndex: number): number
```

规则（每条都能对着纯函数写断言）：

1. `programmatic: true` 的事件只更新 `atTop`，**不改变 `mode`**——修复抖动。
2. `distance = scrollHeight - scrollTop - clientHeight`；`distance <= EDGE(8)` →
   `following`，`showJump=false`，`unseen=0`。
3. `scrollTop < lastScrollTop`（真的往上滚了）且 `distance > EDGE` → `free`。
4. `free` 状态下 `distance > JUMP_THRESHOLD(160)` → `showJump=true`；回到
   `following` 时清掉。
5. `unseen` 由调用方在有新节点时用 `countUnseen` 更新；`following` 下恒为 0，
   并且**只有在 `following` 时**才会把 `lastSeenIndex` 推到尾部。

**为什么移到 `state/` 而不是留在 `ui/chat/`：** 现有约定就是可测逻辑放 `state/`
（`turns.ts`、`transcriptWindow.ts`、`palette.ts` 都是），`ui/` 只留渲染。这也让
`ChatView.tsx` 只减不增。

**浮标（`showJump` 按钮）的语义变化：** 从「有东西在下面」变成「有东西在下面 +
几条用户消息」。`unseen > 0` 时角标显示数字（>9 显示 `9+`）；`unseen === 0`
但 `showJump` 时是纯图标（工具/助手内容在增长）。**新增 i18n key**
`chat.jumpUnseen`（`下方 {count} 条新消息` / `{count} new messages below`）。

**不做的部分（明确划界）：**

- 不做「按 turn 锚定」（`anchored-to-message`）——那是给「点击某条消息后，流式
  内容不能把视角甩走」用的，`scrollToRow` 现在直接 `setFollow(false)`，行为已经是
  用户想要的；为它引入 tail spacer 是另一个量级的改动。
- 不做 `defaultScrollPosition: 'last-anchor'` 的持久化——需要往 settings 里加
  每会话的阅读位置，跨端（TUI/GUI）一致性要单独设计。
- 不做 IntersectionObserver 可见性追踪——`ChatView` 已经在用
  `getBoundingClientRect` 算 minimap 高亮行，窗口化又保证了挂载行数量有上界
  （60–100），没有性能动机。

## 测试策略

| 层 | 文件 | 钉住什么 |
|---|---|---|
| 纯函数单测 | `tests/presence.test.ts` | 6 条相位规则 + `closed` 不变式 + 打断 |
| 纯函数单测 | `tests/scrollFollow.test.ts` | 上面 5 条规则逐条 + 程序化滚动不抖 |
| 纯函数单测 | `tests/motionTokens.test.ts` | 别名同层 + 阶梯值 + 零裸写的快照 |
| 契约单测 | `tests/styleMotion.test.ts` | 守卫匹配器本身（正例/反例/豁免） |
| e2e | `e2e/motion.spec.ts` | 面板/通知/横幅/审批的退出动画期间节点仍在、之后消失、期间不响应点击 |
| e2e | `e2e/follow.spec.ts` | 上滚 → 浮标出现并计数 → 回到底部 → 浮标消失；程序化跳转不闪 |

**为什么 e2e 用「时长 + 轮询」而不是断言 CSS 计算值：** `transition` 的
`getComputedStyle` 在动画中不断变化，断不稳。可断的是**节点存在性**：
点击关闭后立刻 `toHaveCount(1)`（还在 DOM），`leavingMs` 之后 `toHaveCount(0)`。
这恰好就是退场动画的可观测契约。
