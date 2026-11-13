# TUI 使用指南

`zen` 在真实终端里默认进入 Textual 全屏界面；`zen --no-tui` 回落到行式 REPL，
管道/非 TTY 环境自动回落。

## 布局

单列结构，抄自 omp 的主屏：**转录区在上，底部一条 dock（todo HUD + 状态带 + 输入行）**。
dock 里 `TodoHud` 只有存在 todo 时显示一行（当前 phase / 当前任务 / closed/total），
数据来自 `ChatController.todos`（启动时从 `<session>.todo.json` 恢复）。
没有底部状态栏，状态信息就是输入框上方那条填色带；屏幕最后一行就是输入行。

```
                             zen
                   v0.3.0 · python agent loop

           / commands  ·  ctrl+b info  ·  ctrl+d quit

  用 bash 运行 echo hi

  ● bash  {"command": "echo hi"}  ·  ok · 19ms  (enter: expand)
    命令执行成功（exit code 0）。

  done · in 1762 · out 33 · cache 1664 · $0.0000

 deepseek-flash · high · ~/py-agent        1762 in / 33 out · cache 49% · $0.0000 · context 0.1% · 4.2s · 7.9 tok/s
╰─ 输入你的消息…
```

- **转录区**：填满剩余高度，自动贴底；`pageup`/`pagedown` 翻找，往上翻会锁定视图并出现
  `↓ new output · pagedown` 提示，不再把你拽回底部（Textual 没有终端原生 scrollback，只能自己实现）。
- **命令补全**：输入 `/` 会在输入框上方列出命令（最多 6 条），`↑↓` 选择、`tab`/`enter` 补全、
  `esc` 收起；继续打字会实时过滤。
- **输入历史**：普通 prompt 记在 `~/.zenagent/tui-history.jsonl`（最多 500 条），`ctrl+r` 打开搜索面板，
  最新的排在最前；多行 prompt 也能完整回填。斜杠命令不入历史。
- **状态带**：一行填色，在 `╰─` 上方。它是**有序列的 segment**，窄屏按固定顺序丢信息：
  先丢右侧事实（tokens/cache 命中率/cache 读/写/花费/上下文/上一轮耗时/生成速率/session）→ 再缩路径 →
  最后才丢左侧身份（model/thinking），**路径永远最后走**；排队/插话计数是唯一被保留到最后的右侧段
  （它是可操作状态，不是事实）。`cache_hit` 由 app 层按 provider 方言归一化
  （OpenAI/Gemini 的 `input` 已含缓存，Anthropic 的 `input` 不含缓存）；`time_spent`/`token_rate` 只在上一轮已结束时出现。
  `git` 段显示分支与改动计数（`+staged ~modified ?untracked`，干净时只显示分支；分支过长会裁头），
  数据由 app 层 `git status --porcelain -b` 探测并缓存 3 秒，不在仓库里时整段消失。
- **输入行**：无边框，`╰─ ` 是唯一的装饰；草稿超过一行时输入框自动长高（最多 8 行），
  `╰─` 只在第一行。
- **侧栏**：默认隐藏，`ctrl+b` 打开（宽 <100 列时自动不显示）。
- **欢迎块**：`zen` + 版本 + 一行提示，随对话增长被顶出屏幕。

## 工作面板（侧栏与浮层）

`ctrl+b` 打开的信息侧栏是 TUI 独有的“正在发生什么”面板：`session` / `model` / `runtime` /
`context` / `todo` / `subagent` / `jobs` 七段，行内容每秒从同一份 `RunStatus` 快照原地刷新。
侧栏关着而后台还有活时，dock 会出现一条可点击的 `◐ N 个后台任务 · ctrl+b` 提示条。

- **todo**：侧栏只显示摘要，点击打开**只读**清单（命令面板里也有入口）；
  可编辑版本是 `/todo` 打开的浮层（见「浮层」一节）。
- **subagent**：列出运行中/已完成的子代理；点击一行把主区域换成该子代理的**只读转录**，
  顶部提示条显示 `◐ 子代理：<name> · 只读 · esc 返回`，再按 `esc` 回主转录。
- **jobs**：后台 bash 作业列表；点击一行打开 job 日志浮层（命令 + 捕获的输出，运行中持续刷新），
  `esc` 关闭；作业已回收时只提示“job 已结束”。
