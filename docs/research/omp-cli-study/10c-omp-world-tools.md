# 10c · omp 外部世界与集成工具（web / browser / git / LSP / 媒体 / 安全 / xdev）

> **基线**：omp `@oh-my-pi/pi-coding-agent@18.14.0`（本地安装，只读源码）；ZenAgent `HEAD 2aab733` v0.3.0。
> **证据约定**：`src/` = `/Users/mac/.bun/install/global/node_modules/@oh-my-pi/pi-coding-agent/src`；`zenagent/` = 本仓库；`pi-utils/` = 同级 `@oh-my-pi/pi-utils/src`。所有 `file:line` 均可直接复核；**推断**用 `[推断]` 标注。
> **口径**：只讨论「模型可见的工具 / 能力」及其外部依赖、可用条件、审批模型；`read` 的文件语义归 10a，`task`/`todo`/`memory`/`ast_*` 等内核工具不在此篇。
> **ZenAgent 现状勘误**：不是 5 个工具，实际 9 个：`read_file / write_file / edit_file / list_dir / bash / glob / grep / web_search / fetch`（`zenagent/tools/__init__.py:27-37`）。

## 0. 结论速览

1. **没有独立的 `powershell` 工具。** powershell/pwsh/cmd 只是 `bash` 工具执行层解析出来的「shell 配置」，工具名、参数、审批完全不变（§1）。Windows 上模型调的仍是 `bash`。
2. omp 的「外部世界」不是 30 个平级工具，而是**三层**：top-level tool（30 个 built-in + 3 个 hidden，`src/tools/builtin-names.ts:1-37`）、**eval prelude**（`browser`/`computer` 根本不是工具，是 `eval` 里的全局对象，`src/eval/preludes.ts:29-32`）、bridge-only（cursor/acp，不进模型工具表）。
3. **omp 没有独立 `fetch` 工具**：抓网页是 `read` 的 URL 分支（`read https://…`），实现全在 `src/tools/fetch.ts`（`src/tools/read.ts:1779-1797`；`prompts/tools/` 下也没有 fetch.md）。
4. `web_search` 有 **27 个后端**（21 个 HTML/API 引擎 + 6 个 grounded 模型搜索，`src/web/search/provider.ts:18-51`）、**75 个站点专用抓取器**（`src/web/scrapers/index.ts:83`）、**6 级正文回退**（`src/tools/fetch.ts:551-560`）；但后端选择绑在「模型角色链 + auth storage」上，不是 env-key 链（`src/web/search/index.ts:196-215`）。
5. **ZenAgent 的 web 不落后**：查询算子、宽松放宽、去重、缓存、无 key 兜底是同一套设计（`docs/internals/web-search.md:74` 明确写了「这条是 omp 的原设计」），并且 ZenAgent 在「零配置可跑 + 单依赖」上领先；omp 领先在**正文抽取广度**（trafilatura/lynx/jina/Parallel/Firecrawl + PDF/Office/站点特化）与 grounded 搜索的 **answer/citations**。
6. **唯一 P0 级差距是 LSP**：rename / references / diagnostics / code_actions 是文本工具替代不了的（`prompts/tools/lsp.md` "critical" 段），55 个 server 定义装在 `src/lsp/defaults.json`，按 PATH 探测（`src/lsp/config.ts:306-307`）——对「纯 Python + httpx」是可控成本（起子进程说 JSON-RPC）。
7. `github`（单工具 11 个 op，依赖 `gh` 二进制，`src/tools/gh.ts:69-78,138-157`）对单机最小 agent 是 **P1/条件项**；`browser`/`computer`/`debug`/`ida`/`security_scan`/`tts`/`generate_image` 是 omp 的垂直场景成本（P2/N），最小内核不该背。
8. `xd://`（xdev）是**上下文预算机制**而不是外部工具：它把 discoverable 工具从 schema 里卸下来，变成 `read xd://` / `write xd://<tool>`（`src/tools/xdev.ts:1-31,85-88`）。思路可借，实现不必抄（ZenAgent 还没到 schema 预算成为瓶颈的规模）。

## 1. Bash / powershell：用户说的「powershell」到底是什么（必答）

**判定：powershell 是 `bash` 工具的一个 shell 后端，不是第二个工具。** 证据链：

| 证据 | 位置 | 说明 |
|---|---|---|
| 工具名写死 `"bash"` | `src/tools/bash.ts:513-514` | `class BashTool` / `readonly name = "bash"`，没有 getter、不随平台改名 |
| 工具表里只有 `bash` | `src/tools/builtin-names.ts:1-33`；`src/tools/index.ts:573` | 30 个 built-in 中无 `powershell`/`shell`；`grep -rn '"powershell"' src/` 命中的全是 clipboard/open/ssh/profile-alias/session-anonymizer 这类支撑代码，没有工具注册 |
| shell 来自设置而非参数 | `src/exec/settings.ts:100`（`cfgShellPath`，string）；`src/config/settings.ts:1600-1601`（`getShellConfig()` → `procmgr.getShellConfig(cfgShellPath…)`） | 模型参数里没有 `shell` 字段（`BashToolInput` 只有 command/timeout/cwd/name/ready/async/pty，`src/tools/bash.ts:383-391`） |
| shell 抽象层支持 powershell/cmd | `pi-utils/src/procmgr.ts:11-15`（`ShellConfig{shell,args,env,prefix}`）、`:66-73`（`getShellArgs`：cmd→`/c`，PowerShell→`-NoLogo -Command`，POSIX→`-l -c`）、`:86-90`（`isPowerShell`） | 同一工具在不同平台/配置下换 shell 二进制与参数 |
| 执行器本身是内嵌 shell | `src/exec/bash-executor.ts:1-7` | brush-core 原生绑定（`pi-natives`），同时支持 `useUserShell` 走用户 shell、`pty` 走 headless PTY（`:43-50`） |
| `powershell` 别名只用于装 `omp` profile | `src/cli/profile-alias.ts:6,140-195,267-296` | `ProfileAliasShell = "bash"|"zsh"|"fish"|"powershell"|"pwsh"`，用途是把 `omp --profile=x` 写成 shell 函数（`:290-296`），与工具无关 |
| SSH 会话另算 | `src/ssh/connection-manager.ts:17,338` | SSH 主机 shell 探测（cmd/powershell/bash…），是远程会话层，不是模型工具 |

