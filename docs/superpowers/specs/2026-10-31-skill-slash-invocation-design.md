# /skill:<name> 斜杠调用（设计稿）

> 状态：待确认（2026-10-31，v2）。改的是 ZenAgent 的用户侧交互，不涉及 prompt 系统工程的另一条线。

## 1. 意图（why）

Skills 现在是「只给模型用」的：发现后把 `name + description` 放进 prompt，正文由模型按需
`read_skill`。用户侧 `/<name>` 菜单里看不到它们，也不可靠——用户打了 skill 名，模型是否真的
去读正文取决于模型自觉。

omp 18.14.1 的做法（源码证据见 §5）是：skill 注册成 `/skill:<name>` 斜杠命令，提交时**由前端
把 SKILL.md 正文直接注入成一条 user 消息**，模型不需要自己决定要不要读。用户确认按这套做。

**目标结果**：用户敲 `/`（行首或句子中间）能看到并补全 `skill:<name>`；回车后模型一定拿到
完整 skill 正文。

## 2. Goals / non-goals

Goals：
- REPL 与 TUI 的 `/` 补全/提示里出现 `skill:<name>`（带 description 与类型标签）。
- **行首和句中**的 `/skill:<name>` 都支持：提交时注入正文 + 参数 + skill 目录提示。
- 补全同样覆盖句中 token：在任意位置输入 `/skill:pd` 都能弹出 skill 名并补全。
- 未知 skill、skills 被禁用、读取失败都有明确、可恢复的反馈。
- TUI 转录里把注入消息折叠成一行可展开的「skill 调用」chip；session 里仍存全文。
- 刷新：TUI 跟随 `runtime.skills` 实时变化；REPL 与现有 `/skills`、`/reload` 语义一致。
- 斜杠项统一显示类型标签，解决同名歧义。

Non-goals（本版不做）：
- 裸 `/name` 调 skill（和命令/template 冲突；omp 也用 `skill:` 前缀）。
- 新的 custom message 类型 / 改 session schema（用首行标记识别，见 §4.4）。
- TUI 补上 template/extension 命令支持（当前 TUI 只支持内置命令；这是独立缺口）。
- MCP prompts 斜杠化（当前 MCP 只贡献工具，不贡献斜杠项）。
- 改 `read_skill` 工具或 prompt 索引；`/help` 的 skill 区块（`/skills` 已有列表）。

## 3. 备选方案（Approaches）

| 方案 | 做法 | 取舍 | 结论 |
|---|---|---|---|
| A 字面透传 | 只加补全；提交后把 `/pdf-report ...` 原文发给模型，模型自己 `read_skill` | 改动最小；触发靠模型自觉，不稳定 | 否（用户已判断不稳） |
| B 去斜杠透传 | 同 A，但发送 `pdf-report ...` | 名字与 roster 一致，仍靠模型自觉 | 否 |
| C omp 式注入 | `/skill:<name>` 提交时前端读正文、渲染成 user 消息 | 触发确定；需要显示/刷新/错误处理 | **采用** |

C 的代价：多了“用户侧读 skill 文件”的路径、TUI 显示需要识别注入消息；换来的是不再赌模型。
omp 已经用这个方案跑了 30+ 工具与大量 skill，值得对齐。

## 4. 设计（Chosen design）

### 4.1 新模块 `zenagent/app/skill_commands.py`

纯函数（可单测，不依赖 Textual/prompt_toolkit）：

- `SKILL_COMMAND_PREFIX = "skill:"`；`skill_command_name(skill) -> "skill:<name>"`。
- `parse_skill_invocation(text) -> SkillInvocation(name, args, span, prompt) | None`：
  - **行首形式**：`^\s*/skill:(\S+)(?:\s+(.*))?$`（`re.S`，参数可含换行）。
  - **句中形式**：草稿不是以别的 `/` 命令开头时，匹配第一个
    `(?:^|\s)/skill:(\S+)(?=\s|$)` token；token 从注入正文中去掉，**token 前后的散文合并为 args**
    （与 omp 的 `parseSkillInvocation` 一致）。
  - `span=(start,end)` 供补全/TUI 高亮；`prompt` 保留用户原始输入（历史记录用）。
  - 多个 token：第一个生效（与 omp 一致）；其余原样留在 args 文本里。
- `load_skill_body(skill) -> (body, truncated)`：提交时**重读磁盘**（用户可能刚改过），
  读失败回退发现时的 `skill.body`；截断到 `MAX_SKILL_BODY_BYTES`（32KB，沿用 `skills.py`）。
