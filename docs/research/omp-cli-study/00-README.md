# omp CLI 设计调研（用于 Textual 复刻）

调研对象：[`can1357/oh-my-pi`](https://github.com/can1357/oh-my-pi)（omp）main 分支的 CLI/TUI 设计。
目的：**只研究"它长什么样、怎么交互、有哪些功能"，不研究怎么实现**；产出一套可以直接照着改我们自己 TUI 的设计底账。

## 读哪篇

| 文档 | 内容 | 什么时候读 |
|------|------|-----------|
| [`01-interaction-hierarchy.md`](01-interaction-hierarchy.md) | 交互层级：4 种表面、dock、会话焦点、菜单栈、Esc 语义链、键位命名空间 | 先读这篇，建立整体模型 |
| [`02-layout.md`](02-layout.md) | 主屏布局：17 层 dock 顺序、8 种 composer 形状、面板绘制词汇、宽度降级 | 动手改布局前读 |
| [`03-status-line-and-composer.md`](03-status-line-and-composer.md) | 状态栏 27 段段表 + 四步降级 + 6 个 preset；输入区的边框/高度/autocomplete/chip | 做状态带与输入区时读 |
| [`04-transcript-and-tools.md`](04-transcript-and-tools.md) | 转录两条渲染带、block 生命周期、~20 种行类型、工具卡的三种拓扑与统一骨架 | 做转录与工具卡时读 |
| [`05-overlays-and-surfaces.md`](05-overlays-and-surfaces.md) | 三层弹层体系（内联/锚定浮层/全屏）、共用骨架、尺寸档位、栈叠与 Esc | 做选择器与全屏界面时读 |
| [`06-feature-inventory.md`](06-feature-inventory.md) | 功能总账：87 个命令、45 个键位、25 个全屏界面、61 个工具渲染器 + 取舍建议 | 决定"做什么/不做什么"时读 |
| [`07-textual-replication-plan.md`](07-textual-replication-plan.md) | **落地计划**：差距表、目标组件树、模块清单、P0-P4 分阶段、键位迁移表、视觉 token 映射 | 开始改代码时读 |
| [`08-omp-command-reference.md`](08-omp-command-reference.md) | **omp 87 个顶层命令全表**（7 个域）+ 我们的对齐状态 | 想查"omp 有什么功能"时读 |

## 五条最反直觉的结论

1. **omp 的主屏没有侧栏，状态栏也不在底部。** 默认 `band` 形状下，状态信息是**输入区上方一整条填色带**，屏幕最后一行就是输入行本身。
2. **状态栏是 27 个可配置 segment 的有序装配**，不是一行写死的字符串。核心契约：`visible:false` 的段**完全消失且不留分隔符**；宽度不足时按"截 session_name → 丢右组尾部 → 压 path → 丢左组，但 path 永远最后丢"四步降级。
3. **对话区是两条带**：已提交的行写进终端原生 scrollback 后**永久冻结**，只有底部一条活动 viewport 会重排。所以"往上翻"根本不是 UI 功能。
4. **弹层只有三层**（内联替换 composer / 贴底锚定浮层 / 全屏 alt screen），且 7 种"单实例菜单"共用一个规则：重复请求 = 聚焦已开的那个，绝不叠第二个。
5. **Esc 是一个 7 步语义链**：bash 模式 → python 模式 → 中止 eval → 结束等待 → 中止本轮 → **有草稿则什么都不做** → 空草稿时 500ms 内双击才开"回退选择器"。

## 与当前 ZenAgent TUI 的差距（一句话版）

现在的 ZenAgent 是"左侧栏 + 聊天 + 底部状态栏 + 圆角输入框"的三段式；omp 是"单列转录 + 底部 dock（HUD 序列）+ 状态带 + 无框 `╰─ ` 输入"。**布局、状态栏、输入区、工具卡、浮层五处都要改**，其中布局改动最大（去掉默认侧栏）。

## 证据与局限

- 布局/交互结论**全部来自源码**（`packages/tui` 440 文件 + `packages/coding-agent/src/modes`），不是从截图猜的。
- 仓库 `assets/*.webp` 是 **pi 时代**的截图（欢迎页还是 "Welcome back!" + 右栏 LSP/Recent sessions，当前源码里已无此结构），只当视觉参考，不作布局依据。
- 未能采集**当前版本的真实渲染帧**：官方 npm 包（`@oh-my-pi/pi-coding-agent@18.14.0`）要求 `bun >= 1.4.4`，本机为 1.3.5，bundle 解析失败。若需要逐像素对齐，可先装独立 bun 再跑 `omp gallery`（它专门提供 composer / status line / tool renderer 的确定性预览）。

## 源码对照（调研范围）

- `packages/tui/src/{chrome,status-line,chat,tools,overlays,apps,prompt,components}` — 渲染与组件层
- `packages/coding-agent/src/modes/{interactive-mode.ts,controllers,interactive}` — 装配与控制层
- `packages/coding-agent/src/slash-commands/builtin-*.ts` — 87 个命令定义
- `docs/{tui,tui-core-renderer,tui-runtime-internals,keybindings,theme,agent-hub,approval-mode,cli-reference}.md` — 官方文档