**对 ZenAgent 的意义**：如果 v0.4 要支持 Windows，正确做法**不是**新增 `powershell` 工具，而是把 `bash.py` 的执行后端抽象成 \((shell, args)\) 对：POSIX `["-lc", cmd]`、PowerShell `["-NoLogo","-Command",cmd]`、cmd `["/c",cmd]`，再加引号/路径/PTY 差异。当前 `zenagent/tools/bash.py:111` 用 `asyncio.create_subprocess_shell`（POSIX 上是 `/bin/sh`），Windows 上会直接坏掉——这是平台缺口，不是工具缺口。

## 2. 三层结构：读后面所有小节的钥匙

| 层 | 有无 JSON schema / 是否进模型工具表 | 模型怎么调到 | 例子 | 证据 |
|---|---|---|---|---|
| top-level tool | 有 / 是 | 原生 tool call | `bash` `read` `web_search` `github` `lsp` `debug` `ida` `security_scan` | `src/tools/builtin-names.ts:1-33`；`src/tools/index.ts:570-600` |
| eval prelude | **无** schema、无 renderer、不进工具表 | `eval` 里的全局对象（JS/Python） | `browser`、`computer`；另有 `judge`/`agent`/`workpool`/`output` 等 | `src/eval/preludes.ts:29-32,54-56`；`src/tools/browser/prelude-definition.ts:23-40`；`src/tools/computer.ts:127-166` |
| custom tool | 有 / 是（SDK 注入） | 原生 tool call | `generate_image`（`src/tools/image-gen.ts:236-242`）、`tts`（`src/tools/tts.ts:176-180`） | `src/sdk.ts:3716-3718` |
| bridge-only | 无 / 否 | 不面向模型 | cursor exec bridge 的 `pi_grep`/`pi_edit`；ACP 的 `fs.writeTextFile` 路由 | `src/cursor-bridge-tools.ts:1-7`；`src/tools/acp-bridge.ts:1-8` |
| xd:// device | 有 schema，但从工具表卸载 | `read xd://` / `write xd://<tool>` | 5+ 个 discoverable 工具按设置挂载 | `src/tools/xdev.ts:1-31`；`src/internal-urls/xd-protocol.ts:55` |

**关键含义**：统计「omp 有多少工具」时，`browser`/`computer` 不能算工具；`fetch` 不是工具；`generate_image`/`tts` 只在设置打开时是工具。**真正常驻模型上下文的外部工具只有 web_search / (read URL) / github / lsp / debug / ida / security_scan 这几个 + 内核工具。**

## 3. `web_search`

### 3.1 模型看到什么
- 名字 `web_search`，审批 `read` 级（默认策略下自动放行），`loadMode = "discoverable"`（默认被 xdev 挂到 `xd://`，但被 `XDEV_KEEP_TOP_LEVEL` 钉在顶层：`src/tools/xdev.ts:58-67` 明确写「多数模型不懂 xd://，藏起来等于不可达」）。
- 参数只有 6 个：`query`、`recency`(day/week/month/year)、`limit`、`max_tokens`、`temperature`、`num_search_results`（`src/web/search/index.ts:45-52`）；`model` 只在内部/CLI 用（`:56-58`）——**没有** provider 参数、没有域名过滤参数，全部走 query 语法。
- 描述极短，只有一行语法提示：`site:`/`-site:`/`after:`/`before:`/`inurl:`/`intitle:`/`filetype:`/引号/`-term`/`OR`，可选 X 搜索（`prompts/tools/web-search.md:1`；条件渲染 `src/web/search/index.ts:424-430`）。
- 输出：可选 `answer` + `## Sources`（`[n] 标题 (age) url` + 240 字 snippet）+ `## Citations`（`src/web/search/index.ts:60-90`）。