- `render_skill_prompt(skill, body, args, truncated) -> str`，注入内容固定为：

  ```
  [skill:<name>] <args 单行投影，最多 200 字符，换行折成空格>

  [IMPORTANT: User invoked the "<name>" skill; follow its instructions. Full skill below.]

  <body>

  ---
  [Skill directory: <skill.path.parent 绝对路径>]
  Resolve relative paths in this skill (e.g. scripts/foo.py, templates/config.yaml) against
  this absolute directory; read referenced assets/templates and run scripts with the bash
  tool when the skill instructions call for it.

  User: <args 原文>            ← 仅 args 非空时追加
  ```

  首行是**显示标记**（TUI 识别后折叠成 chip），其余是给模型的完整上下文；
  body 被截断时在 `---` 前加一行 `[skill body truncated at 32768 bytes]`。
- `parse_skill_display(text) -> (name, args) | None`：给 TUI 转录识别首行标记用。

### 4.2 补全 / 提示（行首 + 句中）

核心是**按光标所在 token 判断模式**，而不是只看整行开头：

1. 取光标前文本的最后一个空白分隔 token（token 起点 = 光标前最后一个空白之后）。
2. 若行首是**别的** `/` 命令（`/mode ...` 等）：维持现在的“参数值补全”路径，不弹 skill。
3. 否则若 token 以 `/` 开头：
   - `token_start == 0` → **命令模式**：内置命令 + template + extension command + `skill:<name>`
     一起列出；token 是 `/skill:...` 时只列 skill。
   - `token_start > 0` → **句中模式**：只列 `skill:<name>`（句子中间只有 skill token 有意义），
     按已输入部分过滤。
4. 其余情况：不弹斜杠提示（保留 `@path` / 既有逻辑）。

- **REPL**：`ReplCompleter` 改为按 token 补全；新增 `skill_provider: Callable[[], list[tuple[str, str]]]`，
  每次 `get_completions` 时合并 skill 条目（命中 `skill:` 前缀时过滤）。补全项插入
  `/skill:<name>`；`start_position` 覆盖整个已输入 token。
- **TUI**：`on_text_area_changed` 把 `prompt.document.cursor_position` 传给 `_hint_context`；
  返回值扩展为 `(matches, prefix, start, end, mode)`；`action_hint_accept` 在句中模式下只替换
  token 范围（保留前后文与光标后内容），命令模式维持现有“清空 + 插入命令 + 空格”的行为。
- skill 列表来源：TUI 直接用 `runtime.skills`（`/skills`、`/reload` 更新后天然生效）；
  REPL 绑定自己的 `skill_list`（`/reload` 重建；`/skills` 只重扫展示，沿用现有“变更需 /reload”提示）。

### 4.3 提交

- **REPL**：`template_by_name` 展开之后、`if command:` 命令分发之前插入：
  若 `parse_skill_invocation(line)` 命中且不是 template/extension 命令：
  - 已知 skill → `render_skill_prompt(...)`；idle 时 `agent.prompt(injected, images=pending_images)`，
    运行中时 `agent.steer(injected)`；
  - 未知 skill → 打印 `unknown skill: X (available: a, b, …)`，不发送。
- **TUI**：`on_prompt_input_submitted` 在“未知斜杠命令”分支前插入同样判定：
  - 已知 → `self.history.add(text)`（保留用户原始输入）→ `await self.chat.submit(injected)`；
    运行中由 `chat.submit` 现有 follow-up 队列处理；
  - 未知 → `append_notice("unknown skill: …")`，不发送。
- skill 注入的消息不消耗模型工具调用；模型直接看到正文。approval/mode 逻辑不变。

### 4.4 显示（TUI，chip 可展开）

首行标记让显示与消息内容解耦，不改 `UserMessage` / session JSON：

- `MessageView` 增加 `kind="skill"`（`text` = name，`args`，`body`=完整注入文本去掉标记行）；
- `ChatController` 构造 user 视图时（live `message_start` 与恢复/`reload_transcript` 两处）
  先试 `parse_skill_display`；命中则建 skill 视图，否则维持普通 UserBlock；
- 新 widget `SkillBlock`：折叠一行 `✦ skill:<name> · <args>`（args 单行裁剪）；
  focusable，`enter`/`space`/点击展开显示完整注入正文（只读 Markdown/Text），再次触发折叠；
  样式与 `ThinkingRow`/tool card head 一致。
