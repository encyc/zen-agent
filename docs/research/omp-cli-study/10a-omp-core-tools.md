# 10a · omp 核心工具族（文件 / Shell / 搜索 / 编辑）

> **基线**：omp `18.14.0`（`@oh-my-pi/pi-coding-agent`）；ZenAgent `v0.3.0` HEAD `2aab733`。
> **证据约定**（正文用简写，均为绝对路径前缀）：
> - `OMP/` = `/Users/mac/.bun/install/global/node_modules/@oh-my-pi/pi-coding-agent/src/`
> - `CORE/` = `.../@oh-my-pi/pi-agent-core/src/`；`TUI/` = `.../@oh-my-pi/pi-tui/src/`；`UTIL/` = `.../@oh-my-pi/pi-utils/src/`
> - `ZEN/` = `/Users/mac/zen-agent/`
> - `行号` 对应上述版本；标注「推断」的是非源码直读结论。09 号文档覆盖 TUI，本轮只看**工具能力与内核语义**，不重复渲染面。

---

## 0. 结论速览

1. omp 内置 **29 个模型可见工具 + 3 个隐藏工具**（`OMP/tools/builtin-names.ts:1-30,36`）；ZenAgent 是 **9 个**（`ZEN/zenagent/tools/__init__.py:27-37`）。差距不在数量，在**文件读写的分辨率**：omp 一个 `read` 挂 12 个格式子模块 + 9 种选择器形态，ZenAgent 一个 `read_file` 只有"整个文件或前 2000 行"。
2. **没有 powershell 工具**。powershell 在 omp 里只有三处身影：(a) spawn 参数分支（`UTIL/procmgr.ts:86-89` → `-NoLogo -Command`）；(b) profile alias 的一种 shell 字符串（`OMP/cli/profile-alias.ts:6`）；(c) Windows 剪贴板/端口探测的子进程。模型面工具名表里没有它。omp 的 `bash` 命令**默认跑在嵌入式 brush-core shell**（`OMP/exec/bash-executor.ts:1-6`），Windows 外部 shell 是 Git Bash→bash.exe→sh.exe→cmd.exe（`UTIL/procmgr.ts:161-193`）。
3. 最高性价比的三件（本族）：**read 分页**（omp 默认每次 300 行，`OMP/tools/settings.ts:160-163`）、**write 拒绝回写截断读数**（`OMP/tools/write.ts:319-350`，数据丢失防护）、**bash 非交互环境**（`OMP/exec/non-interactive-env.ts:6-53,113-135`）。
4. omp 的"工具多"很大程度是**内核设施**而非工具各自实现：审批 tier + 记忆化缓存、`concurrency`、`loadMode`（按需暴露 schema）、OutputMeta 统一截断通知、read 结果 supersede 剪枝。抄这些比抄工具划算。
5. `hashline`（内容哈希锚定 + 行号编辑）是 omp 的**默认编辑模式**（`OMP/edit/settings.ts:36-41`），并按模型能力回退到 `replace`（`OMP/utils/edit-mode.ts:23-49`）。ZenAgent 的 old/new 字符串编辑就是它的 `replace` 模式；v0.4 不建议上 hashline。
6. omp **没有 `ls`/`list_dir` 工具**——目录列表由 `read` 承担（`OMP/prompts/tools/read.md:10`）。ZenAgent 的 `list_dir` 是反向差异：工具数是可以减的。
7. 反直觉：omp 的 `grep` 一页只给 **20 个文件**（`OMP/tools/grep.ts:83-94`），靠 `skip` 翻页——"给可续读的一页"优于 ZenAgent 的"200 条硬截断且无法翻页"（`ZEN/zenagent/tools/search.py:26,156-157`）。
8. 输出上限也不同量级：omp 内联 **3000 行 / 50KB / 单行 512B**，超出落 `artifact://`（16MiB）（`TUI/tools/streaming-output.ts:11-22`）；ZenAgent 是 30KB head+tail（`ZEN/zenagent/tools/bash.py:31`）。

---

## 1. 内核契约：omp 给工具的东西 vs ZenAgent

`CORE/types.ts:1139-1254` 的 `AgentTool` 比 ZenAgent 的 `AgentTool`（`ZEN/zenagent/agent/loop.py:83-105`）多出的字段，几乎全是**内核设施**：

