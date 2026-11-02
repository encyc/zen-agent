# 会话链接命令（`/urls`、`/copy <n>`、`/open <n>`）设计（2026-11-02）

状态：待批准
作者：Lead（与用户讨论后整理）
基线：main `9a69dfd`（0.7.0）

相关：
`zenagent/app/{links.py(新),commands.py}`、`zenagent/cli/completion.py`、
`zenagent/cli/tui/clipboard.py`、`zenagent/i18n/{zh.py,en.py}`、
`tests/{test_app_links.py(新),test_app_commands.py}`、`docs/feature-map.md`、`CHANGELOG.md`

## 1. 意图

触发场景：隔壁 session 给了一条 265 字符的 Metabase OAuth 回调链接，它落在**代码块**里。
用户三件事同时失败：屏幕上右侧被裁掉、点不动、拖选只拿到前 80 字符——授权窗口 15 分钟，
人被卡住。

实测根因（Textual 8.2.8，100 列终端）：

| URL 写法 | 视觉 | 拖选 | 点击 |
|---|---|---|---|
| 普通段落 | 会换行（wrap） | 可，但须跨行拖 | 可（`@click: link(href)`） |
| ```` ``` ```` 代码块 / 4 空格缩进 | **不换行、右侧裁掉** | **只到可见处** | **无任何可点区域** |

1. `MarkdownFence` 是 `overflow: scroll hidden` + `scrollbar-size-horizontal: 0`，而 Textual
   8.2 的 `text-wrap` 只有 `wrap/nowrap`、没有 word-break：无空格长串只能一路向右，
   超出部分在视觉和选区上都不存在。
2. 代码块内容不走 linkify，没有 `@click` 元数据，点不动。
3. 附带发现：点击打开时 markdown-it 的 `normalizeLink` 会解码，`%3A%2F%2F` 变 `://`——
   打开的**不是屏幕上那条原文**。OAuth 的 `redirect_uri` 要求逐字节一致，这是隐患。

结论：不要再依赖渲染层（它按宽度裁剪、按解析器改写）。直接读会话里的**原始 markdown
文本**，编号列出、原样复制、显式打开。`/copy` 已经是这条路的先例（实测把 359 字符全文
写进剪贴板，含被裁掉的 URL）。

## 2. 目标 / 非目标

**目标**

1. `/urls`：列出上一条回答里出现的全部 http(s) 链接，编号 + 截断预览 + 原始长度。
2. `/copy <n>`：按编号把该链接**逐字节原文**写进剪贴板（不解码、不截断）。
3. `/open <n>`：用系统默认浏览器打开该链接。
4. REPL 与 TUI 共用（走 `CommandRegistry`），中英双语。
5. `/copy` 既有无参行为（复制上一条回答）与「复制字面参数」行为保持不变。

**非目标**

- 不改 `MarkdownFence`、不加可见横向滚动条、不做代码块 linkify（上一轮对话里的方案 B/C，
  另开一轮）。
- 不改 transcript 渲染、不加 hover 高亮、不做鼠标悬停取链接。
- 不做 `/urls --all`（扫整段会话）、不做链接过滤/搜索。
- 不修 `normalizeLink` 的解码问题本身（点击路径仍走解析后的 href；本轮只保证
  `/copy <n>` 与 `/open <n>` 用原文）。修点击路径需要自定义 Markdown 渲染，属方案 B。
- 不碰 Web/其他前端、不落盘、不进会话文件。

**反目标**

- 静默失败：没有链接、索引越界、打开失败、剪贴板不可用都必须有明确文案。
- `zenagent/app/**` import textual/rich（沿用现有分层约束）。
- 让 `/copy` 的旧语义需要用户重新学一遍。

## 3. 方案（对话中已定 A）

| | 做法 | 代价 |
|---|---|---|
| **A（选定）** | 原始文本扫描 + 编号命令 | 不解决"鼠标点代码块里的链接"，但立刻可用、逐字节保真、REPL 同享 |
| B | 定制 `MarkdownFence`：URL 加 `@click` + 恢复横向滚动条 | 改渲染层，收益只有"能点"；拖选仍受可见区限制 |
| C | 只加可见横向滚动条（CSS 一行） | 最小，但点不动、拖选体验差 |

选 A 的原因：用户卡点是"取不到那条链接"，不是"必须用鼠标"。A 一条命令就同时解决
裁切、不可点、解码改写三种情况，且在窄终端下同样成立。

## 4. 行为细节