- **context**：上下文占用摘要；点击打开 **context breakdown** 浮层（每段 label / ≈tokens /
  占比 / 精确字符数）。

这些面板只在 TUI 有：REPL 把等价数据打印成文本，headless 只有 `--json` 摘要，RPC 客户端从
`event` / `status` 拿同一份数据（见 [RPC 与 SDK](rpc-sdk.md)）。

## 转录区的行类型

| 行 | 外观 |
|---|---|
| 用户消息 | 整行填色块，**没有 `›` 前缀**；带图消息的 `[image image/png]` 单独一行、暗色 |
| assistant 正文 | Markdown，无装饰 |
| thinking | 推理中一行 `✻ thinking…  <正文首行>  ·  3.2s`（`✻` 高亮、时长每 0.5s 走字）；结束后冻结成 `✻ thinking  <正文首行>  ·  12s`。`enter` 或点击展开；历史回放没有计时信息时只显示首行 |
| 工具调用 | 一行 `● name args · ok · 19ms`，状态点+底色区分运行/成功/失败，`enter` 或点击展开 |
| 写/改文件 | `write_file` / `edit_file` 折叠态就多显示 5 行 diff 预览（`+`/`-` 着色；其余行以 `… N more diff lines` 收口），`enter` 展开完整结果与完整 diff。历史回放没有 details，只显示单行 |
| 工具输出 | 展开后只留**尾部** 12 行，头部显示 `… N earlier lines` |
| 错误 | 红色点 + 红底卡片；失败的工具自动展开 |
| 压缩分隔线 | dim 一行 `── compacted · N prompt tokens → summary session ──`，后面跟着摘要 |
| 用量 | 每轮结束一行 `done · in … · out … · $…`（暗色） |

**自动压缩**：每轮结束后若上一轮真实用量越过窗口的 85%，TUI 和 REPL/headless 一样自动把会话
压缩成新文件，并在转录区留下上面那条分隔线（`/compact` 手动触发同一条路径）。
provider 错误不会进转录区，而是停在 dock 顶部的错误横幅里（见下）。

成功态刻意**不用绿色**（omp 的规则：运行中=accent、成功=暗、只有错误才着色）。

## 外观与主题

- 三种外观：`auto`（按终端 `COLORFGBG` 判断背景亮暗，失败回退 dark）、`dark`、`light`。
- 切换方式：`/theme`、`/settings → Theme…`、启动参数 `--theme`；选择持久化到
  `~/.zenagent/settings.json` 的 `prefs.theme`，`--theme` 覆盖 pref。
- 即时生效：Textual 主题重算 CSS 变量，转录与状态带用同一套 token 重建；
  不打断正在进行的输出，也不丢会话。
- 颜色只有一个来源（`zenagent/app/appearance.py`）：TUI 的 Rich 文本、Textual
  样式表、REPL 渲染器都从它派生。新增 widget 必须用 palette / 主题变量，
  不允许写死 hex（`tests/test_tui_theme_css.py` 会拦）。
- REPL 的 `default|mono|solarized|<path.json>` 是 legacy 主题，只在 REPL 生效。

## 界面语言

- `auto`（默认）/`en`/`zh`：`/lang`、`/settings → Language…`、`--lang` 三处可改，持久化到
  `~/.zenagent/settings.json` 的 `prefs.language`；优先级 `--lang` > pref > 环境（`LANG` 等）。
- 切换即时生效：命令描述重建（registry 构建期翻译）、侧栏标题刷新、Rich 文本重绘；
  正在进行的输出不中止。命令名、flag、tool 名始终是英文（`/` 补全仍显示英文命令名）。
- 文案范围与术语表、以及"哪些东西故意不翻"，见 [界面语言（i18n）](i18n.md)。

## 键位