### 3.2 后端与选择（与 ZenAgent 的结构差异）
- 21 个引擎 loader：perplexity/zai/exa/tinyfish/jina/kagi/tavily/firecrawl/brave/deepseek/kimi/parallel/synthetic/ollama/searxng/duckduckgo/google/ecosia/startpage/mojeek/public（`src/web/search/provider.ts:18-40`）。
- 6 个 grounded loader：gemini/anthropic/codex/xai/openrouter/openai（`src/web/search/provider.ts:42-51`）——**用聊天模型自带的 web grounding**，产出 answer+citations。
- `public` 是**无凭据聚合器**：并发 startpage/google/duckduckgo/ecosia/mojeek，共识/排名合并，soft 5s / hard 30s deadline（`src/web/search/providers/public.ts:13-45`）。`duckduckgo.isAvailable()` 恒 true（`src/web/search/providers/duckduckgo.ts:371-377`）。
- 选择不是「env 里的 provider 列表」，而是 **model role chain**：`resolveRoleChain("web", …)` 从 model registry 取候选（`src/web/search/index.ts:196-215`），`web/hosted` 占位会展开成会话模型/它的 `webSearchModel`（`:158-176`），X-only query 会优先 xAI（`:181-192`）；默认 web 角色链来自 `roleDefaultModels().web`（`src/config/settings.ts:3368-3377`；角色定义 `src/config/model-roles.ts:48,75`）。失败时按序 fallthrough 并汇总（`src/web/search/index.ts:236-356`）。
- 约束处理：先 `parseSearchQuery`（`src/web/search/query.ts:5`），provider 尽量映射到原生参数，最后统一 `applyQueryConstraints` 做**宽松过滤**——某维度会清空结果就丢弃该维度并在输出里写 `Note: … the constraint was relaxed`（`src/web/search/index.ts:296-316`；`src/web/search/providers/base.ts:23-44` 的契约注释）。这正是 ZenAgent `apply_constraints` 的出处。
- 75 个站点专用 handler 只在 **fetch** 路径生效（`src/tools/fetch.ts:1035-1062`），不参与搜索（`src/web/scrapers/index.ts:83-158`：github/gist/gitlab/youtube/arxiv/pubmed/reddit/hn/npm/pypi/crates/…）。

### 3.3 可用条件 / 审批 / 依赖
- 设置 `web_search.enabled` 默认 true（`src/tools/settings.ts:833-843`）。
- 依赖：`AuthStorage`（本地 SQLite 凭据库，`runSearchQuery` 会 `discoverAuthStorage()`，`src/web/search/index.ts:365-390`）+ 模型目录。单个 provider 也接受 env key（`getEnvApiKey`，如 `src/web/search/providers/brave.ts:208`、`deepseek.ts:168`），**但「哪些 provider 可用、按什么顺序」由 model role chain 决定，不是 env 列表**；无凭据时能用的只有 duckduckgo/public 这类 keyless 引擎，它们同样得作为 model 条目挂在 web 角色里 `[推断]`。
- 无独立超时参数；`providers.webSearchTimeoutSeconds` 有上限（`src/web/search/index.ts:218-232`）。

## 4. `fetch` = `read` 的 URL 分支

**omp 不暴露 fetch 工具**：`read` 的 path 若是 `https?://` 或 `www.` 就走 URL 分支（`pi-tui/src/tools/read.ts:246-247`；`src/tools/read.ts:1779-1797`），受 `fetch.enabled`（默认 true，`src/tools/settings.ts:758-768`）门控，并支持 `:raw` 原样 HTML、行选择器、PDF 页/图片成员（`prompts/tools/read.md` 末尾）。

抓取管线（`src/tools/fetch.ts`）：
1. **站点头等公民**：先跑 special handlers，命中就返回结构化结果（`:1035-1062`），共 75 个（`web/scrapers/index.ts:83`）。
2. **HTML 正文**：`native`（`pi-natives` 的 `htmlToMarkdown`，进程内）→ `trafilatura`（`ensureTool` 可自下载的二进制）→ `lynx -dump` → `parallel` → `firecrawl` → `jina`（`r.jina.ai`），每级都要过长度/JS-gate 质量门（`src/tools/fetch.ts:551-721`）；`providers.fetch` 设置可改优先级（`:654-662`）。
3. **JS 门检测**：`<1024` 字且含 "enable javascript"/"javascript required" 等判定低质，降级到下一 reader（`:703-721`）。
4. **文档格式**：PDF/docx/pptx/xlsx/epub 走 markit（`:52-58`）；ipynb 可编辑（`:60-61`）；sqlite 可列表/查询（`:63-69`、`read-sqlite.ts`）；zip/tar/rar/7z/iso/… 归档（`:71-79`、`read-archive.ts`）；图片内联（20MB→300KB，`:81-90`）；视频需 ffmpeg/ffprobe（`prompts/tools/read.md`）。
5. **其它**：`<link rel=alternate>` 的 markdown/feed 替代表示（`:348-393`）、页面文档链接抽取（`:395`）、Jina reader 备用（`:520-536`）、llms.txt 兜底（`:104-120,1467-1545`；注释见 `:660-664`）。
6. 截断：行数默认 300（`:48`）+ `DEFAULT_MAX_BYTES` 头截断；超出走 `artifact://`。

**对照 ZenAgent**：`zenagent/tools/web/tools.py:91-108` 是 5 行逻辑——`httpx.get` → 只允许 http(s) → `strip_html`（去 script/style/noscript、剥标签、`html.unescape`）→ 12k 字符截断。**没有** reader 回退、没有 PDF/Office、没有站点特化、没有 JS 门检测与降级。

## 5. ZenAgent vs omp：web 到底谁更好（必答 Q3）