- **抽取**（`zenagent/app/links.py`，纯函数、无 IO）：
  `extract_links(text: str) -> list[str]`
  - 正则 `https?://[^\s<>"'`]+`，对代码块与普通段落一视同仁（不需要解析 markdown）。
  - 去尾部标点 `.,;:!?、。，）》】"']}`；若尾部是 `)` 且左括号少于右括号则削掉一个
    （保 Wikipedia 一类成对括号的 URL）。
  - 按出现顺序**去重**（完全相同的字符串只留第一次）。
  - 不做任何百分号解码/编码。
- **数据源**：`ChatController.last_assistant_text()`（现有方法，返回上一条 assistant 的
  原始 markdown，已实测含完整 URL）。
- `/urls`：逐行 `1. https://metabase.zhizugz.cn/oauth/authorize?res…  (265 字符)`，
  预览宽 80 字符；空 → `cmdmsg.urls_empty`。
- `/copy <n>`：参数为纯数字（允许 `#2`）时按索引复制原文；否则保持现有「复制字面文本」。
  **我的判断**：用纯数字当索引进 `/copy`，与"复制字符串 `2`"的冲突可忽略，且用户明确要
  这个笔顺；规则与文案里都写明，避免歧义。越界 → `cmdmsg.url_index_invalid`（带 `1..N`）。
- `/open <n>`：新命令，`webbrowser.open(url)`；返回假值或抛错 → `cmdmsg.url_open_failed`，
  不冒泡异常（命令层异常会撕掉 TUI）。
- 补全表 `zenagent/cli/completion.py` 增加 `/urls`、`/open`。
- i18n 新增：`cmd.urls.description`、`cmd.open.description`、`cmdmsg.urls_empty`、
  `cmdmsg.url_index_invalid`、`cmdmsg.url_copied`、`cmdmsg.url_opened`、
  `cmdmsg.url_open_failed`；`cmd.copy.description` 补一句索引用法。

## 5. 测试计划（全部离线）

1. `extract_links`：代码块内 URL、`[t](url)`、裸 URL、尾部 `)。`、成对括号、重复去重、
   `%3A%2F%2F` 原样保留、`ftp://`/相对路径忽略。
2. `/urls` 输出含编号与字符数；空回答 → empty 文案。
3. `/copy 2`（monkeypatch `write_clipboard_text`）→ 收到逐字节原文（含 `%3A%2F%2F`）。
4. `/copy hello` → 仍复制字面 `hello`；`/copy` 无参 → 上一条回答原文。
5. `/open 1` → monkeypatch `webbrowser.open`；`/open 9` → 越界文案；打开失败 → failure 文案。
6. TUI 端到端：提交含代码块长 URL 的回答 → `dispatch("urls")` 消息里有该 URL →
   `dispatch("copy", "1")` 剪贴板拿到全文。

## 6. 留给实现者

- 预览截断宽度（80）可调，不影响复制保真。
- `/urls --all`、点击路径的解码修复：本轮不做，已记在非目标里。
- help/README 的命令清单由现有机制生成，不需要手工排版。

## 7. 后续追加（同日，实现并合并之后）

原非目标里写着"不改渲染层、不修点击路径"，但实现完成后对照 `docs/research/omp-cli-study/`
发现：omp 的选中/复制/点链接全部是终端 scrollback 白送的（`04-transcript-and-tools.md`），
而 ZenAgent 因为选了 alt-buffer，必须自己实现——这正是 `ALLOW_SELECT = False` 当初被
关掉后一直没人补回来的原因（`09-zenagent-vs-omp-gap.md` 把它记成 "P2（Textual 鼠标被关）"）。

追加决定（一行代码 + 回归测试）：

- `ALLOW_SELECT = True`：拖选生效；有选区时 `ctrl+c` 复制选区并保留草稿，无选区时仍清草稿
  （Textual 的 Screen 绑定在有选区时先命中，等价 omp 的 `tui.input.copy`）。
- 当初关掉它的 Textual 8.2 `Screen._forward_event` 崩溃在锁定的 8.2.8 上复现不出来
  （跨块拖选、点击、流式中拖选三种路径实测通过），`tests/test_tui_selection.py` 把这三条钉住。
- `/urls`、`/copy <n>`、`/open <n>` 不因此淘汰：它们拿到的是**逐字节原文**，而点击路径仍走
  markdown-it 解析后的 href（`%3A%2F%2F` → `://`），代码块里的链接也仍然不可点。