| 键 | 行为 |
|---|---|
| `enter` | 发送；run 进行中则进入待发区的**排队** lane（本轮结束后发送） |
| `shift+enter` | 输入换行 |
| `esc` | 中断当前 run；**有草稿时不删字**；空闲且草稿为空时，0.6s 内**连按两次**撤销上一轮（等价 `/undo`） |
| `ctrl+c` | **有选区时复制选区**到剪贴板（草稿保留）；无选区时清空草稿（不退出） |
| `ctrl+d` | 退出，打印一行会话摘要 |
| `ctrl+q` | 排队 follow-up：run 中入队，空闲时直接发送 |
| `alt+up` | 把**最后一条**排队消息取回输入框（dequeue，比清空更常用） |
| `ctrl+p` | 循环切换模型 |
| `shift+ctrl+p` | 反向循环切换模型 |
| `alt+p` | 打开模型选择器 |
| `shift+tab` | 循环 thinking 档位（off/low/medium/high） |
| `alt+shift+p` | 切换 plan 模式（再按一次回到之前的模式） |
| `f5` / `alt+r` | 重跑上一轮（等价 `/retry`） |
| `ctrl+t` | 隐藏/显示所有 thinking 行 |
| `ctrl+o` | 展开/收起所有工具卡（单卡仍用 `enter`） |
| `1`–`6` / `enter` / `esc` | `ask` 工具弹框：数字键或点击选项、`multi` 时数字切换后 `enter` 提交、`esc` 取消 |
| `enter` / `space` / 点击 | 展开/折叠 `✦ skill:<name>` 调用条（查看注入的 skill 正文） |
| — | 运行中的工具卡会实时显示一行流式进度（`bash` 的长命令输出，1s 内多次更新合并渲染） |
| `ctrl+x` | 清空待发区：排队 + 插话两条 lane |
| `ctrl+s` | steer（插话）：有输入时插入当前 run；输入框为空时把**最后一条排队**升格为插话 |
| `ctrl+k` | 命令面板（模型/会话/退出） |
| `ctrl+r` | 历史搜索：列出本次会话和以往提交过的 prompt，输入过滤、`enter` 回填（最新的在前） |
| `ctrl+b` | 开关信息侧栏 |
| `ctrl+g` | 用 `$VISUAL`/`$EDITOR` 编辑当前草稿（退出后回填；失败保留原草稿） |
| `ctrl+v` | 粘贴剪贴板：内容是本地图片路径则挂载附件，否则按文本插入草稿 |
| `tab` | 提示/补全列表打开时接受当前候选 |
| `pageup` / `pagedown` | 转录区上翻/下翻；下翻到底部即恢复自动跟随 |
| 鼠标拖选 | 选中转录区文本（`ALLOW_SELECT`）；单击链接在浏览器打开；滚轮滚动 3 行 |

> 与 v0.1 的差异：`ctrl+c` 从"中断"改为"清草稿"，中断改为 `esc`；退出从 `ctrl+q`
> 改为 `ctrl+d`；命令面板从 `ctrl+p` 改为 `ctrl+k`（`ctrl+p` 让给模型循环，跟 omp 一致）。

## 待发区（排队 / 插话）

运行中输入的文本不会消失：它会立刻出现在状态带与输入框之间的待发区里，分两组。

```
排队 · 2
  1. 把 handler 改成异步…
  2. 顺手跑一下测试
插话 · 1
  1. 停，不是那个文件
└ alt+↑ 取回 · ctrl+x 清空 · 点击排队行插话
```

- **排队（queued）**：本轮结束后按顺序发送；`alt+up` 取回最后一条、`ctrl+x` 清空。
- **插话（steering）**：不打断本轮；当前工具轮结束、下一次模型请求前注入，模型马上回应。
  有草稿时 `ctrl+s` 直接发插话；输入框为空时 `ctrl+s` 把最新一条排队升格为插话；也可以
  直接点击排队行。插话通道只收文本（带图片的排队消息会提示仍在排队）。
- 超过 6 条消息行时折叠为 `… n more`；两条 lane 都空时整块隐藏。
- 一个边界：如果这轮正好结束、插话还没等到注入点，应用会自动续跑一轮把它送到模型；
  run 报错或被 `esc` 中断时，未送达的插话降级回排队 lane（可见、不丢、不自动重跑）。
- 待发不跨会话：切换会话、`/new`、`/undo`、`/fork`、`/retry` 会清空待发并留一条提示；
  压缩（`/compact` 或自动压缩）保留待发。

## 运行模式与工具审批

状态带左侧显示当前模式（`plan` / `ask` / `auto`）。

- **输入即提示**：输入 `/mode ` 会在输入框上方列出 `plan` / `ask` / `auto`（带说明），
  `/thinking ` 列出档位，`/model ` 列出模型；`↑↓` 移动、`tab` 接受、`esc` 收起。输入会过滤候选。
- **选择器**：裸 `/mode` 或 `/thinking` 按 `enter` 直接打开选择器（当前值预选中），`enter` 应用、
  `esc` 取消；这对应 omp 的 thinking-selector/theme-selector 交互。
