# TUI 使用指南

`zen` 在真实终端里默认进入 Textual 全屏界面；`zen --no-tui` 回落到行式 REPL，
管道/非 TTY 环境自动回落。

## 布局

单列结构，抄自 omp 的主屏：**转录区在上，底部一条 dock（状态带 + 输入行）**。
没有底部状态栏，状态信息就是输入框上方那条填色带；屏幕最后一行就是输入行。

```
                             zen
                   v0.2.1 · python agent loop

           / commands  ·  ctrl+b info  ·  ctrl+d quit

  用 bash 运行 echo hi

  ● bash  {"command": "echo hi"}  ·  ok · 19ms  (enter: expand)
    命令执行成功（exit code 0）。

  done · in 1762 · out 33 · cache 1664 · $0.0000

 deepseek-flash · high · ~/py-agent        1762 in / 33 out · $0.0000 · context 0.1%
╰─ 输入你的消息…
```

- **转录区**：填满剩余高度，自动贴底；`pageup`/`pagedown` 翻找，往上翻会锁定视图并出现
  `↓ new output · pagedown` 提示，不再把你拽回底部（Textual 没有终端原生 scrollback，只能自己实现）。
- **命令补全**：输入 `/` 会在输入框上方列出命令（最多 6 条），`↑↓` 选择、`tab`/`enter` 补全、
  `esc` 收起；继续打字会实时过滤。
- **状态带**：一行填色，在 `╰─` 上方。它是**有序列的 segment**，窄屏按固定顺序丢信息：
  先丢右侧事实（tokens/花费/上下文/session）→ 再缩路径 → 最后才丢左侧身份
  （model/thinking），**路径永远最后走**。
- **输入行**：无边框，`╰─ ` 是唯一的装饰；草稿超过一行时输入框自动长高（最多 8 行），
  `╰─` 只在第一行。
- **侧栏**：默认隐藏，`ctrl+b` 打开（宽 <100 列时自动不显示）。
- **欢迎块**：`zen` + 版本 + 一行提示，随对话增长被顶出屏幕。

## 转录区的行类型

| 行 | 外观 |
|---|---|
| 用户消息 | 整行填色块，**没有 `›` 前缀** |
| assistant 正文 | Markdown，无装饰 |
| thinking | 折叠成一行 `✻ thinking · 1.2k chars`，`enter` 展开 |
| 工具调用 | 一行 `● name args · ok · 19ms`，状态点+底色区分运行/成功/失败，`enter` 展开 |
| 工具输出 | 展开后只留**尾部** 12 行，头部显示 `… N earlier lines` |
| 错误 | 红色点 + 红底卡片；失败的工具自动展开 |
| 用量 | 每轮结束一行 `done · in … · out … · $…`（暗色） |

成功态刻意**不用绿色**（omp 的规则：运行中=accent、成功=暗、只有错误才着色）。

## 键位

| 键 | 行为 |
|---|---|
| `enter` | 发送；run 进行中则进入 follow-up 队列 |
| `shift+enter` | 输入换行 |
| `esc` | 中断当前 run；**有草稿时不删字** |
| `ctrl+c` | 清空草稿（不退出） |
| `ctrl+d` | 退出，打印一行会话摘要 |
| `ctrl+q` | 排队 follow-up：run 中入队，空闲时直接发送 |
| `ctrl+p` | 循环切换模型 |
| `alt+p` | 打开模型选择器 |
| `shift+tab` | 循环 thinking 档位（off/low/medium/high） |
| `ctrl+t` | 隐藏/显示所有 thinking 行 |
| `ctrl+o` | 展开/收起所有工具卡（单卡仍用 `enter`） |
| `ctrl+x` | 清空 follow-up 队列 |
| `ctrl+s` | steer：把输入插入当前 run |
| `ctrl+k` | 命令面板（模型/会话/退出） |
| `ctrl+b` | 开关信息侧栏 |
| `tab` | 提示/补全列表打开时接受当前候选 |
| `pageup` / `pagedown` | 转录区上翻/下翻；下翻到底部即恢复自动跟随 |

