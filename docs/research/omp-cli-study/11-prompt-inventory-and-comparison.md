# 11 · ZenAgent × omp 提示词全量清单与对比

> 问题：我们 zen-agent 目前有哪些 prompt？omp 在对应位置的 prompt 是怎样的？两边做个对比。
>
> 审计基线：
> - **zen-agent**：`/Users/mac/zen-agent`，HEAD **`fecc9df`**（2026-10-31 12:08:32，`merge: P2 tool-kernel into main`），分支 `main`，工作区干净。审计期间仓库从 `f363f41` 推进到 `fecc9df`（新增 `wait` 工具、改写 `bash`/`glob` 描述、plan 附注加 `wait`），本报告所有数字均按 `fecc9df` 重测。
> - **omp**：`@oh-my-pi/pi-coding-agent` **v18.14.1**，安装在 `/Users/mac/.bun/install/global/node_modules/@oh-my-pi/pi-coding-agent`（npm 包自带完整 TypeScript `src/`，以此为准；`dist/cli.js` 是压缩产物，未使用）。相关包 `@oh-my-pi/pi-agent-core`、`pi-ai`、`pi-utils`、`pi-natives`、`snapcompact` 同为 18.14.1。
>
> 口径：只统计**面向 LLM 的文本**——system prompt、工具 description、注入的上下文/技能/扩展片段、模式附注、压缩摘要提示词、标题生成提示词。**不含** UI 字符串、工具结果文案、文档、参数级 JSON Schema（单独标注参考值）。
>
> 方法：源码逐行阅读（全部结论带 `file:line`）+ **运行时真实渲染**（zen 用 `.venv/bin/python` 调 `system_prompt()` 等函数；omp 用 `bun` 直接 import 安装包的 `buildSystemPrompt()`，隔离 `HOME` 渲染「bundled 默认」和「本机实际」两种基准）。原始证据：`/tmp/zen-prompt-audit/{zen,omp-core,omp-tools}.md`（会话临时文件）。

---

## 0. 结论速览

**一句话**：zen-agent 的提示面是「一个约 790 字符的基础 system prompt + 12 条工具描述 + 一条 plan 附注 + 有上限的上下文/skills 注入 + 一个中文压缩摘要」；omp 在同样的每个位置上都有一套工程化得多的提示体系——基础提示运行时渲染约 9.9K 字符、30+3 个内置工具、261 个提示词文件/288KB，压缩是 5 级方法链而不是一条摘要提示词，plan 模式是一套 9.4K 字符的子语言。两边不是「同一张表的 1.0 和 2.0」，而是**极简内核 vs 生产级 CLI** 的两种设计。

| 维度 | zen-agent (`fecc9df`) | omp (18.14.1) | 差异 |
|---|---|---|---|
| 提示词「位置」 | **9 类（A–I）/ 21 个制品** | 同一批位置全部有，且每类再分层（见 §3） | — |
| 固定提示词总量 | **4,727 字符 / 4,999 B**（不含工具 JSON Schema） | 基础 system prompt 渲染 **9,895 字符** + project 块 465 = **10,360**；工具描述另 **7,303 B** | 运行时约 **2.6×**（基准）；本机技能全开时 **≈4.4×** |
| 基础 system prompt | **791 字符**（12 个工具名 + cwd） | 模板 **14,350 字符 / 264 行**，运行时渲染 **9,895**（默认 15 工具、默认人格、无技能） | **12.5×**（渲染值） |
| 工具 | **10 内置** + `todo` + `read_skill` = 12；描述合计 **3,209 字符** | **30 个 `BUILTIN_TOOL_NAMES` + 3 hidden ≈ 33 个内置名**（≈37 个工具类）；11 个对应位置由 8 个顶层工具承载，描述合计 **7,303 B** | 工具数 3×；同位置描述 2.45× |
| 工具描述写法 | 每工具 1–3 句平实英文 + 参数各自带 JSON Schema description | 「高密度寄存器」：MUST/NEVER 短句、结构化标签、DSL/选择器语法、把 offset/limit 折进 `path` 参数 | 最大单工具：`read` 2,025 vs `read_file` 194（**10.5×**） |
| 上下文文件 | `AGENTS.md`/`CLAUDE.md`/`.zenagent/AGENTS.md`，root→cwd，**32KB 上限**，追加 `## Project instructions` | provider 优先级发现（`.omp` 100 > `.claude` 80 > `.agent(s)` 70 > 根文件 10），**内容不截断**，`<repo-rules>` + `<dir-context>` + 反 grep 声明 | 机制完全不同 |
| Skills | 原始 description（≤120 字符）、**4KB 硬上限**、专用 `read_skill` 工具、正文 ≤32KB | 模型压缩成 **≤12 词 routing hint**（≤160 字符、SQLite 缓存）、`skill://` 经 `read` 加载、正文不截断 | 索引质量 vs 额外一次后台模型调用 |
| 会话压缩 | **1 条提示词**（201 字符，中文三节）+ system 125 字符；阈值 0.85；单次调用；**丢弃全部 toolResult** | **5 级方法链**（remote→snapcompact→handoff→shake→soft）+ **7 个 LLM 摘要变体**（system 487 / 首次 1,206 / update 1,396 / 单轮前缀 572 / 短摘要 251 / handoff 1,497 / 分支 662…）；防注入；多窗口折叠 | 两个物种 |
| Plan 模式 | **283 字符**附注（只读工具清单 + 禁止修改） | **9,440 字符**主提示 + 8 个配套模板 + `plan-mode-guard` 硬拦截 + `local://<slug>-plan.md` + `xd://propose` 审批 | 段落 vs 子语言 |
| 装配 | 单个字符串，每个前端各自拼（A→D→E→G→C） | **有序 system 块数组**（base → eval preludes → project-context），显式缓存断点；日期/cwd 移到首个 user turn 保字节稳定 | 缓存工程 |
| 自定义/扩展 | 扩展 `add_system_prompt()` 追加片段；`/commands` 模板是用户文件 | `SYSTEM.md`/`SYSTEM_TEMPLATE.md` **整块替换** + `APPEND_SYSTEM.md` 权威追加 + 扩展片段 | 替换 vs 追加 |
| 标题生成 | **没有** | 侧请求提示词：`title-system.md` 1,530 + `title-fork.md` 3,161 + `title-card.md` 2,769 + marker 317 | zen 缺失 |
| 提示词库规模 | 全仓库就这 21 个制品 | `src/prompts/` **261 个 .md / 288,140 B**；`src/` 下全部 .md **321 个 / 378,481 B**（含 26 条 builtin rules） | — |

**最值得注意的 5 个差异**