- **Enter 接受并提交、Tab 只补全**：值提示打开时，Enter 直接对高亮值执行（`/mode ` 选 auto 再
  Enter 就是 `/mode auto`，`/model ` 选中某个模型再 Enter 就是直接切换，都不再多绕一层
  选择器）；命令名提示打开时，Enter 补成 `/mode ` 再提交（裸命令因此会打开选择器）；
  `Tab` 只补全、不提交，适合继续输入。
- `/mode plan|ask|auto` 显式给值、或 `zen --mode ...` 启动模式也照常可用（默认 ask；`zen` 是旧权限模式别名）。
- **plan**：写/执行工具被拒，模型改为只读调研并给出计划。计划给出后输入 `/plan-review`
  打开只读复核浮层：`a` 批准并切到 `auto`，`k`/`esc` 继续规划；没有计划时提示先生成计划。
- **ask**：写/执行工具弹审批框：`y` 允许一次、`a` 本会话对该**目标**总是允许（框里会显示目标）、`n`/`esc` 拒绝；
  120 秒无响应按拒绝处理。审批框一次只弹一个（并发工具调用会排队）。
  审批框是居中的描边面板（标题 + 工具参数摘要 + 可点击的选项行）：bash 显示命令块、
  写文件显示路径和行数、编辑显示 old/new 首行；**鼠标点击选项即可选择**；
  选项带 `[y]`/`[a]`/`[n]` 键位提示，`Enter` 刻意不绑定——避免误触直接授权。
  `confirm`（删会话）与 `rename` 用的是同一套面板。
- **auto**：全部放行、不弹任何确认（破坏性命令、越界写入、凭证路径都直接执行）；仅灾难命令
  仍拒绝（`--allow-dangerous` 可再放行），`[tools] deny` 永远优先；system prompt 会告诉模型
  直接执行、不要请示。

## 浮层：快捷键 / 用量 / todo / MCP

- `/hotkeys` 打开只读按键表（应用键位 + 输入框 chord），`esc` 关闭；REPL 仍打印文本表。
- `/usage` 打开用量面板：当前会话 in/out/cache/成本/上下文 + 子代理花费，下面附最近 50 个会话的聚合报告。
- `/todo` 打开**可编辑** todo 列表（与模型 `todo` 工具共用 `<session>.todo.json`）：`a` 添加、`d` 完成、`b` 阻塞、`u` 取消阻塞、`x` 删除、`esc` 保存并刷新 HUD。
- `/mcp add` 打开两字段向导（服务器名 + 命令），保存到 `~/.zenagent/mcp.json`（原子写、0600）并尝试重连；REPL 只提示编辑文件。
- `/plan-review` 见「运行模式」；`esc` 在浮层里先清搜索、再关面板（Palette 的 Esc 分层）。

## 启动参数

TUI 与 REPL/RPC 共用 `RuntimeOptions`，所以这些启动参数不再静默忽略：

- `--thinking LEVEL`：首轮推理档位（`/thinking` 仍可会话中切）。
- `--trace PATH`：写 JSONL trace；`/trace` 会显示真实路径。
- `--metrics PATH`：每次 run 追加一行指标。
- `--no-auto-compact`：关闭自动压缩（手动 `/compact` 不受影响）。
- `--image PATH`：启动即挂载图片附件（非 vision 模型会直接报错退出）。

## 模型管理

`alt+p`（或 `ctrl+k` → `model`，或裸 `/model`）打开选择器：

- 底部贴底的浮层，带搜索框；`↑↓` 移动、`enter` 选择、`esc` 取消，输入即过滤。
- `●` 标记当前模型并默认选中；未配置 API key 的模型置灰并显示原因，不能选择。
- 下一轮生效；上下文窗口、thinking 默认值、session id 同步更新。
- 重复按同一个快捷键只会聚焦已经打开的浮层，不会叠出第二个。

想直接切换时用 `/model <id>`（或输入 `/model ` 后按 enter 选中高亮模型）：与 `/mode <值>`
一致，一次 enter 就换好，不再打开选择器。值提示里**当前模型排第一、可用的排在前面**，
所以没有输入时按 enter 只是原地不动，不会误切到缺 key 的模型。