- REPL 不渲染历史，无需显示改动；session 文件里是完整注入文本，导出/回放语义正确。

### 4.5 斜杠项类型标签（Meta）

统一在补全/hint 的显示描述里加类型前缀（不改变执行优先级）：

| 类型 | 标签 | 出现位置 |
|---|---|---|
| 内置命令 | `command ·` | REPL completion meta + TUI hint |
| prompt template | `template ·` | REPL（TUI 暂不支持 template 命令） |
| extension command | `extension ·` | REPL（TUI 同上） |
| skill | `skill ·` | REPL + TUI |
| MCP（未来） | `mcp ·` | 目前 MCP 只贡献工具，不产生斜杠项；若以后支持 MCP prompts 再加 |

命名空间保证 skill 与其它类型不会在同一个菜单名上冲突（`/review` vs `/skill:review` 同时可见）。
现有真正会冲突的是 template/extension 与内置命令同名：REPL 里 template 补全覆盖命令、执行时
template 优先——类型标签让用户能看出选的是哪种；执行优先级保持不变，并在文档/测试里固定。

### 4.6 边界

- `--no-skills`：没有条目、没有任何拦截。
- skill 名与 template/extension 命令同名：命令/template 优先（`skill:` 前缀下通常不冲突）。
- 参数为空：允许，只注入正文；参数含换行：首行投影折成空格，`User:` 段保留原文。
- 注入内容超 32KB：截断并标记；不报错。
- 句中限制：草稿以别的 `/` 命令开头时不解析 skill token；代码块/引用里的 token 不做特殊处理
  （与 omp 的 token 规则一致，用户可自行避免）。
- 运行中调用：REPL 走 steer，TUI 走 follow-up（与 omp 的 steer/followUp 对齐）。

## 5. omp 证据（源码 18.14.1）

- `/skill:<name>` 命令名：`src/extensibility/skills.ts:778-780`。
- 注册进斜杠菜单（带 description、skill 图标）：`src/modes/interactive-mode.ts:2979-2996`。
- 提交时构建「用户调用」消息并注入：`src/modes/skill-command.ts:57-105`；
  渲染模板 `src/prompts/skills/user-invocation.md`；实现 `src/extensibility/skills.ts:885-928`。
- 句中 token 解析：`src/extensibility/skills.ts:808-833`、
  `pi-tui/src/prompt/skill-tokens.ts`（`SKILL_TOKEN_RE` + `allowsSkillTokens`）。
- 模型侧 skill 索引 + `skill://` MUST-read：`src/prompts/system/skillful-notice.md`、
  `src/session/agent-session.ts:11896-11940`、`src/internal-urls/skill-protocol.ts`。
- `/skills` 在 omp 是 registry 管理（search/install/update），不是列表；我们保留自己的 `/skills` 列表语义。

## 6. 改动清单（预估）

- 新增 `zenagent/app/skill_commands.py`、`zenagent/cli/tui/widgets/skill_block.py`，
  测试 `tests/test_skill_commands.py`、`tests/test_tui_skill_invocation.py`。
- 修改 `zenagent/cli/completion.py`（token 补全 + skill_provider + 类型标签）、
  `zenagent/cli/repl.py`（provider + 提交拦截）、`zenagent/cli/tui/app.py`
  （token hints + 提交拦截 + 视图 kind + accept 替换范围）、
  `zenagent/cli/tui/widgets/transcript.py`（skill 视图）、`zenagent/cli/tui/widgets/command_hints.py`
  （如需 token 前缀）、`zenagent/app/chat.py`（构造视图）、
  `docs/guide/skills-templates.md`、`docs/guide/tui.md`、`CHANGELOG.md`。
- 规模：中（约 350–450 行 + 测试），只碰前端/`app` 层，不影响 agent kernel 与工具层。

## 7. 风险与遗留决定

- 标记行是“内容里的契约”：用户手打 `[skill:...]` 作为普通消息时 TUI 会误显示成 chip；
  影响仅显示，可接受（后续可换成 `UserMessage` 可选元数据）。
- REPL 的 `/skills` 重扫不更新补全（与现有 tool 列表语义一致，提示用户 `/reload`）；TUI 是实时的。
- TUI 的 template/extension 命令支持是既有缺口，本版不补；因此 TUI 的 meta 只会出现
  `command ·` 与 `skill ·`。
- 句中补全按“光标 token”实现，不做语法高亮/代码块豁免；极端情况下可能在代码片段里弹提示，
  与现有 `/` 提示行为一致。