1. **zen 的提示词是「够用就好」的扁平结构**：一个函数 `system_prompt(tool_names)` 返回整段文本，四个前端各自按固定顺序拼上下文/技能/扩展/模式。omp 用 Handlebars 模板 + 20 多个条件变量按工具集/设置/模型能力裁剪，并把稳定段与易变段拆成不同 system 块以命中 prompt cache。
2. **omp 的提示词是「规格书」写法**：`read` 一个工具用 2,025 字符讲清路径选择器、九类来源、归档/JSON/PDF/二进制/URL；`§ Tool Policy`/`§ Workflow`/`§ Delivery` 共 5.6K 字符全是 MUST/NEVER 断言。zen 的 12 条工具描述总共 3.2K 字符，风格是给人读的平实英文。
3. **压缩是两套哲学**：zen 用一条中文提示词做一次性摘要，直接丢 toolResult；omp 默认优先 provider 原生压缩，其次把历史渲染成位图让 vision 模型读（snapcompact，无 LLM 调用），再 handoff 文档、shake，最后才是 LLM 摘要；摘要本身是 8 节 handoff 格式 + 迭代 update + 多窗口折叠，并显式把历史当不可信数据防注入。
4. **omp 的提示面里包含大量 zen 根本没有的「产品级」位置**：标题生成、子代理 system prompt、commit message、memory 提取/合并、advisor、goals、security、judge、bench……zen 全仓库只有 **2 个模型调用点**（主 agent loop + 压缩摘要），omp 有几十个。
5. **但 zen 是「有意识地」向 omp 靠拢的**：`zenagent/app/support.py:37-46` 的 docstring 明确写「The policy section mirrors omp's Specialized / General / Exploration split (`docs/research/omp-cli-study/10-tools-gap.md` P1-9)」。所以两边的关系不是无关产品，而是 **omp 的功能面被蒸馏成 zen 的最小内核**——本报告的价值就在于看清「哪些被留下了、哪些被刻意砍掉了」。

---

## 1. 统计口径与方法

| 项 | 说明 |
|---|---|
| 「prompt」定义 | 会进入模型上下文的自然语言文本：system prompt、工具 description、注入的项目上下文/技能索引/扩展片段、模式附注、压缩摘要提示词、标题生成提示词。 |
| 不计入 | UI 文案（确认框、状态栏）、工具执行结果、文档、参数级 JSON Schema（参考值单列）、MCP/扩展工具的外部 description（随环境变化）。 |
| 字符 vs 字节 | `chars` = Unicode 码点（Python `len` / JS `.length`）；`bytes` = UTF-8。中文提示词两者差 2 倍以上，报告尽量同时给。 |
| zen 测量 | `.venv/bin/python` 直接导入函数实测（`system_prompt()`、`mode_prompt_note()`、`SUMMARIZE_PROMPT`、`build_todo_tool()`、`build_skill_tool()`）。 |
| omp 测量 | `bun` 直接 import 安装包 `src/system-prompt.ts` 的 `buildSystemPrompt()`：**基准 A** = 隔离 `HOME`/`PI_CODING_AGENT_DIR`、空项目、无技能/规则/上下文；**基准 B** = 本机真实配置、`cwd=/Users/mac/zen-agent`、发现 160 个技能。工具描述用 `prompt.render(template, flags)` 按默认设置渲染。 |
| 环境数据 | zen 本机 `~/.zenagent/skills` 15 个 skill；omp 本机发现 160 个技能。这些是用户环境数据，不属于仓库固定提示词，单独标注「本机实测」。 |
| 版本漂移 | zen 仓库在审计期间从 `f363f41` 推进到 `fecc9df`，提示面确实变了（`ALL_TOOLS` 9→10 等）。本报告已按 `fecc9df` 重测；引用前请确认 `git log -1` 仍是 `fecc9df`。 |

---

## 2. zen-agent：9 个位置 / 21 个制品

### 2.1 装配管线（提示词如何进入请求）

- **唯一注入点**：提示词不写入会话 JSONL，而是在每次 LLM 请求前临时构造为第一条 `SystemMessage`（`zenagent/agent/loop.py:532-535`，注释 "the state never sees it"）。工具描述走独立通道：`ToolDef(name, description, parameters)` 进入 provider 的 tool schema（`agent/loop.py:539`）。
- **四个前端各拼各的**（`zenagent/app/runtime.py`、`cli/main.py`、`cli/repl.py`、`cli/rpc.py`），固定顺序：

```
system_prompt(tool_names)                 # A 基础 system prompt（含 cwd + 工具名单）
  → system_prompt_with_context(...)       # D 项目上下文（默认开）
  → skills_prompt_index + extension_text  # E skills 索引 → G 扩展片段
  → mode_prompt_note(mode)                # C plan 附注（仅 plan 模式）
```

- TUI/headless/REPL 的工具面 = `ALL_TOOLS` → `todo` → MCP → `read_skill` → 扩展工具；RPC 只有 `ALL_TOOLS + todo`（不注入 skills/扩展/plan）；eval 脚本用另一条更短的基础提示词（`scripts/eval.py:212-219`）。
- 提示词在下一轮生效：`Agent.prompt()` 每次读取当前 `system_prompt`（`agent/agent.py:204`），所以 `/mode`、`/reload` 无需重启。

### 2.2 位置 A–I 全量清单

#### A. 基础 system prompt — `zenagent/app/support.py:37-78`

固定文本 **791 字符**（10 内置 + `todo` + `read_skill` 共 12 个工具名 + 本机 cwd 20 字符；无 todo/read_skill 时 609）。全文：

```text
You are zenagent, a coding agent working in the user's repository.
Working directory: /Users/mac/zen-agent
Available tools: read_file, write_file, edit_file, list_dir, bash, wait, glob, grep, web_search, fetch, todo, read_skill
Tool policy:
- Specialized: prefer read_file, list_dir, glob, grep, web_search and fetch over shell equivalents.
- General: use bash only when no specialized tool fits.
- Exploration: never guess file contents; read or grep the code before changing it.
- Todo: for work with 3+ steps, call todo in the same response as your plan and keep it current; address tasks by their exact content (start/done/block as you go).
- Parallel: batch independent tool calls in one response; keep dependent calls sequential.
Answer concisely. Explain what you did when you finish.
```

（`- Todo:` 行仅当 `todo` 在工具面时出现，`support.py:66-71`。）

#### B. 工具描述（12 个，tool definition 通道）

当前 `ALL_TOOLS`（`zenagent/tools/__init__.py:29-40`，顺序即 prompt 顺序）：
`read_file, write_file, edit_file, list_dir, bash, wait, glob, grep, web_search, fetch`（10 个）；另有条件工具 `todo`（有 session 时）与 `read_skill`（有 skill 时）。