| 能力 | omp | ZenAgent | 判定 |
|---|---|---|---|
| 搜索 provider 数量 | 21 引擎 + 6 grounded | 5（deepseek/tavily/brave/bing/duckduckgo，`zenagent/tools/web/providers.py:324`） | omp 宽；ZenAgent 够用（覆盖有 key/无 key） |
| provider 选择机制 | 模型角色链 + auth storage（`src/web/search/index.ts:196-215`） | 环境变量 + `ZENAGENT_SEARCH_PROVIDERS` 覆盖（`zenagent/tools/web/registry.py:49,162-169`） | **ZenAgent 更简单、更可移植；omp 与账号系统强耦合** |
| provider 失败回退 | 有（逐候选 catch 继续，汇总失败） | 有（链 + `attempts` 汇总） | 平 |
| 查询算子 | `site:/after:/before:/inurl:/intitle:/filetype:/OR/引号/-term`（`src/web/search/query.ts:5,54-111`）+ 宽松放宽 | 同一套 + `lang:` + `-site:`/`-inurl:`/`-intitle:` 负向算子（`docs/internals/web-search.md:52-76`；`zenagent/tools/web/query.py:299-341`） | **ZenAgent 略多（lang、负向算子显式建模）** |
| 结果去重/缓存 | 未见统一去重/短缓存（各 provider 自己处理）`[推断]` | URL 规范化去重 + 60s/32 条缓存（`docs/internals/web-search.md:48-49`） | **ZenAgent 领先** |
| 无 key 可用 | duckduckgo/public（keyless，但挂在 model role 上） | bing + duckduckgo 直接可用，默认链兜底（`docs/internals/web-search.md:26-29`） | **ZenAgent 领先（零配置）** |
| answer/citations | grounded 模型搜索可给 answer+citations（`src/web/search/index.ts:60-90`） | 只有 snippet 列表（`zenagent/tools/web/tools.py:44-56`） | omp 领先（但代价是绑 LLM 账号） |
| 正文抽取 | 6 级 reader + 75 站点 handler + PDF/Office/归档/sqlite | 单 `strip_html` + 12k 截断 | **omp 明显领先** |
| URL 注入面 | `read` 内部统一走审批 `read` 级、`fetch.enabled` 可关 | `fetch` 独立工具、只允许 http(s) | 平（omp 更集中，ZenAgent 更显式） |
| 可观测性 | 失败汇总 + details.response | `/search` 命令 + `details.provider/attempts/cached`（`docs/internals/web-search.md:104-108`） | **ZenAgent 领先（有专门的诊断命令）** |

**结论**：**搜索侧 ZenAgent 不落后，甚至更适合最小内核**（零配置、单依赖、显式算子、去重缓存、诊断命令）。omp 的优势集中在「**读**」：正文抽取质量、文档格式、站点特化、grounded answer。不要把 omp 的 27 后端搬过来；那 27 个后端是它为 40+ provider 账号体系付的成本。

## 6. browser（eval prelude，不是工具）

| 项 | 内容 | 证据 |
|---|---|---|
| 模型可见形态 | `eval` 里的全局 `browser` 对象（JS/Python 各一份 prelude），没有 `browser` 工具；文档在 `prompts/tools/browser.md`（内联进 eval 描述） | `src/tools/browser/prelude-definition.ts:23-40`；`src/eval/preludes.ts:26-31` |
| 能力面 | `open/close/tabs/targets` + `tab.*` 100+ helper：navigation、`observe/ariaSnapshot/a11y`、`screenshot/pdf/diffScreenshot`、`extract/text/html`、click/type/drag/select/upload、`waitFor*`、frames、dialogs、emulate/devices、clipboard/storage/cookies/state、init scripts/downloads、console/errors/trace/profile/metrics、**recordStart(.mp4/.webm)**、vitals、**React 树/渲染分析**、`route/requests/HAR`、`webmcp*`、`evaluate`/`tab.run` | `prompts/tools/browser.md`（`<instruction>` 全段） |
| 驱动器 | Puppeteer 家族（`puppeteer-core@25.3.0`，`package.json:567`）+ 自管 Chromium（`ensureChromiumExecutable`，`src/tools/browser/launch.ts:319-344`）；另有 relay（用户自己的 Chrome，扩展+本地 9224）、CDP attach、Tern PiP（WKWebView）、cmux WKWebView | `src/tools/browser.ts:169-245`（`resolveBrowserKind` 优先级）；`src/tools/browser/settings.ts:7-140` |
| 反检测 | 14 份 stealth 脚本（`src/tools/puppeteer/00_stealth_*.txt` … `13_stealth_worker.txt`） | 目录即证据 |
| 审批/安全 | prelude 级 `approval: "exec"`（每次调用走 `tools.approval.browser` 策略）；`allowed_domains` 白名单；`allow_file_access` 是启动参数；`tab.run` 明确「不是沙箱」；relay「是用户真实登录态，站点归因到用户，不要在未授权时导航 borrowed tab」 | `src/tools/browser/prelude-definition.ts:36`；`src/eval/preludes.ts:104-129`；`prompts/tools/browser.md` 的 `<critical>` |
| 可用条件 | 默认开（`browser.enabled` true）；无 Chromium 时首次使用下载；relay/tern/cmux 各自需额外环境 | `src/tools/browser/settings.ts:7-18`；`launch.ts:319` |
| ZenAgent | ❌ 无（httpx 单依赖无法承载） | — |
| 优先级 | **N**（若要 JS 页面，P2 也只做「可选外部后端」，不引 puppeteer/Chromium） | — |

## 7. computer（eval prelude，默认关闭）