| 能力 | omp | ZenAgent | 差距性质 |
|---|---|---|---|
| 审批声明 | `approval: tier("read"/"write"/"exec") \| fn`，可由参数动态决定（`CORE/types.ts:1243-1244`, `ToolTier:1053`） | 单一 `before_tool_call` 钩子按工具名+参数判断（`ZEN/docs/internals/guardrails.md:18-38`） | omp 的 tier 让**策略按能力分级**，不在权限门里写 if-else |
| 审批记忆 | `approvalCacheTargets` 返回本调用触碰的文件/URL，`always-ask-cached` 命中即免问（`CORE/types.ts:1249-1254`） | 无 | 长会话里"同一文件反复写"会反复问 |
| 并发调度 | `concurrency: "shared" \| "exclusive" \| fn`（`CORE/types.ts:1176`）；`write`/`edit` 声明 exclusive（`OMP/tools/write.ts:612`, `OMP/edit/index.ts:365`），`bash` 仅 pty 独占（`OMP/tools/bash.ts:645-646`） | `execution_mode: parallel \| sequential`（`ZEN/zenagent/agent/loop.py:96`） | 语义接近，但 ZenAgent 写工具**没标 sequential**，并行批里有竞态风险（推断：靠 before_tool_call 兜底） |
| 按需暴露 | `loadMode: "essential" \| "discoverable"`：discoverable 的 schema 不进每次请求（`CORE/types.ts:1055-1064`）；`ast_grep`/`ast_edit`/`checkpoint`/`lsp` 都是 discoverable（`OMP/tools/ast-grep.ts:194`, `ast-edit.ts:239`） | 无（9 个全量进 prompt） | 工具变多时这是唯一不炸 context 的路 |
| 结构化输出元信息 | `details.meta: OutputMeta` + 内核 wrapper 统一附加通知（`OMP/tools/output-meta.ts:780`；渲染 `TUI/tools/output-meta.ts:304-367`） | `details` 自由 dict，各工具自己拼字符串（`ZEN/zenagent/tools/fs.py:130-139`） | 抄这个能让"截断/上限/恢复坐标"一致 |
| 结果可裁剪 | `AgentToolResult.useless`（`CORE/types.ts:1036`）：压缩时可直接丢弃（空结果/超时） | 无 | 与 read supersede 配套 |
| 错误协议 | `ToolError` + `renderError`，工具抛异常由 loop 渲染（`OMP/tools/tool-errors.ts:1-45`） | 约定"不抛异常，返回 is_error"（`ZEN/docs/internals/tools-internals.md:20-26`） | 两者自洽；omp 的 `ToolError.render()` 允许富文本 |

> **推断**：ZenAgent 若只加工具不改内核，会在 P0/P1 阶段就撞上"结果无法分页/无法裁剪/无法按需暴露"三个天花板。建议本族改动里至少有 1 项落在内核（OutputMeta 或 loadMode 二选一）。

---

## 2. `read`：唯一参数 `path`，其余全在路径里

### 2.1 模型面契约

```ts
readSchema = { path: string }   // OMP/tools/read.ts:820-822
```
- 选择器**内联在路径**：`:50`、`:50-200`、`:50+150`、`:-60`、`:5-16,960-973`、`:19,59`、`:raw`、`:raw:2-4`、`:img`、`:conflicts`（`OMP/prompts/tools/read.md:1-14`；解析见 `OMP/tools/read-selector.ts:6-23,91-140`）。
- 默认每次 **300 行**（`OMP/tools/settings.ts:160-163`），clamp 到 3000（`OMP/tools/read.ts:1089-1091`）。
- 内联上限 **3000 行 / 50KB / 单行 512B**（`TUI/tools/streaming-output.ts:11-15`）。
- 带范围读附加上下文：**前 1 行、后 3 行**（`OMP/tools/read-format.ts:287-288`）；`:raw` 才是精确。
- 目录读：根目录完整，子目录列表 12 条截断（`read.md:10`）；图片 20MB 上限（`read.ts:818`）。
- 同参数重复读产生**逐字节相同**输出 ≥3 次 → 注入"别再读了"提示（`read.ts:841,858-887`）。

### 2.2 格式分发表（`#readFilesystemPipeline` `read.ts:1912+`）