密钥在 `/settings → Model API keys…` 里粘贴（存 `~/.zenagent/settings.json`，0600），保存后
立即生效、无需重启；也可以继续写 `~/.zenagent/config.toml` 或设置 provider 的环境变量。
启动时缺 key **不挡窗口**：TUI 照常打开，底部横幅提示该配哪个变量，配好后横幅消失。

## 更新提醒

TUI 启动后如果 PyPI 上有更新的 `zenagent-py`，转录区会出现一行：

```
Update available: v0.4.0 (you have v0.3.0) · uv tool upgrade zenagent-py
```

- 本地缓存 `~/.zenagent/update.json` 24 小时内不重复联网；每个新版本只提醒一次，每次
  会话最多一条。
- 升级命令按安装方式给出（uv tool / pipx / pip）；`[cli]` 是 extra，核心 agent 与 TUI
  一起升级。
- `-p`、`--json`、REPL、RPC 不检查、不输出。
- 关闭：`ZENAGENT_NO_UPDATE_CHECK=1`。

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
- `/pin`（或 `/pin on|off`）把当前会话置顶：选择器里置顶行显示 `★`，置顶组排最前，组内仍按时间倒序。

## 排障

- 界面异常/无法启动：`TERM=dumb`、极老 conhost、非 TTY 环境会自动回落到 REPL。
- 首次启动没配 key：TUI 不会拒绝启动，底部横幅提示到 `/settings → Model API keys` 粘贴即可。
- 想看纯文本输出：`zen --no-tui`。
- 只想跑一次：`zen -p "..."`（headless，不涉及 TUI）。
- 缺 textual：`uv sync --extra cli`（缺依赖时 `zen` 会直接给出这句提示并以退出码 2 结束）。
- 状态带少了某项：那是窄屏降级，不是 bug —— 拉宽终端，或按 `ctrl+b` 看侧栏明细。

## 鼠标与链接

转录区支持**拖选**：选中后 `ctrl+c` 复制选区（无选区时 `ctrl+c` 仍清草稿，与 omp 的
`tui.input.copy` 同一套语义）。这是 alt-buffer 应用必须自己实现的部分——omp 把定稿行
写进终端 scrollback，选中/复制/点链接都是终端白送的（见
`docs/research/omp-cli-study/04-transcript-and-tools.md`）。

单击 Markdown 里的链接（含裸 URL，解析器开着 linkify）会用默认浏览器打开。**代码块
（``` 或 4 空格缩进）里的 URL 不可点击、且会因无法折行被裁掉右半**；要取这类链接用：

- `/urls` —— 编号列出上一条回答里的全部链接（预览 + 原始长度）
- `/copy <n>` —— 第 n 条按**逐字节原文**复制（不截断、不做百分号解码）
- `/open <n>` —— 第 n 条交给默认浏览器

这三个命令读的是原始 markdown 文本，所以不受渲染裁切和 `normalizeLink` 解码影响
（点击路径仍走解析后的 href）。终端自己支持 Shift+拖选绕过应用时，也可以直接用。

## 设计出处

这一版的布局/状态带/工具卡/浮层是对 [omp](https://github.com/can1357/oh-my-pi)
的复刻，调研记录在 `docs/research/omp-cli-study/`（7 篇，含逐项源码对照）。

## 错误横幅

provider/流式失败会停在 dock 顶部的一行红色横幅（`✘ <原因>`）里，不再被后续输出刷走；
**点击横幅**关闭，或开始下一轮时自动清掉。工具失败仍然落在各自的工具卡上，不进横幅。
启动时缺 API key 也走这条横幅（`✘ no API key for … — /settings → Model API keys`），
在设置里粘贴 key 后自动消失。

## 图片附件

- `/image <path>` 把本地图片挂到**下一条** prompt（png/jpeg/gif/webp，单张 ≤5MB，按 magic byte 校验，
  不看扩展名）；非 vision 模型会被明确拒绝，图片不会发出去。
- 挂上后输入框上方出现 attachment chip 带（`[image/png]`），**点击 chip** 移除该图；
  `/images` 列出当前挂着的图，`/images clear` 清空。
- 发送后 chip 自动清空；REPL 的 `/image` 语义相同。
- `ctrl+v` 可以直接粘贴剪贴板里的图片**路径**（终端不会把图片字节交给应用）：命中本地图片就挂载，
  否则当普通文本插入；没有剪贴板工具（pbpaste/wl-paste/xclip/xsel）时提示不可用。