| 项 | 内容 | 证据 |
|---|---|---|
| 形态 | `eval` 里的全局 `computer`；提示词第一句就是「It is not a standalone tool.」 | `prompts/tools/computer.md:1`；`src/tools/computer.ts:127-166` |
| 能力 | 原生桌面控制：`displays/windows/screenshot/zoom/click/drag/scroll/type/press`、macOS AX 树（`win.ax()` 文本树 + `[ref=eN]`）、元素 `value/setValue/selectText/press/type`、菜单 `menu.items/select`、`apps.list/open`、多显示器坐标帧、`holdKeys/holdMouse`、`control.acquire`（需真人确认）、`globalEscape`、`computer.run` | `prompts/tools/computer.md` 全段 |
| 平台 | macOS 为主（Screen Recording / Accessibility 权限、AX、Space 管理），Linux/Windows 部分支持；Wayland 用 host interrupt 代替全局 Escape；底层是 `pi-natives` 的原生 desktop 绑定（`pi-natives/native/desktop.js`，`package.json:100-103`） | `prompts/tools/computer.md` 的 rules；`pi-natives/package.json:71-103` |
| 审批 | 按 action 分级：`capabilities`/只读 `run`/只读 chain → `read`；输入与变更 → `exec`（`src/tools/computer.ts:112-126`）；另有 `control.acquire` 的真人确认 | 同左 |
| 可用条件 | **默认关**（`computer.enabled` false，`src/tools/settings.ts:624-634`）；`/computer` 会话开关 | 同左 |
| ZenAgent | ❌ | — |
| 优先级 | **N**：原生模块 + 权限模型，和「httpx only」定位正面冲突 | — |

## 8. github / gitlab

### 8.1 `github`（单工具，11 个 op）
- 名字 `github`，`loadMode = "discoverable"`，`createIf` 要求 `gh` 二进制存在（`src/tools/gh.ts:154-157`；`src/utils/github.ts:90-92`）。全部通过 `gh` CLI 子进程执行，超时 5 分钟（`src/utils/github.ts:14-22`）。
- op 列表（`src/tools/gh.ts:80-107,168-197`）：`repo_view` / `file_read` / `pr_create` / `pr_checkout` / `pr_push` / `search_issues` / `search_prs` / `search_code` / `search_commits` / `search_repos` / `run_watch`。
- 审批：8 个只读 op → `read`，create/checkout/push → `exec`（`src/tools/gh.ts:69-78,140-143`）。
- 亮点实现：`pr_checkout` 建**专用 git worktree**（不碰工作树），支持一次多个 PR、可复用、`pr_push` 带 force-with-lease（`src/tools/gh-pr-checkout.ts:322,434,554,606`）；`run_watch` 轮询 Actions，首个失败 job 快速失败并把完整日志存 artifact（`src/tools/gh-run-watch.ts:309-337,694`）；PR/issue 有缓存与 `pr://<N>`、`issue://<N>` 内部 URL（`src/tools/github-cache.ts` 共 958 行；`src/tools/gh-pr-diff.ts`）。
- 提示词约束很硬：GitHub 仓库文件必须用 `file_read`，禁止 `curl/wget`（`prompts/tools/github.md` 末段）。

### 8.2 gitlab（只读、无工具）
- `glab` 二进制探测（`src/utils/gitlab.ts:33-37`），但**没有 gitlab 工具**；GitLab MR 只通过内部 URL `mr://<N>`（`src/internal-urls/mr-protocol.ts:81-82`，实现在 `src/tools/glab-mr.ts:102-160`）+ 内置 `/review` 命令（`src/extensibility/custom-commands/bundled/review/index.ts:11`）读取。
- ZenAgent：❌ 两者都没有。
- 优先级：**P1（条件项）**——PR 查看/评论是编码 agent 的高频真实需求，但只建议做 `pr view/diff/checks/logs` 只读子集 + 在 `gh` 存在时才注册（子进程，不违反 httpx-only）；`pr_create/checkout/push` 属于 N。

## 9. `lsp`（P0 差距）

| 项 | 内容 | 证据 |
|---|---|---|
| 模型可见 | `lsp` 工具，`loadMode = "discoverable"`，`createIf` 只在 `session.enableLsp === false` 时不建 | `src/lsp/tool.ts:178-212` |
| action | `diagnostics`（单文件/glob/workspace）、`symbols`（文件/workspace）、`definition`、`type_definition`、`implementation`、`references`、`hover`、`status`、`capabilities`、`rename`、`rename_file`、`code_actions`、`reload`、`request`（裸 JSON-RPC） | `prompts/tools/lsp.md` `<operations>`；只读集合 `src/lsp/servers.ts:26-36` |
| 审批 | 8 个只读 action → `read`；rename/rename_file/code_actions(apply) → `write`；read-only 会话额外拒绝非只读 action | `src/lsp/tool.ts:179-183,222-224` |
| 依赖 | 55 个 server 定义（rust-analyzer、clangd、gopls、typescript-language-server、pyright、zls、…，`src/lsp/defaults.json`），**按 PATH 探测**（`src/lsp/config.ts:306-307`），不自动安装 `[推断：未见 install 路径]`；可选 lspmux 共享 server（`src/lsp/lspmux.ts:5-17`） | 同左 |
| 可用条件 | `lsp.enabled` 默认 true、`lsp.lazy` 默认 true（首次用到才启动）、`lsp.shared` 默认 true（`src/lsp/settings.ts:6-40`） | 同左 |
| 额外价值 | 写后诊断回灌（deferred diagnostics / late diagnostic 提示）、`rename_file` 连 import 一起改、code action 直接 apply WorkspaceEdit | `src/lsp/writethrough.ts`、`diagnostics-ledger.ts`、`prompts/tools/lsp.md` critical 段 |
| ZenAgent | ❌（只有 grep/glob 文本搜索） | — |
| 优先级 | **P0**：唯一能用「纯子进程 + JSON-RPC」实现、且明确放大模型能力的家族。最小实现：`diagnostics` + `symbols` + `references` + `definition` + `rename` 五个 action | — |