| 工具 | file:line | 描述字符数 | 全文/摘要 |
|---|---|---|---|
| `read_file` | `tools/fs.py:434-437` | 194 | 读文本文件，支持 offset/limit 与截断提示 |
| `write_file` | `tools/fs.py:459-461` | 198 | 创建/覆盖文件；自动建父目录；返回写入字节数 |
| `edit_file` | `tools/fs.py:473-476` | 259 | 精确字符串替换；要求 old_string 唯一；失败时提示重读 |
| `list_dir` | `tools/fs.py:501` | 61 | 列出目录条目（非递归） |
| `bash` | `tools/bash.py:210-216` | **312** | `Run a shell command. … With async=true the command runs in the background and the wait tool collects its result.` |
| `wait` | `tools/wait.py:163-168` | **233** | `Wait for a background bash job started with bash async: true …`（P2 新增） |
| `glob` | `tools/search.py:312-316` | **153** | `Find files by glob pattern … Hidden files are skipped unless include_hidden; returns at most limit paths (default 500).` |
| `grep` | `tools/search.py:342-346` | 261 | 正则搜索；ripgrep 优先；分页（20 文件 × 20 命中） |
| `web_search` | `tools/web/tools.py:187-197` | 629 | 网络搜索；支持 site:/filetype:/intitle: 等语法；返回标题/URL/摘要 |
| `fetch` | `tools/web/tools.py:222-223` | 109 | 抓取 URL，正文转 Markdown，截断 |
| `todo` | `tools/todo.py:722-733` | **719** | 分阶段任务清单；`init/add/start/done/block/…` 语义；**最大单条静态字面量** |
| `read_skill` | `zenagent/skills.py:135-138` | 81 + 动态技能名 | `Read a skill's instructions by name`（名字清单嵌入描述，本机 15 个 skill → 416 字符） |

内置描述合计 **2,409 字符**；加 `todo`+`read_skill` 共 **3,209 字符**。参数级 JSON Schema 另计 4,371 字符（12 工具面，不计入总数）。MCP 工具描述原样透传（`zenagent/mcp/manager.py:168-185`）。

#### C. Plan 模式附注 — `zenagent/app/modes.py:66-75`

**283 字符**，仅 `mode=plan` 时追加在 system prompt 末尾（`runtime.py:142-146`）：

```text
PLAN MODE: you are in read-only plan mode. Investigate with read-only tools (read_file, list_dir, glob, grep, web_search, fetch, wait), then present a concise, step-by-step plan. Do not modify files or run mutating commands; the user switches to ask or full mode to execute the plan.
```

#### D. 项目上下文文件 — `zenagent/context_files.py`

- 文件名：`AGENTS.md`、`CLAUDE.md`、`.zenagent/AGENTS.md`（每目录第一个命中生效）。
- 发现：从仓库根（第一个含 `.git` 的目录）到 cwd，root 在前、cwd 在后；无仓库边界时只看 cwd（`context_files.py:36-67`）。
- 上限：总计 **32,768 B**，超出截断并标注（`context_files.py:24,61-88`）。
- 注入包装（`context_files.py:83-88`）：`"{base}\n\n## Project instructions\n{context_text}"`，每个文件一节 `### {path}\n{body}`。
- 开关：`--no-context-files`。

#### E. Skills 索引 — `zenagent/skills.py:151-166`

- 页眉固定 **83 字符**：`## Skills` + `Load a skill with the read_skill tool before following its instructions:` + 空行。
- 每条 `- {name}: {description}`；description 取 SKILL.md frontmatter 或首行，截断 **120 字符**。
- 索引总上限 **4,096 B（软上限）**，超出追加 `- ... more skills omitted`。
- `read_skill` 正文上限 **32,768 B**，超出追加 `[... skill truncated ...]`。
- 发现根：`<project>/.zenagent/skills` 优先，然后 `~/.zenagent/skills`（`skills.py:169-178`）。
- 本机 15 个用户 skill → 索引 **2,788 字符**（占 TUI 实跑 system prompt 的 78%）。

#### F. 压缩摘要提示词 — `zenagent/app/compact.py`

- `_SUMMARIZE_SYSTEM`（**125 字符**，`compact.py:48-51`）：`You compress coding-session transcripts into a compact handoff summary. Be faithful to the transcript and never invent facts.`
- `SUMMARIZE_PROMPT`（**201 字符 / 461 B**，`compact.py:36-46`）：

```text
请把下面这段编码会话压缩成一份可继续工作的摘要。

要求：
1. 用中文，分三节：**任务目标**、**已做出的决定**、**未竟事项**。
2. 保留关键文件路径、命令、错误信息与结论；不要保留寒暄与重复内容。
3. 不要编造会话里没有的事实；信息不足就写"未提及"。
4. 只输出摘要正文，不要加开场白。

===== 会话记录 =====
{transcript}
===== 记录结束 =====
```

- 触发：`AUTO_COMPACT_THRESHOLD = 0.85`，`usage.input + usage.cache_read >= context_window * 0.85`，且只在两次 run 之间（`compact.py:34,87-121`）。
- 转录渲染 `_render_transcript()`：user/assistant/thinking/tool_call 都保留，**跳过所有 toolResult**（"tool output is summarized by the assistant's own text"）。
- 结果：新 session 只含一条 user 消息 `# 此前会话摘要\n{summary}`（`compact.py:159-170`），失败不改状态。

#### G. 扩展提示片段 — `zenagent/extensions.py:63-65,81-85`

扩展可调 `api.add_system_prompt(text)` 注册片段；`LoadedExtensions.prompt_text` 用 `\n\n` 连接，运行时拼在 skills 索引之后（`runtime.py:362-365`；headless `main.py:286-292`；REPL `repl.py:605-611`）。无内置扩展 → 默认 0 字符。

#### H. 提示词模板（`/commands`）— `zenagent/templates.py`

- 机制：`.zenagent/commands/<name>.md`（项目优先，再用户 home），frontmatter 可覆盖 name/description；`{{args}}`/`{{args:default}}` 展开。
- **仓库内置 0 个模板**；只在 REPL 里展开成 user 消息（`repl.py:836-840`）。这不是 system prompt，但是用户可定义的 prompt 面。

#### I. 其他 LLM-facing 内容：全部缺席（有证据）

全仓库只有 **2 个模型调用点**：主 agent loop 与压缩摘要。**没有**标题生成、子代理、commit message、记忆提取/合并、judge、plan review 等提示词（`grep` 证据见 `zen.md` §2.I）。唯一例外是评测脚本里的开发用文案 `"You are zenagent, a coding agent. Be concise."`（`scripts/eval.py:212-219`，非产品运行面）。

### 2.3 zen 关键数字