| 子模块 | 模型能力 | 关键数值 / 语义 | 证据 |
|---|---|---|---|
| `read-json.ts` | `file.json?q=<jq>&raw=&compact=&offset=&limit=` | 默认 100 行/页，上限 1000 | `read-json.ts:48-49` |
| `read-sqlite.ts` | `db:table`、`:table:key`、`?where=&q=SELECT&limit=&offset=` | 表列表 500；裸 SELECT 有行上限 | `read-sqlite.ts:118,208` |
| `read-archive.ts` | ZIP/JAR/APK/WHL、tar 族、RAR/7z/ISO/CAB/DEB/RPM/CPIO/AR/LZH/ARJ/ASAR/压缩流；`archive:member` | 列表默认 500 | `read-archive.ts:161` |
| `read-binary.ts` | ELF/PE/Mach-O：overview/`:func` 伪代码/`:asm`/`:imports`/`:exports`/`:strings`/`:xrefs` | 依赖 IDA | `read-binary.ts:12-17` |
| `read-pdf.ts` | PDF 抽文本；`:pageN.png` 光栅化 | 30s 渲染超时 | `read-pdf.ts:11` |
| `read-summary.ts` | 大文件结构摘要替代全文 | 阈值 minTotalLines=100 / minBodyLines=4 / minCommentLines=6 | `OMP/tools/settings.ts:215-243` |
| `read-selector.ts` | 选择器解析/归一化 | 6 种 kind（none/raw/conflicts/image/lines/tail） | `read-selector.ts:6-23` |
| `read-supersede.ts` | 完整读可剪掉先前该文件的范围读 | 需"正面证据"（行数已扫描/图片） | `read-supersede.ts:12-18`；调用 `OMP/session/session-maintenance.ts:764-770` |
| `read-path-resolution.ts` | 唯一后缀匹配 / ENOENT 判定 / 远端挂载跳过 | 每次 execute 一个 memo | `read-path-resolution.ts:1-30` |

分发顺序（源码确认）：目录 `read.ts:2123` → archive `:1956` → sqlite `:1968` → json `:1986` → binary `:2007` → pdf `:2136` → video `:2224` → 图片/文档/notebook/流式文本。

> **设计要点（推断）**：omp 把 read 做成"**一个入口 + 内容嗅探**"，而不是"多工具"。对模型只有一个工具名，schema 永远只有 `path`；每加一种格式，工具数不变、prompt 只涨一行。这正是 ZenAgent 该学的**收敛式扩展**，而不是 `read_pdf`/`read_zip` 各开一个工具。

### 2.3 ZenAgent 现象与差距

- 只有 `path`，**无 offset/limit**（`ZEN/zenagent/tools/fs.py:105-139`）。超过 2000 行只回一句 `[truncated: showing 2000 of N lines; read a smaller range or use grep]`——但"smaller range"**根本不存在**（schema 里没有参数能指定范围）。这是本族最硬的缺口。
- 二进制一律拒绝（`fs.py:117-122`），无格式扩展。
- 无目录读（另有 `list_dir`），无选择器，无重复读检测，无 supersede。
- **优先级：P0**（read 分页）。每多一个"读大文件"任务就撞一次，且成本最低。

---

## 3. `write`：内容守卫比写入本身重要

```ts
writeSchema = { path: string, content?: string }   // OMP/tools/write.ts:216-219
```
`content` 可选是因为目标可能是 archive member / sqlite row（`write.md:2`；解析 `write.ts:481-488,500-560`）。

| 语义 | 说明 | 证据 |
|---|---|---|
| 独占调度 | `concurrency = "exclusive"` | `write.ts:612` |
| **拒绝回写截断读数** | 若内容以 `read` 截断提示结尾，且覆盖字节数 < 当前文件 → 抛 `ToolError` 拒绝，提示重读或改用 edit | `write.ts:319-350`，调用点 `:743,:985` |
| 保持 CRLF/BOM | 读目标文件约定，模型给 LF 时补回 `\r\n` / `\uFEFF`，并回一句 Note | `write.ts:417-467`，应用 `:1117-1121` |
| 保留可执行位 | `[Notice: Made executable via chmod +x]` | `write.ts:96,1143` |
| 剥离 hashline 前缀 | 模型可能把 `[path#TAG]`/行号前缀一起贴回来 | `OMP/tools/write-content.ts:75` |
| 生成文件守卫 | auto-generated 检测器命中则拒写 | `OMP/tools/auto-generated-guard.ts:1-15` |
| 宿主权限回退 | 沙箱内 write/delete 可走外部 seam（host 提供） | `OMP/tools/file-write-fallback.ts:1-30` |
| LSP 写穿透 | 写后格式化 + 诊断，可延迟投递 | `write.ts:628-650`（调用点 `:1151`） |