## 10. `debug`（DAP）/ `ida`

| 项 | debug | ida |
|---|---|---|
| 名字/形态 | `debug` 工具（`src/tools/debug.ts:544-586`），默认开（`debug.enabled` true，`src/tools/settings.ts:575-585`），`concurrency = "exclusive"`（单会话） | `ida` 工具（`src/tools/ida.ts:79-109`），`ida.enabled` true 但 `createIf` 额外要求找到 idalib（`cfgIdaAvailable`，`src/tools/settings.ts:1144`；`src/ida/install.ts:15-96` 按 `libidalib.dylib/.dll/.so` 探测） |
| 外部依赖 | 14 个 DAP adapter 配置（gdb/lldb-dap/codelldb/debugpy/dlv/js-debug-adapter/netcoredbg/…，`src/dap/defaults.json`），需要对应二进制在 PATH；无 adapter 时报错并列已装 | **IDA Pro 商业授权** + idalib；DB 是 daemon（`omp.ida.<id>`），图片/二进制永不修改，另有 `ida.maxOpen`/`idleCloseSec` |
| 能力 | launch/attach、breakpoint（含条件/hit/数据断点）、step、threads/stack/scopes/variables、evaluate(repl/watch/hover)、disassemble/read_memory、modules/loaded_sources、自定义 DAP request | lifecycle、`read` 视图（pseudocode/asm/imports/exports/strings/xrefs）、结构化编辑（rename/comment/set_type/make_function）、`exec` Python（预置 `db`/`ida_*`/helpers） |
| 审批 | 只读 action → `read`，其余 `exec`（`src/tools/debug.ts:57-68,545-549`） | list → `read`，exec → `exec`，其余 `write`（`src/tools/ida.ts:81-86`） |
| ZenAgent | ❌ | ❌ |
| 优先级 | **N**（debug 需要用户装 adapter，收益集中在 C/Rust/Go 系统调试；最小内核不做） | **N**（商业授权、逆向场景） |

## 11. `security_scan` / `generate_image` / `tts`

| 项 | security_scan | generate_image | tts |
|---|---|---|---|
| 名字 | `security_scan`（`src/tools/security-scan.ts:108-114`） | `generate_image`（custom tool，`src/tools/image-gen.ts:236-242`） | `tts`（custom tool，`src/tools/tts.ts:176-181`） |
| 参数要点 | 9 个 action：`preflight/start/status/cancel/validate/cloud_scans/cloud_start/cloud_status/cloud_pull`；target_kind、include/exclude、base/head revision、`credential_id`、cloud 配置 ID | `subject` + action/scene/composition/lighting/style/text/changes/aspect_ratio/image_size/input[]/model | `text`(≤15000)、voice_id、language、output_path、sample_rate、bit_rate |
| 外部依赖 | **OMP 自建安全扫描后端 + Codex Security 云（ChatGPT OAuth 凭据 + 独立配额）**；`security://` 资源命名空间 | 配置的 image 模型（`roleCandidatePool("image")`，走 provider API） | 本地 Kokoro ONNX（内置，无网）或 xAI Grok Voice / OpenAI 兼容语音端点 |
| 可用条件 | `security.enabled` **默认 false**（`src/tools/settings.ts:845-856`） | `generate_image.enabled` **默认 false**（`:611-622`） | `speechgen.enabled` **默认 false**（`:599-609`） |
| 审批 | `exec`（`security-scan.ts:109`） | `write`（`image-gen.ts:240`） | `write`（`tts.ts:180`） |
| ZenAgent | ❌ | ❌ | ❌ |
| 优先级 | **N**（绑定 OMP 自建服务，不是通用能力） | **N**（与编码内核无关） | **N**（TTS 是 TUI 语音场景，非编码） |

## 12. `xdev`：`xd://` 虚拟工具设备（最值得理解的机制）

- **目的**：把「很少用」的工具从每次请求的 tools schema 里卸载，模型改用已经有的 `read`/`write` 驱动，省 prompt token（`src/tools/xdev.ts:1-31` 的文件头）；`tools.xdev` 默认 **true**（`src/tools/settings.ts:1012-1023`）。
- **协议**（`src/internal-urls/xd-protocol.ts:55`；`src/tools/xdev.ts:1-8`）：
  - `read xd://` → 列出已挂载设备；
  - `read xd://<tool>` → 该工具的完整文档 + **TS 形式的 JSON 参数 schema**；
  - `write xd://<tool>` → 执行，`content` 就是 JSON 参数对象（走 `validateAgentToolArguments`，schema 不匹配会把 schema 回给模型自纠）。
- **挂载规则**：只有 `loadMode = "discoverable"` 的工具可挂（`src/tools/xdev.ts:85-88`）；`XDEV_TRANSPORT_TOOLS = {read, write}` 永不可挂（否则设备全不可达，`:71-73`）；`XDEV_KEEP_TOP_LEVEL = {todo, yield, ask, grep, web_search}` 永远置顶（`:56-69`，注释解释了每个的原因，如 grep 是 bash interceptor 的重定向目标、web_search 多数模型不会走 xd://）。
- **文档预算**：inline 到 system prompt 的 docs 有总预算 48k 字符、单设备 10k（`:249-253`），三种模式 `inline|builtins|catalog`（`cfgToolsXdevDocs` 默认 catalog，`src/tools/settings.ts:1025-1059`）。
- **审批**：`write xd://<device>` 的档位来自被包裹工具对该 payload 自己的审批（`deviceWriteTier`，`src/internal-urls/xd-protocol.ts:34-53`），并带 `policyKey` 让用户能按设备单独 allow/deny（`tools.approval.<device>`）。
- **对 ZenAgent 的判断**：这是**上下文预算机制**，ZenAgent 只有 9 个工具、schema 总量很小，**现在抄没有收益**；但它预示了「MCP/工具变多以后」的必然问题——可以作为 v0.5+ 的观察项，不进 v0.4。