> 与 v0.1 的差异：`ctrl+c` 从"中断"改为"清草稿"，中断改为 `esc`；退出从 `ctrl+q`
> 改为 `ctrl+d`；命令面板从 `ctrl+p` 改为 `ctrl+k`（`ctrl+p` 让给模型循环，跟 omp 一致）。

## 运行模式与工具审批

状态带左侧显示当前模式（`plan` / `ask` / `full`）。

- **输入即提示**：输入 `/mode ` 会在输入框上方列出 `plan` / `ask` / `full`（带说明），
  `/thinking ` 列出档位，`/model ` 列出模型；`↑↓` 移动、`tab` 接受、`esc` 收起。输入会过滤候选。
- **选择器**：裸 `/mode` 或 `/thinking` 按 `enter` 直接打开选择器（当前值预选中），`enter` 应用、
  `esc` 取消；这对应 omp 的 thinking-selector/theme-selector 交互。
- **Enter 接受并提交、Tab 只补全**：值提示打开时，Enter 直接对高亮值执行（`/mode ` 选 full 再
  Enter 就是 `/mode full`，不再多绕一层选择器）；命令名提示打开时，Enter 补成 `/mode ` 再提交
  （裸命令因此会打开选择器）；`Tab` 只补全、不提交，适合继续输入。
- `/mode plan|ask|full` 显式给值、或 `zen --mode ...` 启动模式也照常可用（默认 ask）。
- **plan**：写/执行工具被拒，模型改为只读调研并给出计划。
- **ask**：写/执行工具弹审批框：`y` 允许一次、`a` 本会话总是允许、`n`/`esc` 拒绝；
  120 秒无响应按拒绝处理。审批框一次只弹一个（并发工具调用会排队）。
- **full**：直接放行；危险命令仍被拦截（除非 `--allow-dangerous`），`[tools] deny` 永远优先。

## 模型管理

`alt+p`（或 `ctrl+k` → `model`，或 `/model`）打开选择器：

- 底部贴底的浮层，带搜索框；`↑↓` 移动、`enter` 选择、`esc` 取消，输入即过滤。
- `●` 标记当前模型并默认选中；未配置 API key 的模型置灰并显示原因，不能选择。
- 下一轮生效；上下文窗口、thinking 默认值、session id 同步更新。
- 重复按同一个快捷键只会聚焦已经打开的浮层，不会叠出第二个。

编辑 provider/密钥请改 `~/.zenagent/config.toml`，TUI 不做内联编辑。

## 会话管理

`ctrl+k` → `sessions`（或 `/sessions`，`/sessions all` 直接进全项目视图）打开选择器：同一套浮层骨架，带搜索。
列表显示的是**会话名**（首个用户问题的 brief，或 `/rename` 设置的名字），文件名作为副信息。

- 默认只显示**当前项目**的会话；`tab`（或搜索框为空时按 `t`）在 project / all-projects 之间切换，
  all 视图每行标注 `[项目名]` / `[legacy]`。
- `enter` 恢复会话（运行中会先中断当前 run 再切换；跨项目会话会提示工具仍在当前 cwd）。
- `n` 新建、`f` fork 当前会话、`d` 删除、`e` 导出 Markdown、`r` 改名（都属于当前高亮行）。
- 删除前弹 Confirm 二次确认，删除走「移到 `sessions/.trash/`」，不做不可逆 unlink；
  当前会话删除后自动新建空会话。
- 也可以直接输入 `/rename <name>` 改当前会话名；名称存在 `sessions/.titles.json`，不写进 JSONL。

## 排障

- 界面异常/无法启动：`TERM=dumb`、极老 conhost、非 TTY 环境会自动回落到 REPL。
- 想看纯文本输出：`zen --no-tui`。
- 只想跑一次：`zen -p "..."`（headless，不涉及 TUI）。
- 缺 textual：`uv sync --extra cli`（缺依赖时 `zen` 会直接给出这句提示并以退出码 2 结束）。
- 状态带少了某项：那是窄屏降级，不是 bug —— 拉宽终端，或按 `ctrl+b` 看侧栏明细。

## 设计出处

这一版的布局/状态带/工具卡/浮层是对 [omp](https://github.com/can1357/oh-my-pi)
的复刻，调研记录在 `docs/research/omp-cli-study/`（7 篇，含逐项源码对照）。