ZenAgent：覆盖写 + 自动建父目录（`fs.py:141-156`），返回摘要 + ≤200 行 unified diff（`fs.py:41`）。**没有截断回写防护、没有 CRLF/BOM 保持、没有生成文件守卫**。

- **P0**：截断回写防护（约 25 行，防的是真实数据丢失：模型 `read_file` 拿 2000 行 + 提示，再 `write_file` 回写，文件就被截断）。
- **P1**：CRLF/BOM 保持（Windows 用户/生成文件会整文件 diff）。

---

## 4. `edit`：5 种模式，默认不是 replace

| mode | 参数形状 | 证据 |
|---|---|---|
| `replace` | `{path, old_string, new_string, replace_all?}` | `OMP/edit/schemas.ts:3-9` |
| `patch` | `{path, edits:[{op?,rename?,diff?}]}` | `edit/schemas.ts:25-35` |
| `apply_patch` | `{input}`，`*** Begin Patch` 信封（wire name 也是 `apply_patch`） | `edit/schemas.ts:37-39`；`edit/index.ts:415-417` |
| `hashline` | `{input}`：`[PATH#TAG]` + 行号 + `PUT/CUT/REM/MV` | `OMP/edit/hashline-compact.md:1-30` |
| `sloppy` | `{input}` | `edit/schemas.ts:45-47` |

- 默认 `hashline`（`OMP/edit/settings.ts:36-41`）；对 kimi/mimo/minimax/deepseek/stepfun 等弱模型自动回退 `replace`（`OMP/utils/edit-mode.ts:23-49`）。
- hashline 的核心不变量：TAG 是内容哈希，**每次编辑后 tag 和行号都会变，必须重读**；只能改"看得见的行"，elision 视为不可见（`hashline-compact.md:7,20-27`）。这是"编辑前先证明你看过当前内容"的机制。
- `concurrency = "exclusive"`（`edit/index.ts:365`）；审批取 payload 中**所有路径的最严 tier**（`edit/index.ts:423-432`）。
- 编辑后 LSP 诊断（`edit/index.ts:172-193`）；引入语法错误时 `auto-repair` 定位最小回滚区并让 smol 模型修（`OMP/edit/auto-repair.ts:1-25`，阈值 MAX_REGION_LINES=150）。
- 冲突块解析：`read` 扫到 `<<<<<<<` 块即注册 id，`write({path:"conflict://N"})` 直接拼接（`OMP/tools/conflict-detect.ts:1-30`；`conflict-uri.ts:1-10`）。

ZenAgent：单一 old/new + 唯一匹配检查（`fs.py:158-200`），返回 ≤200 行 diff。缺 `replace_all`、缺编辑后诊断、缺锚定/过时检测。

- **P1**：`replace_all`（改名/批量替换高频，约 10 行）。
- **P2/N**：编辑后诊断需要 LSP 或编译器，与"零依赖"冲突；hashline 需要 native 哈希与语法块识别，v0.4 明确不做。

---

## 5. `bash` / shell —— powershell 裁决

### 5.1 裁决：powershell 不是工具，是 shell 选项

证据链（全部源码确认）：

1. 模型面工具名表 `BUILTIN_TOOL_NAMES` 共 29 项，**无 powershell**（`OMP/tools/builtin-names.ts:1-30`）。
2. `isPowerShell()` 只用于**选择 spawn 参数**：PowerShell 走 `-NoLogo -Command`，POSIX 走 `-l -c`（`UTIL/procmgr.ts:66-74,86-89`）。
3. powershell 作为 `ProfileAliasShell` 的字面量存在（`OMP/cli/profile-alias.ts:6,46`）——那是"CLI 里打开一个 shell"的 profile 维度，不是 agent 工具。
4. `bash` 工具**默认执行器是嵌入式 brush-core**（`OMP/exec/bash-executor.ts:1-6`），只有 `useUserShell`（interactive PTY / ACP 客户端 terminal）才落到外部 shell（`bash-executor.ts:443-445,557-569`）；Windows 外部 shell 解析顺序见 `UTIL/procmgr.ts:161-193`。