| 指标 | 值 |
|---|---|
| 固定提示词合计（A+B+C+D 包装+E 页眉+F+F-3 前缀） | **4,727 字符 / 4,999 B** |
| 基础 system prompt | 609（10 工具）/ 779（+todo）/ **791**（+read_skill，TUI 典型） |
| 工具描述 | 内置 2,409；含 todo/read_skill **3,209** |
| 工具 JSON Schema（参考，不计入） | 4,371 字符 |
| 本 checkout TUI 实跑 system prompt（15 skill、无 AGENTS.md） | **3,581 字符 / 3,587 B**；plan 模式 3,866 / 3,872 B |
| 单条最大固定提示词 | 基础 system prompt 791；最大静态字面量 `todo` 719 |

---

## 3. omp：对应位置的 prompt

### 3.1 装配：有序 system 块数组 + 缓存工程

`src/system-prompt.ts:651-1083` 的 `buildSystemPrompt()` 返回 **`string[]`**，不是单条字符串：

| # | 块 | 条件 | 证据 |
|---|---|---|---|
| 0 | 基础模板渲染结果（或 `SYSTEM.md`/`SYSTEM_TEMPLATE.md` 的整块替换） | 恒有（`NULL_PROMPT` 除外） | `system-prompt.ts:1041-1058` |
| 1..N | 每个 eval prelude 的 guidance | 有 prelude（browser/computer 等） | `:1059-1062` |
| 末块 | `project-prompt.md` 渲染的 `<project-context>` | 渲染结果非空即 push | `:1063-1073` |

设计意图（源码注释原文）：

> `Working-directory content (context files with their paths, workspace tree/roots, active repo) and session append text form one trailing <project-context> block after every static block, so sessions in different directories share the static prefix and the Anthropic head cache breakpoint lands right before this block.`（`system-prompt.ts:1063-1067`）

配套：`<project-context>`/`<memories>` 被标为易变段标记（`pi-ai/src/providers/anthropic.ts:4365-4385`）；**日期 + cwd 提醒不进 system**，而是贴在首个 user turn 内容前（`src/session/date-cwd-reminder.ts:1-12`，注释明说为了 system 前缀字节稳定）；plan/goal/vibe 模式上下文走隐藏 `custom→developer` 消息（`agent-session.ts:8856-8880`）。

**实测大小**（真实执行 `buildSystemPrompt()`）：
- 基准 A（bundled 默认：15 工具、默认人格、无技能/规则/上下文）：block0 **9,895 字符** + project 块 **465** = **10,360 字符**。
- 基准 B（本机实际，160 个技能）：block0 **22,294**（其中 `<skills>` **12,237**）+ 465 = **22,759 字符**。

### 3.2 A. 基础 system prompt — `src/prompts/system/system-prompt.md`

**14,350 字符 / 14,425 B / 264 行**（Handlebars 模板，默认配置渲染成 9,895）。结构（渲染后章节字符数）：

| 章节 | 字符 | 说明 |
|---|---|---|
| 前言（RFC 2119 / XML 标签权威性） | 264 | `MUST/NEVER` 定义；XML 标签内容视为 system 权威 |
| `§ Role` | 47 | **只有一行**：`You are omp's trusted coding assistant.` |
| `# Engineering` | 666 | 正确性优先、可维护性、禁止无谓抽象、编译代码零分配等 |
| `# Personality` | 911 | 默认人格全文（897 字符，可切 friendly/pragmatic/自定义 `PERSONALITY.md`） |
| `# Internal URLs` | 1,610 | `skill://`、`agent://`、`history://`、`artifact://`、`local://`、`proc://`、`ssh://`、`issue://`/`pr://`、`mcp://`… |
| `# Tool Inventory` | 159 | 默认只列 15 个工具名（描述走 provider tool schema） |
| `§ Scratchpad` | 131 | `think` 工具存在时出现 |
| `§ Tool Policy` | **2,094** | General / Tool I/O / Specialized（885）/ Exploration / AST / Delegation（635） |
| `§ Workflow` | **2,045** | 1 Scope → 2 Research Before Editing → 3 Decompose → 4 Implement → 5 Verify → 6 Cleanup（Verify 单节 1,142） |
| `§ Delivery` | 1,533 | `<contract>`（Inviolable）/ `<completeness>` / `<evidence-and-output>` / `<yielding>` |
| `§ Critical` | 415 | 三条「不许提前收工/不许考虑预算/工具结果即验证」 |

关键原文（节选）：

