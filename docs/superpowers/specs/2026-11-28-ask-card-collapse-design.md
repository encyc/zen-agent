# 提问卡片：更小、更密、可向下折叠（设计）

日期：2026-11-28
状态：待批准
规模：小改动（2 个组件 + 1 个布局位移 + 1 条 e2e）

## 意图与为什么

一次提问出现时，`AskCard` 顶在输入框上方，占掉屏幕下半截。用户要的是两件事：

1. 卡片小一号、信息更密，并带一个向下折叠的按钮，折起来只剩一行状态条。
2. 卡片变高时，**对话内容跟着被推上去，不被卡片盖住**——今天用户想先翻上面的
   对话找上下文，往上滚的时候卡片始终浮在阅读区上，把下半屏的内容压住。

## 现状（读代码得到的，不是猜的）

- `AskCard` 挂在 `InputBar` 里（`desktop/src/ui/composer/InputBar.tsx:272`），
  而 `InputBar` 在 `ComposerDock` 这个**绝对定位的浮层**里（`App.tsx:366`）。
- `ComposerDock` 用 ResizeObserver 把自己的高度写进 `[data-shell-center]` 上的
  `--composer-dock-height`，正文的 `padding-bottom` 与底部渐隐带都用它
  （`ChatView.module.css:18,33`）。
- 所以**卡片的占位今天已经在预留了**：阅读位置在底部（`scrollFollow` 的
  `following`）时，最后一条消息确实坐在卡片上方。
- 出问题的是另一种情况：用户往上滚去读历史，此时 `mode === 'free'`，正文不再
  被钉在底部，内容就滑到卡片底下——正是用户描述的那个不方便。

这条判断决定了方案的选择：把卡片的高度「算得再准一点」是白算，算的是同一件事。

## 目标

- 卡片垂直占用明显变小：题干 16→14px，选项内边距 9/12→7/10，区块间距收紧，
  选项列表上限 340→200px。
- 状态条最右多一个折叠按钮；折叠后只剩一行：图标 + 标题 + `1/3` + 进度点 + 队列数。
- 卡片是**正文流的一部分**：往上滚就把它滚出去，阅读区拿到整个视口的高度；
  停在底部时它坐在最后一条消息下面，内容被推上去而不是被盖住。
- 折叠是**每份提问**的状态，跟着草稿走：切窗口、重挂载、会话快照刷新都不会把
  它弹回展开，也不会把「我折过了」带给下一份提问。

## 非目标

- 不改 `ask` 协议、`askDraft` 的答案语义、resolve 流程。
- 不动审批卡片（`ApprovalPanel`）。
- 不做「记住折叠偏好、下次提问默认折叠」：新提问永远默认展开。
- 不改分栏（`PaneView`）的输入框布局——那边 dock 本来就在流里。

## 三个方案

**A. 卡片搬进正文流（选中）**
`AskCard` 从 `InputBar` 里搬出来，作为 `ChatView` 滚动区里的一行（在 turn 列表
之后、tail 之前）。`InputBar` 不再知道 ask。往上滚时卡片跟着滚出视口，阅读区
恢复整屏；停在底部时卡片在最后一条消息下方，内容被推上去。
- 代价：卡片滚出视口后，界面上没有「还有一题等着答」的提示，需要补一个（见下）。

**B. 留在浮层，把卡片高度并进 `--composer-dock-height`（否）**
这是**今天的行为**：卡片本来就在被测量的 dock 里。再精确一次解决不了读数时被压。
- 为什么否：它只覆盖「停在底部」的场景，而用户的痛点在「往上翻」的场景。

**C. 留在浮层，量高度差再给 scroller 补 `scrollTop`（否）**
- 为什么否：和 `scrollFollow` 的跟随/自由状态机抢同一样东西（`lastScrollTop`
  那条把「内容长高」和「读者向上滚」区分开），必然出现自己抖一下。

## A 的三处着落

### 1. 卡片变小（`AskCard.module.css`）

| 位置 | 现在 | 改为 |
|---|---|---|
| 状态条 padding | `12px 18px 0` | `8px 12px 0` |
| 正文 gap / padding | `12px` / `12px 18px 16px` | `9px` / `8px 12px 12px` |
| 题干字号 / 行高 | 16 / 22 | 14 / 20 |
| 题干最大高 | `min(208px, 24vh)` | `min(160px, 20vh)` |
| 选项内边距 / 间距 | `9px 12px` / `7px` | `7px 10px` / `5px` |
| 选项字号 / 行高 | 13 / 20 | 13 / 18 |
| 选项区上限 | `min(340px, 46vh)` | `min(200px, 30vh)` |
| 页脚 padding | `10px 18px 14px` | `8px 12px 10px` |