> **结论**：omp 的跨平台策略是"**工具语义不变（POSIX/bash），把 shell 差异收敛到 spawn 参数与 profile**"，而不是给每个 shell 开一个工具。ZenAgent 若要支持 PowerShell，正确形状是给 `bash` 加 `shell` 配置项（推断），不是新增 `powershell` 工具。

### 5.2 参数（schema 按 settings 拼装，共 4 个变体）

`base {command, timeout?, cwd?, pty?}`（`OMP/tools/bash.ts:333-338`）；`+async?`（`:340-346`）；`+name?/ready?{log,port,host,timeout}`（`:348-360`）；两者（`:362-375`）。
- `timeout`：默认 300s，范围 1–3600，**0 表示禁用**（`OMP/tools/tool-timeouts.ts:11`；`bash.ts:331`）。
- `cwd` 是一等参数——prompt 明确"**用 cwd，不要 cd**"（`OMP/prompts/tools/bash.md:3`）。

### 5.3 关键语义

| 语义 | 行为 | 证据 |
|---|---|---|
| 持久 shell | 一条调用=一条命令/管道；cwd/env 跨调用保留 | `bash.md:1` |
| 非交互环境 | 约 45 项：`PAGER=cat`、`TERM=dumb`、`CI=true`、`GIT_TERMINAL_PROMPT=0`、`npm_config_yes=true`、`PIP_NO_INPUT`、`DEBIAN_FRONTEND`…；Windows 额外补 UTF-8（`PYTHONIOENCODING`/`LANG`，已设置则不覆盖）；`PI_BASH_NO_CI` 可关掉 CI 注入 | `OMP/exec/non-interactive-env.ts:6-53,55-135` |
| 输出 sink | 内联 3000 行/50KB，单行 512B，超出全文落 `artifact://`（16MiB） | `TUI/tools/streaming-output.ts:11-22`；`bash.md:6` |
| 后台作业 | `async:true` → 作业号 `bg_N`，结果作为后续消息注入，**不要轮询** | `OMP/async/job-manager.ts:955-957`；`bash.md:5,8` |
| 服务 | `name`+`ready{log/port/host/timeout}`，长驻服务专用 | `bash.ts:348-360`；`bash.md:7` |
| pty | 交互式终端（xterm overlay）；独占调度 | `OMP/tools/bash-interactive.ts:1-30`；`bash.ts:645-646` |
| 审批 | 静态 tier=`exec`，动态规则：critical patterns + `allow/deny/prompt` + 复合命令**分段**匹配 | `bash.ts:514-600,187-235` |
| 拦截器 | `cat/head/tail`→read、`grep/rg`→grep、`find -name`→glob、`sed -i`→edit，直接返回错误+建议 | `OMP/exec/settings.ts:17-54`；`OMP/tools/bash-interceptor.ts:1-8` |
| worktree 改写 | `git worktree add` 被改写成 omp 自己的命令 | `OMP/tools/bash-worktree-rewrite.ts:1-45` |
| git guard | git builtin 拦截丢弃/移动他人工作的命令 | `OMP/exec/bash-executor.ts:64-73` |

### 5.4 shell 支撑模块（谁在做"shell 抽象"）

| 模块 | 作用 | 证据 |
|---|---|---|
| `bash-executor.ts` | 唯一的执行入口：嵌入式 brush（native `Shell`，默认）/ 外部 user shell（`useUserShell`）；含 direnv 自动加载（`bash.direnv: auto/off`）、输出 minimizer、`artifact://`、`onSessionShellBusy` 回退到一次性 shell | `OMP/exec/bash-executor.ts:1-6,36-110,443-445,557-569`；direnv 设置 `OMP/exec/settings.ts:188-192` |
| `exec/exec.ts` | 给 **hooks 与自定义工具**用的 `execCommand()`（argv 形式、timeout/abort，返回 stdout/stderr/code/killed）——不是 bash 工具的执行路径 | `OMP/exec/exec.ts:1-60` |
| `shell-tokenize.ts` | 保守 shell 分词器，供审批规则匹配、gh 缓存失效、会话匿名化、worktree 改写共用；明确不是完整 POSIX 解析器（heredoc/`$(…)` 不处理） | `OMP/tools/shell-tokenize.ts:1-60` |
| `bash-pty-selection.ts` | `pty` 是否可用：需有 UI 且 `PI_NO_PTY != 1` | `OMP/tools/bash-pty-selection.ts:1-11` |