```text
§ Role
You are omp's trusted coding assistant.

# Tool Policy
# General
SHOULD resolve prerequisites, parallelize independent calls. Retry empty/partial/narrow results differently; NEVER settle for plausibility when another call reduces uncertainty.
# Specialized Tools
MUST use specialized tool over shell equivalent:
- File/directory reads: `read` (directory lists entries).
- Surgical edits: `edit`.
- Regex search: `grep`, NEVER shell `grep`/`rg`/`awk`.
- File structure/names: `glob`, NEVER `ls **/*.ext`/`fd`.
- `bash`: real binaries/short fact pipelines, NEVER specialized-tool work.
<critical>
NEVER use `sed`|`perl`|`python` via `bash` to issue individual edits; MUST use `edit`.
</critical>
# Exploration
NEVER open guessed files. Use `read` ranges, not whole files.

# Workflow
# 1. Scope
- Plan multi-file work before opening files.
# 5. Verify
Non-trivial work: NEVER yield without a smoke run: run the thing, exercise the changed path, observe the result. Tests alone are not proof.

§ Delivery
<contract>
Inviolable.
- NEVER fabricate output; ground code/tool/test/doc/source claims; unobserved = `[INFERENCE]`.
- NEVER substitute easier/familiar problem …
- Default clean cutover: migrate every caller; remove obsolete code …
</contract>

§ Critical
<critical>
- NEVER yield before complete deliverable or while actionable work remains …
- NEVER narrate/consider session limits, token/tool budgets, effort estimates …
</critical>
```

工具政策/Workflow 的每一行都绑定工具存在性（`{{#has tools "lsp"}}` 等）——工具不存在，该行整条消失。人格外置为可切换文件（`src/prompts/system/personalities/{default,friendly,pragmatic}.md`，默认 897 字符），身份只有一行。

### 3.3 B. 工具描述（omp 侧）

**注入机制**（与 zen 的单路径不同）：
- 默认（`inlineToolDescriptors=auto` 且非 Gemini）：完整描述在 **provider tool schema 的 `description` 字段**，system prompt 里只有 `# Tool Inventory` 的工具名清单（`src/session/settings.ts:393-421`、`src/system-prompt.ts:919,941-953`）。
- Gemini 或 `inlineToolDescriptors=on`：完整目录以 Harmony `namespace functions { … }` 格式内联进 system prompt，并从 schema 剥除描述避免双计费（`@oh-my-pi/pi-agent-core/src/agent-loop.ts:1039-1064`）。
- 会话内工具变化：`tool-roster-notice.md`（291 字符）作为隐藏 `custom` 消息（`session-tools.ts:1798-1808`）。
- `xd://` 设备挂载默认开启（`tools.xdev`），低频工具 schema 不随每次请求发送（`src/tools/xdev.ts:58-90`）。

**11 个对应位置的映射与体量**（描述为默认设置下的渲染值）：

| zen 位置 | omp 对应 | 提示词源 | omp 渲染字符 | zen 字符 |
|---|---|---|---|---|
| `read_file` | `read` | `src/prompts/tools/read.md`（模板 2,388） | **2,025** | 194 |
| `write_file` | `write` | `write.md`（模板 299） | 298 | 198 |
| `edit_file` | `edit` | 原生 addon `editDescription(mode)`（无 .md；默认 hashline） | 1,955 | 259 |
| `list_dir` | **无专用工具**，折入 `read` 目录读取 | `read.md:10` 分支 | ≈120 | 61 |
| `glob` | `glob` | `glob.md`（模板 438） | 298 | 153 |
| `grep` | `grep` | `grep.md`（模板 643） | 549 | 261 |
| `bash` | `bash` | `bash.md`（模板 1,134） | 900 | 312 |
| `web_search` | `web_search` | `web-search.md`（模板 314） | 187 | 629 |
| `fetch` | **无专用工具**，折入 `read` 的 URL 读取 | `read.md:15` 分支 | ≈120 | 109 |
| `todo` | `todo` | `todo.md`（模板 1,064） | 1,063 | 719 |
| `read_skill` | **无专用工具**：`skill://` 经 `read` + `/skill:<name>` + autoload | `system-prompt.md:40-49`、`prompts/skills/*.md`、`internal-urls/skill.md` | 索引每行 ≤160 | 81+names |

合计：**8 个顶层工具 7,303 B** vs zen 11 位置 2,976 字符（去掉无对应位置的 `wait`）——**2.45×**。最大单工具 `read` 2,025 vs `read_file` 194（**10.5×**）。

`read.md` 的写法（开头 3 行即可见风格差异）：

```text
Use `read` for static web; browser only if needed.

Put line ranges in `path`, never a separate range/offset/limit parameter: `{"path":"notes.txt:450-455"}` reads lines 450-455; `{"path":"notes.txt:-5"}` reads the last 5. …
Path suffixes: :50 or :50- starts at line 50; :50-200 inclusive; :50+150 counts lines; :-60 last 60; commas join ranges … :raw verbatim … :conflicts lists one line per unresolved merge block. …
Sources:
- Bare code: declarations only; re-read ONLY footer-named omissions …
- Directory: complete root; child listings cap at 12 …
- SQLite: file.db tables; :table schema/rows; :table:key by primary key; ?limit=, ?where=, ?q=SELECT.
- Archives: ZIP/JAR/APK/WHL, compressed TAR, RAR/7z/ISO/CAB/DEB/RPM/CPIO/AR/LZH/ARJ/ASAR …
- JSON/JSONL: file.json?q=<jq filter>; …
- PDF/documents; notebooks; images; URLs …
```

工具家族：`BUILTIN_TOOL_NAMES` **30 个** + `HIDDEN_TOOL_NAMES` 3 个（`yield/goal/think`）= 33 个内置名，另加 browser/computer/tts/generate_image/vibe_* 等模式门控类 ≈ **37 个工具类**（`src/tools/builtin-names.ts:1-34`、`src/tools/index.ts:570-607`）。`src/prompts/tools/` 共 **85 个 .md / 109,304 B**（含大量运行时错误/提示片段，不全是 description）。

### 3.4 C. Plan 模式 — 一套子语言

- 主提示 `src/prompts/system/plan-mode-active.md`：**9,440 字符 / 9,528 B / 123 行**。注入为 `custom→developer` 消息，排在 user turn 之前（`agent-session.ts:7872-7909`），渲染后 6.6K–7.8K 字符。
- 结构：`## What a plan is` / `## Plan file` / `## Ground every claim` / `## Re-entry` / `## Workflow — iterative` / `## Workflow — parallel` / `## Plan contents`。
- 核心规则（原文节选）：

```text
<critical>
Plan mode active.
- Working tree/system read-only: NEVER create, edit, delete, or rename working-tree files; NEVER run state-changing commands …
- Canonical plan: MUST write `local://<slug>-plan.md`.
Implementing: write the plan `<slug>`/title, plain text, to `xd://propose` with `write`; … User then selects an execution option; full write access restored.
NEVER ask user to exit plan mode or request approval in prose/with `ask`; approval ONLY via `xd://propose` write.
</critical>

## What a plan is
Plan: execution spec, not design doc. … A competent implementer unfamiliar with the conversation MUST execute top-to-bottom with ZERO design decisions; file contains every choice.
Detail removes implementer decisions, not padding. A plan with Non-Goals, Alternatives, or risk matrices but an open decision … FAILED. Decision-completeness > brevity.
```

- 配套 8 个模板：`plan-mode-reference.md`(448) / `plan-mode-approved.md`(900) / `plan-mode-compact-instructions.md`(664) / `plan-mode-subagent.md`(864) / `plan-mode-tool-decision-reminder.md`(412) / `plan-yolo-handoff.md`(291) / `plan-filename.md`(469) / `plan-mode-active.md` 本体。
- 强制层在工具侧：`src/tools/plan-mode-guard.ts:129-150` 对写操作硬抛错；`local://` 与 `xd://propose` 两个内部 scheme 把「计划文件」与「审批」解耦。

### 3.5 D. 上下文文件 — `src/prompts/system/project-prompt.md`

`project-prompt.md`（模板 2,744 字符）渲染出 system 的最后一个块；空项目实测 **465 字符**：

```text
<project-context>
<workstation>
- OS: darwin 27.2.0
- Arch: arm64
- Model: anthropic/claude-sonnet-4-5
</workstation>

<repo-rules>
MUST follow these context files for all tasks:
<file path="…">…</file>
</repo-rules>

<dir-context>
Some directories may have rules; deeper rules override higher ones.
Before changes in these directories, MUST read:
- …
</dir-context>

Context files above auto-loaded. NEVER `grep`/`glob` for `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, or similar agent/context files: relevant files already in context; others noise.
…
<critical>
- Each response MUST advance the task; completion only stopping condition.
- MUST default to informed action; do not ask for confirmation when tools or repo context can answer.
- Before yielding, MUST verify significant behavioral changes: run the specific test, command, or scenario covering the change.
</critical>
```

- 发现：provider 优先级 first-wins（`.omp` 100 > `.claude` 80 > `.agent(s)` 70 > 根 `AGENTS.md`/`CLAUDE.md` 仅 10），同深度只保留一个；向上遍历有边界；支持 `@import`。
- 上限：**文件内容不截断**；只对嵌套 AGENTS.md 路径清单限 200 条（`AGENTS_MD_LIMIT`）、workspace tree 限 120 行（默认关闭）。
- 去重：近者包含远者则丢弃远者（段落级包含去重）。

### 3.6 E. Skills — 索引是「模型压缩后的 routing hint」

- 索引在 system prompt 的 `# Skills & Rules` 段（`system-prompt.md:40-49`）：`Matching skill → MUST read \`skill://<name>\` first.` + `<skills>` 每行 `- name: description`。
- 关键差异：description 不是 SKILL.md 原文，而是 `SkillDescriptionCatalog` 用 **smol 模型**压缩成 **≤12 词 / ≤160 字符**的 routing hint（`src/prompts/skills/compress-description.md` 273 字符；SQLite 缓存 `skill-descriptions.db`；未命中先用确定性预览，后台压缩）。压缩指令原文：

```text
Compress into one routing hint of at most 12 words: WHEN to use; WHEN NOT only if it prevents confusion. Retain recognizable trigger words. Do not restate the skill name or market it. Output one line without bullets or quotes.
```

- 加载：`read skill://<name>`（内部 URL 解析，正文不截断）；用户 `/skill:<name>` 用 `user-invocation.md`(411) 注入；子代理 autoload 用 `autoload.md`(79)。
- 本机 160 个技能 → `<skills>` 块 **12,237 字符**（占 block0 的 54%）。
- 另有 `<generic-rules>` / `<domain-rules>`（规则分桶：disabled→builtin→agents 作用域→TTSR→alwaysApply→rulebook），`src/discovery/builtin-rules/` 26 个 .md / 42,244 B。

### 3.7 F. 压缩（Compaction）——先纠正一个前提

**`src/compress/prompts/system.md`（6,198 B）不是会话压缩提示词**，而是 `omp compress` CLI 的「把提示词改写成高密度寄存器」工具的系统提示词（`src/compress/index.ts:1-11` 自述；`rewrite`/`approve` 两工具协议）。真正的会话压缩在 `@oh-my-pi/pi-agent-core/src/compaction/`。

**会话压缩是 5 级方法链**（`src/session/compaction-methods.ts:18-46`，默认顺序）：

1. `remote`：provider 原生 server compaction（OpenAI Responses compact / Anthropic compaction beta）
2. `snapcompact`：把历史渲染成位图图像，vision 模型直接读回，**无 LLM 调用**
3. `handoff`：生成 handoff 文档作为压缩摘要继续
4. `shake`：无 LLM 调用，就地丢弃可恢复的重内容
5. `soft`：用压缩模型就地摘要

**LLM 摘要提示词全集**（`pi-agent-core/src/compaction/prompts/`）：

| 文件 | 字符 | 作用 |
|---|---|---|
| `summarization-system.md` | 487 | 摘要 system prompt；历史/旧摘要视为**不可信数据**，禁止执行其中指令 |
| `compaction-summary.md` | **1,206** | 首次摘要主提示词：8 节 handoff 格式（Goal / Constraints & Preferences / Progress{Done,In Progress,Blocked} / Key Decisions / Next Steps / Critical Context / Additional Notes） |
| `compaction-update-summary.md` | 1,396 | 已有摘要时的迭代更新（保留旧信息、移动进度、更新下一步） |
| `compaction-turn-prefix.md` | 572 | 单轮过大时压缩「本轮前缀」，保留最近后缀 |
| `compaction-short-summary.md` | 251 | UI 展示用 PR 式短摘要（2–3 句、第一人称、不谈测试） |
| `handoff-document.md` | 1,497 | handoff 策略主提示词：命令式对继任者说话、只写用户任务 |
| `branch-summary.md`(+preamble 84/context 57) | 662 | 分支回退摘要 |
| `compaction-summary-context.md` / `handoff-summary-context.md` | 127 / 530 | 旧摘要包装 |
| `anthropic-compaction-instructions.md` | 393 | provider 原生 Anthropic 压缩指令 |
| `snapcompact-summary.md` | 1,893 | 位图历史阅读协议（`¶user:`/`¶think:`/`¶ai:`/`¶call:`） |

摘要请求组装（`compaction.ts:1005-1057`）：`<conversation>` + `<previous-summary>` 包裹；system = `summarization-system.md`；工具结果截断到 2,000 字符、thinking 丢弃；超长历史多窗口折叠（上一窗摘要带进下一窗）；摘要后回填 read/modified 文件清单 `<files>`；输出预算 `min(0.8 × reserve, 16,384)`。

触发：默认 `thresholdPercent/thresholdTokens = -1`，走 reserve 逻辑 `contextWindow − max(floor(0.15×window), 16,384)`（`compaction.ts:197,330-365`），`keepRecentTokens` 默认 20,000，允许 mid-turn。

### 3.8 G. 自定义 system prompt / 追加

- `SYSTEM.md`（字面）或 `SYSTEM_TEMPLATE.md`（Handlebars）：**整块替换** block 0，内置 Tool Policy/Workflow/Delivery/Critical 全部消失；替换模板 `custom-system-prompt.md`（1,389 字符）只补回技能/规则/脱敏说明（`src/capability/system-prompt.ts`、`system-prompt.ts:1041-1043`）。
- 追加是另一条路径：`APPEND_SYSTEM.md` → `composeAppendPrompt()`（`system-prompt.ts:640-649`），落在 project 块末尾，模板 `user-append.md`（201 字符）显式声明 `## User Instructions` **权威高于前文**。
- 扩展片段：`src/extensibility/` 通过 SDK 注入（与 zen 的 `add_system_prompt` 类似，但 omp 另有替换/追加两条正式路径）。

### 3.9 I. 标题生成（zen 没有）

- 触发：首条 operator 消息的回复开始后，**独立侧请求**（默认从流式回复 fork 一个同模型 ephemeral turn，失败降级 tiny 模型），不进主对话（`src/utils/title-generator.ts`、`agent-session.ts:10868-10921`）。
- `title-system.md`（1,530 字符）核心：

```text
You name coding-agent sessions. Write a 3-6 word title for the task in the next message.
Output exactly one line: the title inside `<title>` tags, or `<title/>` when there is no task to name.
Rules:
- Name the user's goal as a short imperative phrase built from the user's own key words …
- Answer `<title/>` for greetings, acknowledgements, gibberish, or requests too vague …
- No quotes, trailing punctuation, or explanations.
```

- 另有 `title-card.md`(2,769)、`title-fork.md`(3,161)、`title-marker-instruction.md`(317)；输出用正则抓 `<title>…</title>`（不用 forced tool_choice，注释解释模型会把 JSON 示例当标题回显）。

### 3.10 omp 提示词库全景

| 目录 | .md 文件数 | 字节 |
|---|---|---|
| `src/prompts/system/` | 106 | （system 模板族：基础/人格/plan/标题/模式/错误重试/snapcompact/autolearn/…） |
| `src/prompts/tools/` | 85 | 109,304 |
| `src/prompts/internal-urls/` | 17 | |
| `src/prompts/memories/` | 9 | |
| `src/prompts/agents/` | 8 | |
| `src/prompts/goals/` / `bench/` / `advisor/` | 6 / 6 / 6 | |
| `src/prompts/session/` / `skills/` / `security/` / `steering/` | 4 / 3 / 3 / 2 | |
| 顶层单文件（review/ratchet/ci-green/…） | 6 | |
| **`src/prompts/` 合计** | **261** | **288,140** |
| `src/` 下全部 .md（含 `discovery/builtin-rules/` 26 个 / 42,244 B、compress/commit/cleanse/autoresearch 等） | **321** | **378,481** |

另：`src/compress/prompts/` 3 个 7,022 B；`src/commit/` 家族 13 个；`src/cleanse/prompts/` 3 个 4,504 B；`src/autoresearch/` 4 个 8,665 B。

---

## 4. 两边对比

### 4.1 规模对比（同口径）

| 口径 | zen-agent | omp | 倍数 |
|---|---|---|---|
| 基础 system prompt（固定/渲染） | 791 字符 | 9,895（基准）/ 22,294（本机） | 12.5× / 28× |
| 工具描述（对应位置） | 3,209 字符（12 工具） | 7,303 B（8 顶层工具覆盖 11 位置） | 2.27–2.45× |
| 单次请求的提示总量（system + 工具描述） | 3,581 + 3,209 = **6,790**（本机 15 skill） | 10,360 + 7,303 = **17,663**（基准）/ 22,759 + 7,303 = **30,062**（本机 160 skill） | **2.6× / 4.4×** |
| plan 模式额外 | +283 字符 | +6.6K–7.8K 字符 | ≈25× |
| 压缩提示词 | 326 字符（1 条 + system） | 7 个变体合计 ≈5,500+ 字符（不含 snapcompact 1,893 / 分支 803 / handoff 1,497） | ≈17× |
| 标题生成 | 0 | ≈7,800 字符（4 个模板） | ∞ |
| 提示词库文件数 | 21 个制品（散在 6 个模块） | 261（`src/prompts/`）/ 321（`src/` 全部） | — |

> 注意：omp 的 9,895 是「默认 15 个 CLI 工具 + 默认人格 + 无技能/规则」的干净基准；本机 22,294 里 12,237 是用户环境发现的 160 个技能索引。zen 的 3,581 里 2,788 是本机 15 个技能索引。两边都受环境影响，但 omp 的「固有底座」仍大约是 zen 的 12–16 倍。

### 4.2 同一位置逐项对照

| 位置 | zen-agent | omp | 关键差异 |
|---|---|---|---|
| **A 基础 system prompt** | 791 字符：身份 + cwd + 工具名单 + 5 条 policy + 收尾 | 9,895 字符：Role 1 行 + Engineering + Personality + Skills&Rules + Internal URLs + Tool Inventory + Tool Policy(6 子节) + Workflow(6 步) + Delivery(4 契约) + Critical；Handlebars 条件裁剪 | zen 是「最小行为约束」；omp 是「工程规范 + 交付契约」 |
| **B 工具描述** | 12 条，1–3 句平实英文，参数语义放 JSON Schema；总 3,209 字符 | 8 顶层工具承载 11 位置；高密度寄存器写法、选择器 DSL、结构化标签；总 7,303 B；另有 30+3 工具家族 | zen 面向人；omp 面向「冷读者执行的规格书」 |
| **C plan 模式** | 283 字符附注 + 权限层拒写 | 9,440 字符主提示 + 8 模板 + `plan-mode-guard` 硬拦截 + `local://` 计划文件 + `xd://propose` 审批 | 提示约束 vs 协议化工作流 |
| **D 上下文文件** | `AGENTS.md`/`CLAUDE.md`/`.zenagent/AGENTS.md`，root→cwd，32KB 上限，`## Project instructions` | provider 优先级发现（`.omp` 100 > `.claude` 80 > `.agent(s)` 70 > 根 10）、内容不截断、`<repo-rules>`/`<dir-context>`/反 grep 声明、workspace-tree | 上限/去重/优先级策略完全不同 |
| **E Skills** | 原始 description ≤120 字符、4KB 硬上限、`read_skill` 工具、正文 ≤32KB | 模型压缩 ≤12 词 hint（≤160 字符、SQLite 缓存）、`skill://` 经 `read`、正文不截断、`/skill:` + autoload | 零额外调用 vs 一次后台压缩调用 |
| **F 压缩** | 1 条中文三节摘要（201 字符）+ system 125；0.85 阈值；单次；丢 toolResult | 5 级方法链 + 7 个摘要变体（8 节 handoff、update、turn-prefix、short、handoff、branch、context）；防注入；多窗口折叠；工具结果截断保留 + 文件清单回填 | 两个物种 |
| **G 自定义/扩展** | 扩展 `add_system_prompt()` 追加片段 | `SYSTEM.md`/`SYSTEM_TEMPLATE.md` 整块替换 + `APPEND_SYSTEM.md` 权威追加 + 扩展片段 | 追加 vs 替换+追加 |
| **H 模板** | `/commands` 用户 Markdown 模板（内置 0） | `config/prompt-templates.ts` 同类机制（未展开对比） | 基本相同 |
| **I 标题** | 无 | 侧请求标题生成 4 个模板 + fork/card 策略 | zen 缺失 |
| 装配/缓存 | 单字符串，四前端各拼；cwd 在 system 里 | 有序块数组，稳定/易变分离，缓存断点；日期/cwd 移到首个 user turn | zen 不关心前缀缓存；omp 为缓存做了专门设计 |
| 模型调用点 | **2 个**（主 loop + 压缩） | 几十个（标题、摘要、技能描述压缩、judge、memory、advisor…） | 提示面 = 调用面的投影 |

### 4.3 设计哲学差异（7 条）

1. **极简内核 vs 生产级 CLI**：zen 的 README 自述「minimal coding agent written from scratch in pure Python … one obvious way to do things」；omp 是功能完备的 CLI（30+ 工具、多代理、记忆、压缩方法链、标题、commit、goal、security…）。提示词规模差异是产品定位差异的直接投影。
2. **「提示词即行为约束」vs「提示词即工程规范」**：zen 只约束工具选择、探索方式、todo、并行、收尾；omp 还约束工程正确性、可维护性、验证义务、交付完整性、禁止范围蔓延、禁止提前收工——更像一份团队工程手册。
3. **提示词分层方式**：zen 用「函数拼接」（A→D→E→G→C，四个前端各写一遍顺序）；omp 用「模板引擎 + 条件变量 + 有序块数组」，同一份模板按工具集/设置/模型能力裁剪，并通过 block 边界做缓存。
4. **工具描述的读者模型**：zen 假设读者是「会读英文的模型」，用自然语言；omp 假设读者是「必须零歧义执行的冷读者」，用 MUST/NEVER、结构化标签、DSL 语法、内嵌 JSON 示例，并把参数语义尽量折进顶层描述（`read` 的 `path` 选择器）。
5. **上下文/技能的预算策略**：zen 用硬上限（32KB / 4KB / 32KB）保证 token 可预测；omp 用「内容不截断 + 索引压缩 + 反 grep 声明 + workspace tree 默认关」保证信息完整，把成本控制交给索引压缩和缓存。
6. **压缩的默认路径**：zen 默认「LLM 摘要」；omp 默认「先 provider 原生、再位图无 LLM、再 handoff/shake、最后才 LLM 摘要」——优先避免 LLM 调用和信息损失，且摘要本身是结构化 handoff 而不是散文。
7. **可替换性**：zen 允许扩展追加片段；omp 允许 `SYSTEM.md`/`SYSTEM_TEMPLATE.md` 整块换脑（并明确「替换后内置行为规范全部消失」），另有 `APPEND_SYSTEM.md` 权威追加——控制粒度更细，风险也更高。

### 4.4 zen 的省略：哪些是「还没做」、哪些是「刻意不做」

- **还没做（有明确缺口）**：标题生成（omp 有完整侧请求方案）；plan 模式的计划文件/审批协议（zen 只有只读附注 + 权限拒写）；压缩的结构化 handoff 格式与迭代 update；工具 roster 变更通知；上下文文件的 provider 优先级/去重；`wait` 之外的异步/作业工具面。
- **刻意不做（与 zen 的定位一致）**：多代理/子代理、记忆、commit 生成、goal/vibe 模式、judge/bench、30+ 工具家族、模板条件裁剪、缓存分段。这些在 zen 的 `docs/research/omp-cli-study/09-zenagent-vs-omp-gap.md` 里有明确「不做（N）」清单。
- **已经落地的「omp 蒸馏」**：基础提示里的 Specialized / General / Exploration 三段式（docstring 自述镜像 omp）；工具 policy 的「专用工具优先、bash 兜底」；plan 模式只读约束；skills 索引 + `read_skill`；压缩摘要。这些是两边可直接对照的部分。

---

## 5. 附注与风险

1. **仓库在审计期间移动**：`f363f41`（12:00）→ `fecc9df`（12:08，合入 P2 工具内核）。本报告按 `fecc9df` 重测；`feat/tool-kernel-p0`、`feat/update-notice` 等分支尚未合并，提示面仍可能变化。引用前确认 `git log -1`。
2. **文档滞后**（不影响本报告结论，但会影响读者）：
   - `docs/feature-map.md:34` 仍写「9 个内置工具」，实际 10 个（新增 `wait`）；
   - `docs/reference/file-map.md:34-37` 工具表缺 `wait`/`web_search`/`fetch`/`todo`；
   - `docs/internals/context-compaction.md:40` 说 headless 压缩后 `switch_session`，但 `cli/main.py:361-364` 丢弃返回值、`persist` 仍写旧 session；
   - `docs/research/omp-cli-study/10b-omp-orchestration-tools.md:33`、`10-tools-gap.md:206` 称 zen 系统提示「只有一句 Prefer using tools」——已过时（三段式已落地）。
3. **omp 提示词大小高度依赖配置**：9,895 / 22,294 是同一份代码在「隔离默认」和「本机 160 技能」两种环境下的渲染值；引用时必须带基准说明。
4. **`src/compress` 不是会话压缩**：6,198 B 的 `system.md` 是 `omp compress` 提示词改写工具，真正的会话压缩在 `pi-agent-core/src/compaction/`。这是最容易误读的一处。
5. **omp `edit` 描述不在 TS 源码里**：由原生 addon `editDescription(mode)` 生成（5 种模式，默认 hashline 1,955 字符），npm 包内无 .md 可 grep。
6. **缓存差异**：zen 把 `Working directory: {cwd}` 写进 system prompt，换目录即前缀失效；omp 明确把日期/cwd 移出 system（贴到首个 user turn）以保 system 前缀字节稳定——如果 zen 未来关心 prompt cache，这是第一个可借鉴点。
7. **zen 的压缩丢弃全部 toolResult**：对「继续工作」可能丢失关键命令输出/错误信息；omp 的做法是截断到 2,000 字符保留 + 回填 read/modified 文件清单，可参考。

---

## 6. 证据与复现

- **原始审计报告**（会话临时文件，含全部逐行证据与 verbatim）：
  - `/tmp/zen-prompt-audit/zen.md`（zen-agent，595 行，257 处 file:line，0 越界）
  - `/tmp/zen-prompt-audit/omp-core.md`（omp 核心位置，820 行）
  - `/tmp/zen-prompt-audit/omp-tools.md`（omp 工具/skills/压缩，444 行）
- **测量脚本**：`/tmp/zen-prompt-audit/measure2.ts`（omp 基准 A：隔离 HOME）、`measure3.ts`（基准 B：本机真实）、`render-tpl.ts`（单模板渲染）；zen 侧为 `.venv/bin/python` 直接导入函数。
- **关键复现命令**：

```bash
# zen 固定提示词
cd /Users/mac/zen-agent && ./.venv/bin/python -c "
from zenagent.app.support import system_prompt
from zenagent.app.modes import mode_prompt_note
from zenagent.app.compact import SUMMARIZE_PROMPT, _SUMMARIZE_SYSTEM
from zenagent.tools import ALL_TOOLS
print(len(system_prompt([t.name for t in ALL_TOOLS])))  # 609
print(len(system_prompt([t.name for t in ALL_TOOLS] + ['todo','read_skill'])))  # 791
print(len(mode_prompt_note('plan')))  # 283
print(len(SUMMARIZE_PROMPT), len(_SUMMARIZE_SYSTEM))  # 201 125
"

# omp 运行时渲染（隔离 HOME 的基准 A）
HOME=/tmp/zen-prompt-audit/fake-home PI_CODING_AGENT_DIR=/tmp/zen-prompt-audit/fake-home/.omp/agent \
  bun /tmp/zen-prompt-audit/measure2.ts   # b0=9895 b1=465
bun /tmp/zen-prompt-audit/measure3.ts      # b0=22294 b1=465，skills=12237
```

- **本报告中的行号**均相对两个根：zen 为 `/Users/mac/zen-agent`，omp 为安装包根目录 `.../@oh-my-pi/pi-coding-agent`（`src/...`）。