## 13. bridge-only 与解析设备（不进模型工具表）

| 文件 | 是什么 | 模型可见？ | 证据 |
|---|---|---|---|
| `src/cursor-bridge-tools.ts` | Cursor exec bridge 每次调用构造的 grep/edit 实例（`pi_grep` 需要 per-call context 宽度、`pi_edit` 需要 replace 模式），并强制套审批 wrapper | 否 | `cursor-bridge-tools.ts:1-7,39-56,65-75` |
| `src/tools/acp-bridge.ts` | ACP 客户端（如 Zed）声明 `fs.writeTextFile` 后，所有写工具路由到编辑器 buffer；内部 artifact 直写磁盘；写完回读校验 drift | 否 | `acp-bridge.ts:1-8,31-47` |
| `src/tools/resolve.ts` | 三个纯文本「解析设备」：`write xd://resolve <理由>` 应用暂存改动、`xd://reject` 丢弃、`xd://propose <slug>` 提交 plan；不占 schema、不进 system prompt | 通过 write 的文本内容 | `resolve.ts:1-19,56-74` |
| `src/tools/report-tool-issue.ts` | 自动 QA：`write xd://report_issue` 写一条 `<tool>: <desc>`，落 SQLite，可选推送到 `qa.omp.sh`；有同意流程 | 同上（无 schema） | `report-tool-issue.ts:1-26` |

**注**：`resolve`/`report_issue` 这种「用 write 纯文本触发内部动作」的设计，和 ZenAgent 的审批弹窗/plan 模式有重叠但没有对应物；不建议抄（ZenAgent 已有 plan 模式 + 审批）。

## 14. `read` 的格式扩展（只列名，语义归 10a）

`prompts/tools/read.md` 声明的 path 后缀/来源：行选择器（`:50`/`:50-200`/`:50+150`/`:-60`/逗号多段/`:raw`/`:conflicts`）、SVG（`:img` 栅格化）、光栅图、视频（ffmpeg/ffprobe，`:412`/`:1h5m42s`）、代码 outline、目录、SQLite（表/主键/`?limit=?where=?q=`）、归档（ZIP/JAR/APK/WHL/TAR/RAR/7z/ISO/CAB/DEB/RPM/CPIO/AR/LZH/ARJ/ASAR + 成员路径）、JSON/JSONL（`?q=<jq>`）、PDF/文档（markit：pdf/docx/pptx/xlsx/epub，`src/tools/fetch.ts:52-58`）、notebook（可编辑单元格）、二进制（ELF/PE/Mach-O 概要/函数/`:asm`/`:imports`/`:strings`/`:xrefs`）、URL（reader 文本或 `:raw`）。实现文件：`read.ts` / `read-format.ts` / `read-pdf.ts` / `read-archive.ts` / `read-sqlite.ts` / `read-json.ts` / `read-binary.ts`。

## 15. 总表（外部世界 / 集成家族）

| 家族 | 模型可见名 | 形态 | 外部依赖 | 可用条件 | 审批 | ZenAgent | 优先级 |
|---|---|---|---|---|---|---|---|
| shell | `bash` | top-level tool | 内嵌 brush-core（+可选用户 shell/PTY） | `bash.enabled` | exec（含 pattern 规则） | ✅ 有（POSIX-only） | shell 抽象 P2 |
| 搜索 | `web_search` | top-level tool | 引擎 key 或 grounded 模型 + auth storage | `web_search.enabled` | read | ✅ 有（5 provider 链） | 保持，补强见 §16 |
| 抓网页 | **无独立工具**（`read <url>`） | read 分支 | trafilatura/lynx/Parallel/Firecrawl/Jina、markit | `fetch.enabled` | read | ✅ `fetch` 独立工具 | 保持/小增强 |
| 浏览器 | `browser` | eval prelude | puppeteer-core + Chromium（或 relay/Tern/cmux） | `browser.enabled` | exec | ❌ | N |
| 桌面 | `computer` | eval prelude | pi-natives 原生 desktop + OS 权限 | `computer.enabled`（默认 false） | 按 action read/exec | ❌ | N |
| GitHub | `github` | top-level tool | `gh` CLI + 认证 | `github.available()` | 只读 op=read，其余 exec | ❌ | P1 条件 |
| GitLab | 无工具；`mr://` + `/review` | internal URL | `glab` CLI | `glab` 存在 | read | ❌ | P2 |
| LSP | `lsp` | top-level tool | 语言服务器二进制（PATH）、可选 lspmux | `lsp.enabled`（默认 true，lazy） | read/write | ❌ | **P0** |
| 调试 | `debug` | top-level tool | 14 种 DAP adapter 二进制 | `debug.enabled` | read/exec | ❌ | N |
| 逆向 | `ida` | top-level tool | IDA Pro + idalib（商业） | `cfgIdaAvailable` | read/write/exec | ❌ | N |
| 安全扫描 | `security_scan` | top-level tool | OMP 自建扫描服务 + Codex Security 云（OAuth） | `security.enabled`（默认 false） | exec | ❌ | N |
| 生图 | `generate_image` | custom tool | 配置的 image 模型 API | `generate_image.enabled`（默认 false） | write | ❌ | N |
| 语音 | `tts` | custom tool | Kokoro ONNX（内置）/ xAI / OpenAI 兼容 | `speechgen.enabled`（默认 false） | write | ❌ | N |
| 工具卸载 | `xd://` 设备 | 机制 | 无 | `tools.xdev`（默认 true） | 继承被包裹工具 | ❌ | 观察（P2+） |