ZenAgent 对照：固定 `os.getcwd()`、无 cwd 参数（`ZEN/zenagent/tools/bash.py:111-118`）；输出 30KB head+tail（`:31`）；默认 120s（`:107`）；无 async、无 artifact、无拦截器、无分段审批（只有危险命令正则）。

- **P0**：非交互环境（约 20 行；不做的话 `git log`/`man`/`npm` 都可能挂住到超时）。
- **P1**：`cwd` 参数；截断维持 30KB 但补"输出已截断，可加 `> /tmp/x` 后 read"提示（避免引入 artifact 机制）。

---

## 6. 搜索族：glob / grep / find / ast_grep / ast_edit

| 工具 | 模型面参数 | 页大小/上限 | 关键语义 | 证据 |
|---|---|---|---|---|
| `glob` | `{path?, hidden?, gitignore?, limit?}` —— **没有 pattern 字段**，pattern 就在 path 里，`;` 分隔多项 | 200（硬上限 200）；超时 5s | hidden/gitignore 默认 true；**mtime 倒序**（最新优先）；目录以 `/` 结尾 | `OMP/tools/glob.ts:36-41,45-47,294-295,654` |
| `grep` | `{pattern, path?, case?, gitignore?, skip?}` | 一页 **20 个文件**，多文件每文件 20 条，单文件 200 条，内部总上限 2000；30s 预算；每文件只搜前 4MB | `path` 支持 `;` 列表 + 每项 `:N-M` 行范围；跨行写 `\n`；`skip` 翻页 | `OMP/tools/grep.ts:71-77,83-104,1045` |
| `find` | `{query, grep_keywords[], path?}` | 每次返回 3 段范围；20s 超时 | 语义搜索，依赖 judge 角色；无 native judge 时 `auto` 不启用 | `OMP/tools/jfind/index.ts:33-37,41-45,54-60` |
| `ast_grep` | `{pat, path?, lang?, skip?}` | 默认 50；`skip` 翻页 | AST 结构匹配；`$NAME`/`$$$` 元变量；discoverable | `OMP/tools/ast-grep.ts:41-47,244,194` |
| `ast_edit` | `{ops:[{pat,out}], paths[]}` | maxFiles 1000（`PI_MAX_AST_FILES`）；discoverable | **提案制**：先 dry-run，`xd://resolve` 才落地 | `OMP/tools/ast-edit.ts:41-54,269`；`prompts/tools/ast-edit.md:10` |

ZenAgent 对照（`ZEN/zenagent/tools/search.py`）：
- `glob`：pattern+path，500 上限，字典序，只返回文件，不尊重 .gitignore（`:24,45-54`）。
- `grep`：pattern+path+glob，**200 条全局硬截断、无翻页**（`:26,156-157`）；有 rg 则用、无则纯 Python（`:81-140`）。
- 无 find / ast_* / lsp。

**优先级**：
- **P1**：`grep` 改"每文件上限 + skip 翻页 + 一页文件数"（约 40 行）。现在的形状下，一个热文件就能吃掉全部 200 条配额。
- **P2**：`glob` 加 `limit`/`gitignore`/mtime 排序（约 40 行）。
- **P2**：`ast_grep`/`ast_edit` 可选（外部 `ast-grep` 二进制，沿用 rg 的 fallback 模式，约 150 行）——若不接受额外二进制则 **N**。
- **N**：`find` 语义搜索（额外模型调用 + provider 依赖，与"最小内核"冲突）。

---

## 7. 横切基础设施（最容易被忽略、最值得抄）