选中态、推荐药丸、勾选圈、焦点环一律不变——密的是留白，不是信息。颜色只用
现有 token（`check-style-tokens.mjs` 会拦裸色值）。

### 2. 折叠

- 按钮：状态条最右端，36px 命中区（与 `todoToggle` 同尺寸），`ChevronDownIcon`，
  `aria-expanded` / `aria-controls` / `aria-label` 齐全；图标方向沿用 todo dock 的
  写法（收起朝上、展开朝下），不新增图标。
- 折叠后只有状态条；进度点仍可点，点任意一题会**展开并切到那一题**。
- 动画：外层 `grid-template-rows: 0fr → 1fr` + `opacity`，内容包在
  `min-height: 0; overflow: hidden` 的层里（与 `todoContent` 同一套写法），
  时长用现有 token。
- 状态归 `AskDraft`：加 `collapsed: boolean`，`emptyAskDraft` 默认 `false`。
  store 按 `requestId` 存草稿，天然是「每份提问一份」。不另开 UI store——草稿
  已经是这张卡片唯一的重挂载安全状态，两处存会出现「草稿在、折叠态丢了」。

### 3. 布局位移

- `InputBar.tsx`：删掉 `{askCount > 0 && <AskCard />}` 与相关 selector。
- `ChatView.tsx`：卡片渲染在一个 `[data-ask-seat]` 座位里——非空对话时在
  `[data-chat-flow]` 末尾（turn 列表与 running row 之后），空对话时在空状态之后，
  同一个滚动区。座位带 `askCard?: boolean` prop（默认 `true`）：分栏时只有聚焦
  pane 传 `true`，因为一个没有 session 标签的提问（老服务端）会被
  `pendingForProject` 放行到每个 pane，各渲染一张就会答两次。
- **跟随**：阅读位置在底部时，卡片每次长高（展开、多选变多、自定义输入框出现、
  换到文字更长的一题）都要重新钉到底部，否则卡片长在视口外。用一个 ResizeObserver
  观察座位，回调里读 `snapRef.current.mode === 'following'` 后 `markProgrammatic()`
  + `scroller.scrollTop = scroller.scrollHeight`；不动 `scrollFollow` 的纯函数。
- **补提示**：卡片滚出视口时没有任何东西说明「还有一题等着答」。处理：有待答提问
  且不在底部时，强制显示已有的 jump pill（`snap.showJump || pendingAsk` 的并集，
  只影响渲染，不改 scrollFollow 的状态）。这样往上翻历史时右下角仍有一个明确的
  「下面还有东西」的入口，点它回到底部作答。
- **底部浮层避让**：jump pill / minimap / 宽度把手都按 `--composer-dock-height`
  定位，卡片搬进滚动区后会在它们下面，所以再发布一个 `--ask-card-height`
  （卡片的实测高度），这三处把它一起减掉。
- **渐隐带不用动**：`.scroll` 的 `padding-bottom` 是 `dock + 16px`，遮罩的不透明
  截止线也是 `100% - dock - 16px`，同一条线——停在底部时卡片底边正好落在不透明
  区的边界上，整张卡是实色。滚到中途经过底部渐隐带是所有滚动内容的共同行为。
  这条降级成「实机看一眼即可确认」，不是改动项。

## 门禁与测试

- `bash scripts/check.sh`（ruff / mypy / 架构预算 / rpc 新鲜度 / worktree / pytest；
  本次无 Python 改动但门禁是整仓的）。
- `desktop`：`pnpm test`、`pnpm lint:tokens`、`pnpm lint:motion`、
  `tsc --noEmit`。
- `desktop/tests/askDraft.test.ts`：新草稿默认展开；`setCollapsed` 不改答案。
- `desktop/e2e/composer.spec.ts`：现有用例继续过（`[data-testid="ask-card"]` 不变）；
  新增四条：折叠/展开；不遮盖（最后一行 bottom ≤ 卡片 top，往上滚后卡片离开视口）；
  待答时 jump pill 恒在且点它回到卡片；卡片长高后仍在视口内、回答后读者仍在尾部。
- `desktop/e2e/panes.spec.ts`：分栏时 `[data-testid="ask-card"]` 只有一张。
- 文案：新增 `ask.collapse` / `ask.expand`，中英双份，走 `messages.ts`
  （`i18n` 键集与占位符一致性由 `locale.test.ts` 钉住）。

## 留给实现者的

- 渐隐带那 16px 的实机确认（上一节最后一条）：它只是确认，不是改动项。
- ResizeObserver 的钉底回调写成「只在 `following` 时动」，避免和读者的滚动抢。
- 折叠动画时长、`dvh` 上限的具体数值可按实机微调；契约是不新增 token、不新增图标。