## 16. 最后：值得抄的最小集 / 明确不抄 / 开放问题

### 16.1 值得抄（必答 Q2：哪些是真价值，按性价比排序）

| # | 项 | 成本 | 为什么 | 最小验收口径 |
|---|---|---|---|---|
| 1 | **`lsp` 工具（只读 4 action + rename）** | 2–4d | 文本工具替代不了的 cross-file 语义；子进程 + JSON-RPC 不需要新依赖 | `diagnostics`（file/glob）、`symbols`（file/workspace）、`references`、`definition`、`rename`（apply/preview）；server 表按扩展名映射，缺二进制时动作报错并提示安装 |
| 2 | **bash 执行后端抽象（shell/args/平台）** | 0.5–1d | 用户明确提到 powershell；这是平台缺口不是工具缺口 | `shell.py`：`resolve_shell()` 返回 (shell, args)，POSIX/Windows 各一条路径；`bash` 工具名与参数不变 |
| 3 | **fetch 的正文质量小步增强** | 0.5d | 不加依赖也能拿到大头：优先 `<article>/<main>`、去 nav/aside/footer、折叠空行、保留列表/代码块；HTML 实体解码已做 | 同 URL 下正文更干净、长度更短；不引入第三方库 |
| 4 | **`github` 只读子集（条件项）** | 1–2d | PR 场景高频；`gh` 子进程不破依赖约束 | 仅 `pr view` / `pr diff` / `pr checks` / `run logs`；`gh` 不存在时工具不注册 |
| 5 | `fetch` 结果的可观测性对齐 | 0.25d | ZenAgent 已有 `/search`；给 `fetch` 补 `details.provider/method` 一起进 `/search` 风格诊断 | `/search` 或 `/fetch` 能显示「用了哪条回退」 |

### 16.2 明确不抄

- **browser / computer**：`puppeteer-core + Chromium` 或 `pi-natives` 原生桌面，与「httpx 唯一运行时依赖」正面冲突；两者在 omp 里也不是工具，而是 eval prelude，抄形态没有意义。
- **27 个搜索后端 / 75 个站点抓取器**：omp 为 40+ provider 账号体系付的成本；ZenAgent 的 5 provider 链 + 查询算子已经覆盖真实需求。
- **debug / ida / security_scan / tts / generate_image**：垂直场景或需要商业授权/自建服务。
- **xd:// 工具卸载**：ZenAgent 9 个工具，schema 预算不是瓶颈；等 MCP 工具数量上来再评估。
- **cursor/acp bridge**：宿主编辑器集成，ZenAgent 是独立 CLI/TUI，无对应宿主。

### 16.3 开放问题（需要 Lead 决策）

1. **LSP 优先级**：P0 只在「v0.4 要服务真实多文件重构」成立；如果 v0.4 主线是内核简化（更少状态、更强流式/恢复），LSP 应降 P1。**建议：先做 1 个 server（typescript-language-server 或 pyright）+ 4 个只读 action 的探针，再决定是否全量。**
2. **是否接受「可选外部二进制」**：LSP/gh 都需要用户在 PATH 里装东西。ZenAgent 的「httpx only」是**运行时依赖**约束，外部 CLI 依赖（gh/lsp）属于可选能力，建议以「检测不到就静默不注册」的方式规避，保持 pip 依赖不变。
3. **fetch 是否要支持 PDF**：纯 Python 无依赖解析 PDF 不现实；建议**不在 fetch 里做**，让模型用 `bash` 调 `pdftotext`/`python -m ...`，把「文档格式」留给 shell——这与 omp 的 markit 路线相反但更符合最小内核。
4. **web 是否补 answer 类搜索**：omp 的 grounded 搜索能给 answer+citations，但需要把搜索绑到模型账号。若 ZenAgent 已有 DeepSeek key，可在 deepseek provider 内把「answer 摘要」做成可选输出（不新增 provider）——需确认 DeepSeek search API 是否返回 answer `[不确定]`。

### 16.4 minimal v0.4 proposal（若只做一件事）

**v0.4 = 「让模型能读懂工程」而不是「让模型能操作世界」**：

1. （P0）`lsp` 最小工具：`diagnostics` / `symbols` / `references` / `definition` / `rename`，server 表内置 5–8 个常见语言，缺 server 静默提示；审批：只读 4 个 → read，rename → write。
2. （P1）`bash` 平台抽象：`shell.py` 输出 `(shell, args)`，为 Windows 预留 powershell/cmd；参数契约不变（不改工具名）。
3. （P1）`fetch` 正文启发式增强（无新依赖）+ `details.method` 可观测。
4. （P2，条件）`github` 只读 4 op，gated on `gh`。
5. **不做**：browser/computer/debug/ida/security/tts/image/xdev。

> 一句话：omp 在「外部世界」上的体量是它 40+ provider 账号体系的副产物；对 ZenAgent，唯一能立刻放大模型能力且不破坏定位的，是 **LSP（语义）** 和 **shell 平台抽象（可移植）**，其余都是可选项或明确不做。