| 模块 | 作用 | 证据 |
|---|---|---|
| **output-meta** | 结构化 truncation/limits/diagnostics，由内核 wrapper 在消息边界统一附加；恢复提示精确到 `artifact://id:raw:N` | `OMP/tools/output-meta.ts:780`；`TUI/tools/output-meta.ts:304-367` |
| **path-utils** | `resolveToCwd` 允许绝对/`~`/`..`（工具默认无 workspace 沙箱）；`confineToWorkspace` 用 realpath 防 symlink 逃逸，**只给不可信输入用**；唯一后缀解析；`path:sel` 与真实文件名冲突时字面优先 | `OMP/tools/path-utils.ts:332-345,364-437,1374,266-285` |
| **conflict 家族** | `read` 扫合并冲突块→注册稳定 id→`write conflict://N` 拼接；`:conflicts` 选择器 | `OMP/tools/conflict-detect.ts:1-30`；`conflict-uri.ts:1-10` |
| **file-mutation-queue** | 按 realpath 串行化同一文件的写；注释明确"内置 edit/write **不**走此队列"，只给扩展用 | `OMP/tools/file-mutation-queue.ts:1-45`（注记 `:34-35`） |
| **file-recorder** | 去重的相对路径收集器，搜索输出格式化用 | `OMP/tools/file-recorder.ts:1-30` |
| **checkpoint / rewind** | **模型可见工具**（在 `BUILTIN_TOOL_NAMES` 内，approval=`read`）：checkpoint(goal) → 探索 → rewind(report)，中间消息被报告替换 | `OMP/tools/builtin-names.ts:17-18`；`checkpoint.ts:29-39,51-140`；`prompts/tools/checkpoint.md:1-15` |

> checkpoint/rewind 严格说属于**上下文治理**而非文件族，但它以工具形态暴露、且直接决定"读多少/丢多少"。它与 ZenAgent 已有的 `context-compaction` 是同一问题的两个入口（自动 vs 模型主动）。

---

## 8. ZenAgent 对照总表

| 能力 | omp | ZenAgent | 差距等级 | 优先级 |
|---|---|---|---|---|
| read 分页/选择器 | `path` 内联 9 种选择器形态，默认 300 行 | 无，只有前 2000 行 | 硬缺口 | **P0** |
| read 目录/多格式 | 目录、json/sqlite/archive/pdf/binary/video/image | 只读 UTF-8 文本 | 大 | P2（sqlite/zip 用 stdlib） |
| write 截断回写防护 | 有（319-350） | 无 | **数据丢失** | **P0** |
| write CRLF/BOM/可执行位 | 全保持 | 无 | 中 | P1 |
| edit 模式 | 5 种，默认 hashline | 1 种（replace 等价） | 中 | replace_all=P1，其余 N |
| edit 后诊断 | LSP/auto-repair | 无 | 中 | P2/N |
| bash cwd | 一等参数 | 无 | 小 | P1 |
| bash 非交互 env | 约 45 项 + Windows UTF-8 兜底 | 无 | 中 | **P0** |
| bash async/服务/pty | 三者都有 | 无 | 中 | P2 |
| bash 拦截器 | cat/grep/find/sed→工具 | 无 | 小 | P2 |
| bash 审批 | tier + 规则 + 分段 + 缓存 | 危险命令正则 | 中 | P1（缓存） |
| grep 分页 | 20 文件/页 + skip | 200 条硬截断 | 中 | P1 |
| glob gitignore/limit | 有 | 无 | 小 | P2 |
| ast_grep/ast_edit | 有 | 无 | 可选 | P2/N |
| find 语义搜索 | 有（judge 模型） | 无 | 可选 | N |
| checkpoint/rewind | 模型可见 | 无（只有自动压缩） | 中 | P1（待裁决） |
| 输出元信息统一 | OutputMeta + wrapper | 各工具自己拼 | 内核级 | P1 |
| 工具按需暴露 | loadMode | 无 | 内核级 | P2 |

---

## 9. v0.4 最小提案（本族）

前提：工具加在 `ZEN/zenagent/tools/*.py`，实现 `AgentTool` 契约（`zenagent/tools/__init__.py` 的 `ALL_TOOLS` 追加）；**保持 httpx 为唯一运行时依赖**。

| # | 变更 | 形状（模型面） | LOC | 测试 | 优先级 |
|---|---|---|---|---|---|
| 1 | `read_file` 分页 | 加 `offset?`/`limit?`；截断时返回 `[showing lines 2001-4000 of N; next offset=4000]` | ~50 | 6（首页/续页/越界/超限提示/二进制/abort） | **P0** |
| 2 | `write_file` 截断回写守卫 | 内容以 read 截断提示结尾且比现文件短 → is_error 拒绝 | ~25 | 4（拒绝/放行/新文件/CRLF 变体） | **P0** |
| 3 | bash 非交互 env | 合并约 12 项关键 env（PAGER/TERM/CI/GIT_TERMINAL_PROMPT/npm_*/PIP_NO_INPUT），用户 env 优先 | ~20 | 3（env 生效/用户 env 不被覆盖/abort） | **P0** |
| 4 | bash `cwd` 参数 | `cwd?: string`，相对路径按进程 cwd 解析 | ~10 | 2 | P1 |
| 5 | `grep` 翻页 | 加 `skip?`；每文件 20 条、一页 20 文件 | ~40 | 4 | P1 |
| 6 | write/edit CRLF+BOM 保持 | 读目标约定，写回时补齐并回 Note | ~35 | 4 | P1 |
| 7 | 输出元信息 helper | `details.meta` 约定 + `output_notice()` 统一渲染截断/上限/续读提示 | ~60 | 5 | P1 |
| 8 | `edit_file` `replace_all` | 布尔参数，唯一性检查改为 `>=1` | ~10 | 2（全替换/不存在） | P1 |
| 9 | checkpoint/rewind | 两条消息 + 上下文重写（依赖 compaction 管线） | ~80 | 5 | P1（待 Lead 裁决） |
| 10 | glob `limit`/`gitignore`/mtime | 三参数 + 排序 | ~40 | 3 | P2 |
| 11 | ast_grep/ast_edit（可选） | 外部 `ast-grep` CLI，无则给出安装提示 | ~150 | 6 | P2/N |
| 12 | read 目录 + sqlite/zip（stdlib） | 只读 `sqlite3`/`zipfile`/`tarfile`，不新增依赖 | ~80 | 4 | P2 |

**P0+P1 合计 ≈ 290 行 + 30 个测试**，其中 #3/#7 落在内核层（env 注入点、结果元信息），其余为纯工具层新增。

---

## 10. 值得抄的最小集 / 明确不抄 / 开放问题

### 10.1 值得抄（按顺序）

1. **read 分页 + 续读坐标**（P0）——所有长任务的公共地基。
2. **write 截断回写守卫**（P0）——唯一一个"不做就会丢用户数据"的项。
3. **bash 非交互环境**（P0）——不做就会周期性卡死到超时。
4. **grep 页式输出 + 每文件上限**（P1）——把"给得更多"换成"给得可续"。
5. **输出元信息统一**（P1）——一次投入，所有工具受益；也是后面加工具不炸 context 的前提。
6. **bash cwd / replace_all / CRLF-BOM**（P1）——各 10–35 行的小修。
7. **checkpoint/rewind**（P1，待裁决）——模型主动压缩，和现有自动压缩互补。

### 10.2 明确不抄

- **hashline 编辑**（它需要内容哈希 + 语法块边界 + 每次重读协议，收益要在弱模型/长文件场景才显现，v0.4 成本过高）。
- **edit 的 5 模式矩阵**与 `apply_patch` 信封（模型兼容性工程，不是能力）。
- **PDF 光栅化 / IDA 反编译 / 视频 contact sheet**（Chromium、IDA、ffmpeg 全是重依赖）。
- **find 语义搜索**（第二次模型调用 + judge 角色，与"最小内核"直接冲突）。
- **file-write-fallback 宿主 seam**（为嵌入式宿主设计，ZenAgent 无此场景）。
- **native 文本摘要**（`read-summary` 依赖 pi-natives，纯 Python 实现质量/速度都不划算）。
- **omp 的 60 个工具渲染器**（09 已裁决，属 TUI）。

### 10.3 待 Lead 裁决的开放问题

1. **read 分页的形状**：`offset/limit` 参数，还是 omp 的"路径内联 `:N-M`"？我建议 `offset/limit`——ZenAgent 的 schema 是扁平 JSON，模型填两个整数比在路径里拼选择器更稳；代价是失去"read/grep/glob 统一的路径语法"（`推断`：omp 内联是为三工具共用同一套解析器）。
2. **checkpoint/rewind 是否进 v0.4**：它要求上下文可重写（`context-compaction` 已有），但与"工具只做 IO"的定位有张力。
3. **是否接受外部二进制作为可选能力**：`rg` 已是先例；若接受 `ast-grep`，ast_* 可 P2；若不接受，直接 N。
4. **bash 输出上限**：保持 30KB head+tail，还是对齐 omp 的 3000 行/50KB + artifact 落盘？后者需要引入 `artifact://` 概念，成本明显更高。
5. **`list_dir` 去留**：omp 用 `read` 读目录，工具数更少；ZenAgent 是否合并（会让 `read_file` 语义变宽，但与 omp 一致）。
6. **内核是否引入 `loadMode`**：9 个工具时无用；当提案 #11/#12 落地、工具数到 13+ 时就是刚需。
