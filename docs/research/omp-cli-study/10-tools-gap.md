# 10 · ZenAgent × omp 工具层差距与 v0.4 路线（工具面 + 工具内核）

> **日期**：2026-10-31 · **作者**：Lead（综合 `10a`–`10e` 五份源码级调研 + 本机实测核对）
> **基线**
> - **ZenAgent**：tag `v0.3.0`，HEAD `2aab733`。本文所有现状都回源码核对过；`10d` 跑过 `tests/test_tools_*` 等 **155 条测试全绿**。
> - **omp**：`@oh-my-pi/pi-coding-agent@18.14.0` + `@oh-my-pi/pi-agent-core@18.14.0`，**本地安装包源码**（不是仓库 main 快照）。
>
> **证据缩写**（正文简写，均为绝对路径前缀）
> - `omp/` = `~/.bun/install/global/node_modules/@oh-my-pi/pi-coding-agent/src/`
> - `core/` = `.../@oh-my-pi/pi-agent-core/src/`；`tui/` = `.../@oh-my-pi/pi-tui/src/`；`util/` = `.../@oh-my-pi/pi-utils/src/`
> - `za/` = `/Users/mac/zen-agent/zenagent/`
>
> **口径**：只讨论**模型可见的工具**和**工具内核机制**，不讨论 TUI 视觉（渲染见 `04`/`09`）。标注「源码确认」的带 `file:line`；标注「推断」的不是 omp/本仓库明文。
> **注意**：调研期间工作树存在与本次调研**无关**的 TUI 改动（`zenagent/cli/tui/widgets/rows.py`、`tests/test_tui_rows.py` 等 6 个文件）。本次只新增 `docs/research/omp-cli-study/10*.md`，未改任何代码/测试。

---

## 0. 结论速览

**先纠正三个前提（都能用命令复现）：**

1. **不是 4 个工具，是 9 个**。默认暴露：`read_file`、`write_file`、`edit_file`、`list_dir`、`bash`、`glob`、`grep`、`web_search`、`fetch`（`za/tools/__init__.py:27-37`；本机实测打印一致）。另有条件注册的 `read_skill`（发现到 skill 才注册，`za/app/runtime.py:186-193`）、MCP 工具、扩展工具。
2. **`web_search` 没有"没了"**。它无条件在 `ALL_TOOLS` 里，工具层不检查 key；无 key 时链仍会试 keyless 的 Bing/DuckDuckGo（`za/tools/web/registry.py:30`、`providers.py:333-346`）。**真正让用户觉得"search 没了"的是权限分类**：`READ_ONLY_TOOLS` 漏了 `web_search`/`fetch`（`za/app/permissions.py:32`），于是 **plan 模式直接拒、headless 的 ask 模式（默认）无 `--yes` 也拒**（本机实测，见 §3.3）——而 `docs/guide/tools-permissions.md:14-15,65` 却写它们只读免确认，代码与文档不一致。这是 v0.4 最便宜、最该先修的"体感 bug"。
3. **omp 没有 `powershell` 工具**。powershell/pwsh/cmd 只是**单个 `bash` 工具的 shell 后端**（`omp/tools/builtin-names.ts:1-33` 的工具名表里没有它；shell 来自设置经 `util/procmgr.ts:66-90` 解析成 `-NoLogo -Command` 等 spawn 参数；`omp/cli/profile-alias.ts:6` 只是 `omp` 的 shell profile 别名）。omp 也没有独立 `fetch` 工具——抓网页是 `read` 的 URL 分支（`omp/tools/read.ts:1779-1797`）；`browser`/`computer` 是 `eval` 的 prelude 全局对象，不是工具（`omp/eval/preludes.ts:29-32`）。

**omp 的真实工具面（源码确认）**：名册 **30 个 builtin + 3 个 hidden**（`omp/tools/builtin-names.ts:1-30,36`），但模型默认顶层可见的只有约 **16–18 个**（13 个 `essential` + 5 个钉在顶层的 `discoverable`：`todo`/`ask`/`grep`/`web_search`/`yield`，`omp/tools/essential-tools.ts:24-38`、`omp/tools/xdev.ts:58-66`）；其余按 `createIf`/settings 条件注册，或挂到 `xd://` 设备后面。

**一句话结论**：**差距不在"工具数量"，在四件事——读写分辨率、工具内核治理、编排工具、世界集成。**
> v0.4 应该先把现有 9 个工具做扎实 + 给工具内核补治理（P0，约 2–3 天量级），再加 `todo`（P1），`task`/`wait`/`lsp`/`github` 全部后置为条件项；**不要为了对齐 30 个工具而膨胀**。

**优先级总览（详见 §6）：**

| 层 | 内容 | 规模 |
|---|---|---|
| **P0（v0.4 必做）** | 权限元数据/只读分类、统一结果预算、read 分页、write 防截断回写、bash 非交互环境、partial 结果接 UI | 6 项 ≈ 350–450 行 + 测试 |
| **P1（v0.4 第二阶段）** | `todo` 工具、grep 分页、审批 v2（目标键）、超时/取消框架、错误类别+循环守卫、max_tokens 拟合、并发语义修正、replace_all/CRLF-BOM、工具政策提示词 | 9 项 ≈ 600–800 行 + 测试 |
| **P2 / 条件** | background bash + `wait`、`task` 同步单发、`ask`、LSP 最小版、shell 抽象、artifact 落盘、hidden/registry、MCP 桥增强、run 工具统计 | v0.4.x / v0.5 |
| **N（明确不做）** | browser/computer/debug/ida/security/tts/image、eval 内核、memory backend、managed skill、xd://、speculation、hashline、find 语义搜索、27 个搜索后端… | 见 §6.5 |

---

## 1. 调研基线与方法

| 项 | 值 |
|---|---|
| ZenAgent 基线 | `v0.3.0` @ `2aab733`（2026-10-30 发布）；工作树另有 6 个无关 TUI 改动 |
| omp 基线 | 本地安装包 `@oh-my-pi/pi-coding-agent@18.14.0`，源码目录（非 git main；行号以该版本为准） |
| 方法 | 5 名研究员按族并行做源码级盘查（每条结论 `file:line`），Lead 再做交叉核对与优先级裁决；关键结论本机复现（工具清单、权限门、测试） |
| 未做 | 没有运行 omp（本机 bun 版本不满足其 18.14.0 要求，`00-README` 已记录）；没有真实网络/key 下的 web_search 成功路径；400 溢出区间是代码推断未实测 |

**五份证据附录**（本文所有细节都指向它们）：

| 文件 | 内容 | 行数 |
|---|---|---|
| [`10a-omp-core-tools.md`](10a-omp-core-tools.md) | 核心工具族：read/write/edit/bash/shell/glob/grep/find/ast_*/checkpoint + 横切设施 | 297 |
| [`10b-omp-orchestration-tools.md`](10b-omp-orchestration-tools.md) | 编排与自主性：task/todo/wait/ask/eval/memory/learn/plan + todo-task-wait 先后论证 | 243 |
| [`10c-omp-world-tools.md`](10c-omp-world-tools.md) | 外部世界：web_search/fetch(URL)/browser/computer/github/lsp/debug/ida/security/tts/image/xdev + powershell 裁决 | 257 |
| [`10d-zen-tool-baseline.md`](10d-zen-tool-baseline.md) | ZenAgent 工具面 + 工具内核基线（含 155 条测试与用户说法核实） | 194 |
| [`10e-omp-tool-control-plane.md`](10e-omp-tool-control-plane.md) | 控制面：契约/加载/审批/超时/流式/预算/错误/并行 + v0.4 最小内核改造清单 | 358 |

---

## 2. omp 工具全景（30 + 3，按族归并）

### 2.1 名册与"实际暴露"是两件事

omp 把工具分成四个展示层（源码确认）：

| 层 | 数量 | 名单 | 机制 |
|---|---|---|---|
| essential（顶层常驻） | 13 | read / write / bash / edit / glob / find / eval / task / wait / learn / manage_skill / context_notes / new_context | 类上 `loadMode="essential"`（`omp/tools/essential-tools.ts:24-38`） |
| 固定顶层 discoverable | 5 | todo / ask / grep / web_search / yield | `XDEV_KEEP_TOP_LEVEL`（`omp/tools/xdev.ts:52-66`），各自有"藏起来就不可达"的理由 |
| discoverable → `xd://` | 其余 | ast_grep / ast_edit / lsp / github / memory_* / checkpoint / rewind / debug / ida / security_scan… | schema 不进请求，用 `read xd://` 查文档、`write xd://<tool>` 执行（`omp/tools/xdev.ts:1-31,85-88`） |
| hidden | 3 | yield / goal / think | 按模式/场景启用（`omp/tools/builtin-names.ts:36`） |

> 所以"omp 有 30 个工具"是对的，但"模型每次请求都看到 30 个 schema"是错的：默认顶层约 16–18 个，再经 `createIf`/settings 过滤。**ZenAgent 的 9 个工具 schema 成本完全可接受，现在不需要 omp 那套加载压缩。**

### 2.2 全部 30 个 builtin 按族（含对 ZenAgent 的相关性）

| 族 | 模型可见名 | 默认暴露 | 外部依赖/条件 | ZenAgent 相关性 |
|---|---|---|---|---|
| **核心 IO**（6） | read / write / edit / bash / glob / grep | 常驻 | 内嵌 brush-core shell（bash）；rg 类搜索自实现 | **直接对标，P0/P1 做深** |
| 搜索扩展（3） | find / ast_grep / ast_edit | find=essential；ast_*=discoverable+settings | find 需 judge 模型；ast_* 需 ast-grep/语法引擎 | find=N；ast_*=P2/N |
| **编排**（5） | task / wait / todo / ask / eval | task/wait/eval=essential；todo/ask=顶层 | task 需子代理运行时；eval 需 py/js 内核 | **todo=P1；task/wait/ask/eval 见 §6** |
| 上下文/记忆/技能（9） | learn / manage_skill / memory_edit / retain / recall / reflect / checkpoint / rewind / context_notes / new_context | 多为条件 | memory backend（hindsight/mnemopi/local）、experimental 设置 | learn/manage=P2/N；memory/context=N；checkpoint/rewind=P2 条件 |
| **世界集成**（4+） | web_search / github / lsp / debug / ida / security_scan | web_search=顶层；其余条件或 discoverable | 引擎/模型账号、`gh`、语言服务器、DAP adapter、IDA 授权 | **web 保持；LSP=P1 条件；github=P1 条件；debug/ida/security=N** |
| hidden（3） | yield / goal / think | 不默认 | 随 task/goal/外部思考模式 | 随对应系统，v0.4 不做 |

**不是工具、但常被误算的东西：**

| 名字 | 实际形态 | 证据 |
|---|---|---|
| `powershell` / `cmd` / `pwsh` | `bash` 工具的 shell 后端（设置 `shellPath` → spawn 参数） | `omp/tools/bash.ts:513-514`（名字写死 bash）；`util/procmgr.ts:66-90`；`omp/cli/profile-alias.ts:6` 只是 profile 别名 |
| `fetch` | 不存在独立工具；`read <url>` 的 URL 分支（6 级 reader + 75 站点 handler + 文档格式） | `omp/tools/read.ts:1779-1797`；`omp/tools/fetch.ts:551-560` |
| `browser` / `computer` | `eval` 的 prelude 全局对象，无 JSON schema、不进工具表 | `omp/eval/preludes.ts:29-32`；`omp/tools/browser/prelude-definition.ts:23-40` |
| `xd://` / `xd://resolve` | 工具卸载/内部动作协议，不是模型可见工具名 | `omp/tools/xdev.ts:1-31`；`omp/tools/resolve.ts:1-19` |

---

## 3. ZenAgent 现状（核实版）

### 3.1 默认工具面（9 个，顺序即 prompt 顺序）

| 工具 | 参数 | 权限档 | 关键限制 | 位置 |
|---|---|---|---|---|
| `read_file` | `path*` | 只读 | 2000 行截断；**无 offset/limit、无行号、无续读坐标**；截断提示却说 "read a smaller range"（但参数不存在） | `za/tools/fs.py:105-139,237-244` |
| `write_file` | `path*`,`content*` | 写 | 覆盖写；≤200 行 diff；**无"截断回写"防护** | `za/tools/fs.py:141-156,246-256` |
| `edit_file` | `path*`,`old_string*`,`new_string*` | 写 | 唯一匹配；0/多匹配即拒；无 replace_all | `za/tools/fs.py:158-200,258-272` |
| `list_dir` | `path*` | 只读 | 500 条上限 | `za/tools/fs.py:202-235,274-280` |
| `bash` | `command*`,`timeout_s?` | 执行 | 30,000B 头+尾截断；默认 120s；`killpg` 杀进程组；**无 cwd/env/async/artifact**；stderr 合并 | `za/tools/bash.py:31-34,107,206-224` |
| `glob` | `pattern*`,`path?` | 只读 | 500 条；字典序；不读 .gitignore | `za/tools/search.py:24,27-56,161-176` |
| `grep` | `pattern*`,`path?`,`glob?` | 只读 | **200 条全局硬截断、无翻页**；rg 优先，缺失退 Python | `za/tools/search.py:26,81-158,178-197` |
| `web_search` | `query*`,`limit?`,`recency?` | **不在只读集合** | 5 家 provider 链 + 查询算子 + 去重 + 60s 缓存；无 key 也能跑（bing/ddg） | `za/tools/web/tools.py:64-108,111-143` |
| `fetch` | `url*` | **不在只读集合** | 12,000 字符；`strip_html`；只允许 http(s)；无 reader 回退/文档格式 | `za/tools/web/tools.py:91-108,145-157` |

条件工具：`read_skill`（发现到 skill 才注册，`za/app/runtime.py:186-193`、`za/skills.py:104-147`）、MCP 工具（`mcp__<server>__<tool>`，`za/app/runtime.py:180-183`、`za/mcp/manager.py:25-33`）、扩展工具（`za/app/runtime.py:201-211`）。

### 3.2 对用户说法的核实

| 说法 | 判定 | 证据 |
|---|---|---|
| "tool 只有 bash/write/edit/grep" | ❌ 不准确 | 默认 9 个（见 §3.1）；`read_skill`/MCP/扩展另算 |
| "需要配置 provider 的 search 没了" | ⚠️ 一半：search 在、provider 不是必须，但 **plan/ask 会拒** | 见 §3.3；工具层无 key 检查（`za/tools/web/tools.py:64-84`） |
| "omp 有 powershell 等一堆工具" | ⚠️ omp 工具确实多，但 **powershell 不是工具** | §2.2 |

### 3.3 本机实测：权限门把 `web_search`/`fetch` 拦掉了（"search 没了"的真因）

`PermissionGate.check()` 在 `ask` 模式且非交互、没 `--yes` 时拒绝一切非只读工具；`READ_ONLY_TOOLS = {read_file, list_dir, glob, grep, read_skill}`（`za/app/permissions.py:29-32`）**不含 web_search/fetch**。实测输出（本机，2026-10-31）：

```
READ_ONLY_TOOLS = ['glob','grep','list_dir','read_file','read_skill']
mode=plan  read_file=Y web_search=N fetch=N bash=N write_file=N
mode=ask   read_file=Y web_search=N fetch=N bash=N write_file=N   # 非交互、无 --yes
mode=full  read_file=Y web_search=Y fetch=Y bash=Y write_file=Y
--read-only  web_search=N fetch=N grep=Y
```

即：**plan 模式不能联网调研；`zen -p ...`（默认 ask、非交互）也直接拒**。这与 `docs/guide/tools-permissions.md:14-15`（称 web 只读免确认）、`:65`（称 `--read-only` 放行 web）直接冲突，属于**代码/文档不一致 + 分类缺失**，不是工具不存在。

### 3.4 工具内核现状（一句话版）

契约是 `AgentTool(name/description/parameters/execution_mode/execute)` + `ToolResultAgent(content/details/is_error/terminate)`（`za/agent/loop.py:68-105`）；参数校验是**故意窄**的手写子集（`za/agent/validate.py:48-87`）；调度是"parallel 组 + sequential 组，两轨同时跑"（`za/agent/loop.py:278-332`）；错误全部降级为 `is_error`（`za/agent/loop.py:227-259`）；`on_update` 管线存在但**只有 bash 发射、且 4 个前端都不消费**（`za/agent/loop.py:249-259,305-320`；`za/app/chat.py:320-352`、`za/cli/repl.py:367-413`、`za/cli/main.py:118-147`）。`details` 只给 UI 且**不落盘**（`za/types.py:179-194`）。

---

## 4. 工具面差距矩阵（按能力，不按工具名）

图例：`P0` 立即补 · `P1` 值得做 · `P2` 条件项/后置 · `N` 不做。成本是单人估算（含测试）。

### 4.1 核心 IO（read / write / edit / bash / glob / grep）

| # | 能力 | omp 做法（源码确认） | ZenAgent 现状 | 差距 | 优先级 |
|---|---|---|---|---|---|
| 1 | **read 分页/选择器** | 唯一参数 `path`，选择器内联：`:50`、`:50-200`、`:50+150`、`:-60`、逗号多段、`:raw`；默认 **300 行/页**，上限 3000 行/50KB（`omp/tools/read.ts:820-822`；`omp/tools/settings.ts:160-163`；`omp/prompts/tools/read.md:1-14`） | `path` only，超过 2000 行看不到、**无续读坐标**（`za/tools/fs.py:105-139`） | 每读大文件必撞 | **P0** |
| 2 | **read 格式/目录** | 1 个入口嗅探 12 类内容：目录、JSON(jq)、SQLite、归档、PDF、二进制、图片、notebook、URL…（`omp/tools/read.ts:1912+`） | 仅 UTF-8 文本；另有 `list_dir` | 大，但多数可后置 | P2（sqlite/zip 用 stdlib）；PDF=N |
| 3 | **write 截断回写守卫** | 若 content 以 read 截断提示结尾且比现文件短 → 拒写并提示重读（`omp/tools/write.ts:319-350`） | 无 | **真实数据丢失**：read 截断→write 回写会静默截断文件 | **P0** |
| 4 | write CRLF/BOM/可执行位 | 读目标约定后补齐并回 Note（`omp/tools/write.ts:417-467,1117-1121`） | 无 | Windows/生成文件整文件 diff | P1 |
| 5 | **edit 模式** | 5 种，默认 `hashline`（内容哈希+行号锚定），弱模型回退 `replace`；`replace_all`；conflict:// 拼接（`omp/edit/settings.ts:36-41`；`omp/edit/schemas.ts:3-9`；`omp/tools/conflict-detect.ts:1-30`） | 单一 old/new 唯一匹配 | 锚定/过时检测、批量替换缺失 | replace_all=P1；hashline=N |
| 6 | **bash 非交互环境** | ~45 项 env（`PAGER=cat`/`TERM=dumb`/`CI=true`/`GIT_TERMINAL_PROMPT=0`/`npm_config_yes`/`PIP_NO_INPUT`…）+ Windows UTF-8 兜底（`omp/exec/non-interactive-env.ts:6-135`） | 无 | `git log`/`man`/`npm` 可能卡到超时 | **P0** |
| 7 | bash cwd / async / 服务 / pty / artifact | `cwd` 一等参数；`async` 后台作业 `bg_N`；`name+ready` 服务；pty；输出超限落 `artifact://`（16MiB）（`omp/tools/bash.ts:333-375,1223`；`omp/tools/tool-timeouts.ts:11`） | 自由 `os.getcwd()`；无 | 后台/服务是独立子系统（见 wait） | cwd=P1；artifact=P2；async=P2（v0.5） |
| 8 | **grep 分页/每文件上限** | 一页 20 个文件、每文件 20 条、单文件 200 条；`skip` 翻页；30s 预算；每文件只搜前 4MB（`omp/tools/grep.ts:71-104,1045`） | **200 条全局硬截断，无翻页**；一个热文件可吃掉全部配额 | 大文件/大仓检索质量 | P1（~40 行） |
| 9 | glob limit/gitignore/mtime | 200 上限、hidden/gitignore 默认 true、mtime 倒序（`omp/tools/glob.ts:36-47,294-295`） | 500、字典序、不读 .gitignore | 小 | P2 |
| 10 | ast_grep/ast_edit | 结构匹配/提案制编辑，discoverable（`omp/tools/ast-grep.ts:41-47,194`；`ast-edit.ts:41-54`） | 无 | 依赖外部二进制 | P2/N（需裁决） |
| 11 | find 语义搜索 | `find` 需 judge 模型（`omp/tools/jfind/index.ts:33-60`） | 无 | 与最小内核冲突 | **N** |

### 4.2 编排 / 自主性

| # | 能力 | omp 做法（源码确认） | ZenAgent 现状 | 差距 | 优先级 |
|---|---|---|---|---|---|
| 12 | **todo 进度清单** | 单工具 9 个 op（init/start/done/rm/drop/block/unblock/append/view），**按内容寻址**、批量原子性；session details 持久化；HUD；外加 tracker（首轮 eager、跑中 nudge、停止提醒≤3）（`omp/tools/todo.ts:53-70,713-763`；`omp/session/todo-tracker.ts:163-320`） | 无 | 长任务行为改变最大的单项 | **P1（v0.4 首选新工具）** |
| 13 | **task 子代理** | 单发/批量/async/blocking/结构化 yield/worktree 隔离/并发 32/递归 2/完成度探针/subagentCost（`omp/task/types.ts:75-130`；`omp/task/index.ts:771-774,1315-1337`；`omp/task/settings.ts:147-166,268-311,383-386`） | 无（`Agent` 类可复用，但无工具） | 契约面极重，成本 3–5 倍 todo | P1 条件 / v0.5；最小=同步单发（~250–400 行） |
| 14 | **wait / 后台作业** | schema `{}`，只等自己拥有的 job/service/本轮未答 agent；`useless` 标记让空等可被压缩丢弃；async job 投递（`omp/tools/wait.ts:23,88-95,146-160`；`omp/async/job-manager.ts:49`） | 无后台世界 | 先做 wait 只会得到"永远 Nothing to wait for" | **P2（前置 = background bash）** |
| 15 | **ask 结构化提问** | `questions[]{id,question,header,options,multi,recommended}`；plan 模式禁超时；无 UI 时返回专用文案（`omp/tools/ask.ts:70-90,755-758,637-645`） | 只有审批弹窗 | 模型不能主动问用户 | P2；若 plan 升级为工作流则 P1 |
| 16 | eval 代码化编排 | 持久内核 + `agent()` handle + `tool.<name>()` 桥 + workpool；judge 模型调用（`omp/prompts/tools/eval.md`；`eval-agents.md:2-9`；`omp/tools/tool-timeouts.ts:12`） | 无 | 与 httpx-only/无框架定位冲突 | **N** |
| 17 | memory / learn / manage_skill | 外部 backend + 自改写 skill，默认 discoverable（`omp/tools/memory-recall.ts:13-16`；`omp/tools/learn.ts:71-77`；`manage-skill.ts:52`） | `read_skill` 只读 | 打开自修改面 | **N** |
| 18 | checkpoint / rewind | 模型可见工具：checkpoint(goal)→探索→rewind(report)（`omp/tools/checkpoint.ts:29-140`；`builtin-names.ts:17-18`） | 只有自动压缩/`/undo` | 与上下文治理重叠 | P2（待裁决） |

### 4.3 世界集成

| # | 能力 | omp 做法（源码确认） | ZenAgent 现状 | 判定 | 优先级 |
|---|---|---|---|---|---|
| 19 | **web_search** | 27 后端（21 引擎 + 6 grounded），按 model role chain + AuthStorage 选择；answer+citations 可选（`omp/web/search/provider.ts:18-51`；`omp/web/search/index.ts:60-90,196-215`） | 5 家链 + 查询算子（含 `lang:`/负向）+ 去重 + 60s 缓存 + `/search` 诊断；**无 key 可跑**（`za/docs/internals/web-search.md` 全文） | **搜索侧不落后，部分领先**（更简单/可移植/零配置） | 保持；只修权限（P0） |
| 20 | **fetch 正文质量** | `read <url>`：75 站点 handler → 6 级 reader（native/trafilatura/lynx/Parallel/Firecrawl/Jina）→ markit 文档格式；JS 门检测（`omp/tools/fetch.ts:551-721`；`omp/web/scrapers/index.ts:83`） | `strip_html` + 12k 截断（`za/tools/web/tools.py:91-108`） | omp 明显领先"读" | P1 小步增强（article/main/nav 清洗，无新依赖） |
| 21 | **LSP** | `lsp` 工具：diagnostics/symbols/references/definition/rename/code_actions…；55 个 server 定义按 PATH 探测；只读 action=read（`omp/lsp/tool.ts:178-212`；`omp/lsp/defaults.json`） | 无 | 文本工具替代不了的跨文件语义；子进程+JSON-RPC 不违依赖约束 | **P1 条件项**（多文件重构是主线才做） |
| 22 | github / gitlab | `github` 单工具 11 op（pr view/diff/checkout/search/run_watch…），需 `gh`；gitlab 只读走 `mr://`（`omp/tools/gh.ts:69-107,154-157`；`omp/tools/glab-mr.ts`） | 无（bash 可调 `gh`） | PR 场景真实高频，但非内核必需 | P1 条件 / P2 |
| 23 | browser / computer | puppeteer+Chromium / pi-natives 原生桌面，且都不是工具而是 eval prelude（`omp/tools/browser/*`；`omp/tools/computer.ts:127-166`） | 无 | 与 httpx-only 冲突 | **N** |
| 24 | debug / ida / security_scan / tts / generate_image | DAP adapter / IDA 授权 / 自建安全服务 / 语音 / 图像模型（`omp/tools/{debug,ida,security-scan,tts,image-gen}.ts`） | 无 | 垂直场景成本 | **N** |

---

## 5. 工具内核机制差距（比"再加几个工具"更重要）

omp 的 `AgentTool` 有 30+ 个可选字段（执行/审批/并发/中断/推测/截断，`core/types.ts:1139-1274`），但真正撑起体验的是统一执行器。对 ZenAgent 有价值的机制如下（完整证据见 `10e`）：

| # | 机制 | omp 做法（源码确认） | ZenAgent 现状（源码确认） | 建议 |
|---|---|---|---|---|
| 1 | **工具声明与上下文** | 工具声明 `approval/timeout/concurrency/hidden/loadMode` 等；`execute` 收 `context`（`core/types.ts:1095-1117`）；`before/after` 钩子能读工具声明、可改写 args 后重新校验（`core/types.ts:917-949`） | `AgentTool` 只有 5 个字段；钩子只拿到 `ToolCall`+args，**读不到工具声明**（`za/agent/loop.py:82-105,133-136,240-243`） | **P0**：加 `approval(args)->{tier,reason,targets}` + `timeout` + `context`；这是审批/超时/UI 回填的共同前置 |
| 2 | **结果预算 + 统一截断 notice** | `wrapToolWithMetaNotice` 包装所有工具：>50KB 落 artifact（head/tail 各 20KB），列宽 768，notice 带 `artifact://N`/`nextOffset`（`omp/tools/output-meta.ts:454-470,554-720,780-800`；`omp/tools/settings.ts:12-134`） | **每个工具各自硬编码上限**（bash 30KB、read 2000 行、grep 200 条、fetch 12k、MCP 无上限），无统一 notice/落盘/列宽（`za/tools/*.py`；`za/mcp/manager.py:198-225`） | **P0**（先做预算+notice；artifact 落盘 P1/P2） |
| 3 | **审批：tier + 目标键 + 记忆** | 声明三档 `read/write/exec`；解析链"工具声明→用户策略→模式等级→目标缓存→受限目录"；会话内存 + SQLite 两层缓存，键 `tier\0path`（`omp/tools/approval.ts:132-136,364-387`；`approval-cache.ts:14-26,84-124,178-190`） | 硬编码 `READ_ONLY_TOOLS` 名字集合；"always allow"只按**工具名**记，一次 allow 整类放行（`za/app/permissions.py:29-32,130,198-200`） | **P0** 分类元数据；**P1** 目标键缓存（不做 SQLite） |
| 4 | **超时** | 不是调度器：`TOOL_TIMEOUTS` 钳制表 + 工具内实现，内核只传 `AbortSignal`（`omp/tools/tool-timeouts.ts:10-39`；调用点 `omp/tools/bash.ts:1223` 等） | 只有 bash 自带 `timeout_s`；fetch 固定 20s 不可调；fs/search 无界；内核无 `wait_for`（`za/tools/bash.py:107`；`za/tools/web/net.py:14-22`；`za/agent/loop.py` 无超时结构） | **P1**：`TOOL_TIMEOUTS` 表 + `clamp_timeout` + 内核级取消（fs/search/web/MCP） |
| 5 | **并发语义** | `concurrency: shared|exclusive|fn`：`write`/`edit` 声明 exclusive，`bash` 仅 pty 独占；exclusive 与前后的 shared **真互斥**（`core/types.ts:1176`；`omp/tools/write.ts:612`；`omp/edit/index.ts:365`；`core/agent-loop.ts:3786-3900`） | `execution_mode: parallel|sequential`，但**两轨同时 `gather`**：sequential 与 parallel 并不互斥（`za/agent/loop.py:278-332`，缺陷）；且 9 个工具全未标 sequential | **P1**：修正为真互斥；给 write/edit 标 exclusive（当前 fs 是同步 IO、实际交错窗口小，但语义上无保护） |
| 6 | **partial result** | `onUpdate → tool_execution_update`；run 结束后还有 `onDetachedToolUpdate`（`core/agent-loop.ts:3668-3678`） | 管线存在、bash 是唯一发射者（4KB/0.5s），但**4 个前端全不消费**：`tool_progress_line` 是死代码（`za/agent/loop.py:249-259,305-320`；`za/app/chat.py:320-352`；`za/cli/render.py:131-140`） | **P0/P1**：TUI 工具卡/REPL 接一条节流进度行；detached 等后台系统 |
| 7 | **错误契约** | 工具可 `throw ToolError`，内核 `renderError`/`coerceToolResult` 统一规范化（`omp/tools/tool-errors.ts:13-67`；`core/agent-loop.ts:556-590`） | 零抛错路线（返回 `is_error`），文本无类别（`za/agent/loop.py:227-259`） | **P1**：加 `error_kind`（policy/invalid_args/not_found/timeout/aborted/tool_error） |
| 8 | **循环守卫** | 跨轮 `(name,canonical args)` 哈希连续 ≥5 注入纠正消息，不杀 run；exempt poll 类（`core/tool-call-loop-guard`；`omp/session/stream-guards.ts:224-273`） | 无；模型重复同一调用会一路烧到 `max_turns=100`（`za/agent/loop.py:177`） | **P1**（<100 行） |
| 9 | **请求级 max_tokens 拟合** | `fitOutputTokensToContextWindow`：`max_tokens = max(1024, min(cap, window−prompt−headroom))`，防 Chat Completions 系 400（`core/output-budget.ts:81-96`） | `max_tokens = model.max_tokens` 不拟合；prompt 落在 `(window−max_tokens, 0.85·window)` 区间会 400（`za/providers/anthropic_messages.py:200-205`；`za/config.py:43-45,321-363`） | **P1**（~20 行，消灭一类 400） |
| 10 | **工具政策提示词** | 系统提示有 Specialized/General/Exploration 三段式工具政策（`omp/prompts/system/system-prompt.md:96-136`） | 只有一句 "Prefer using tools"（`za/app/support.py:37-44`） | **P1**（半天，几乎零成本） |
| 11 | **工具注册/展示层** | `BUILTIN_TOOLS` 注册表 + `hidden/defaultInactive` + settings reconcile（`omp/tools/index.ts:570-823`；`omp/session/session-tools.ts:1272-1460`） | 平铺 `ALL_TOOLS` + REPL 手写 `refresh_mcp`；TUI 无热更新（`za/tools/__init__.py:27-37`；`za/cli/repl.py:647-661`） | **P1/P2**：hidden/default_inactive + 统一 registry/reconcile |
| 12 | **MCP 桥** | 工具名铸造/去重/annotations/结果规范化/重连重试一次/定义缓存（`omp/mcp/tool-bridge.ts:47-81,278-336,464-538,711-841`） | 前缀 + 热替换；**无 readOnlyHint、无结果预算、无重试**（`za/mcp/manager.py:158-225`） | **P2**（readOnlyHint 归入 P0 分类修复更划算） |
| 13 | **run 级工具统计** | `AgentRunCollector`：by-name/status/latency、available/invoked/unused（`core/run-collector.ts:1-150`） | 只有 `tool_calls` 计数（`za/telemetry.py:28,49-56`） | **P2**（evals 有用，很便宜） |
| 14 | **明确不抄** | `xd://` 设备协议、BM25 lazy discovery（omp 18.14.0 已删除该设置且无替代，`omp/config/settings.ts:3243-3259`）、speculation（默认关、只支持本地读、900 行）、`openArgStream` 参数增量流、live steering、JTD/typebox schema 系统、工具内自动重试、跨会话 SQLite 审批缓存 | — | — | **N** |

**两个已确认的现有缺陷（不是"缺功能"，是"行为不对"）**，建议在 P0/P1 一并修：

1. **`sequential` 与 `parallel` 同时跑**（`za/agent/loop.py:322-331`）：注释写"两轨并发"，所以标了 `execution_mode="sequential"` 的工具仍会与 parallel 工具同时执行，不等价 omp 的 `exclusive`。当前 9 个工具都没标 sequential，所以还没暴露；一旦加 todo/写类工具就会。
2. **`terminate` 判定是 `all()`**（`za/agent/loop.py:521-523`）：批内只有一个工具要求结束、其它没有时，run 不会结束。建议改 `any()` + 其余标 skipped（或明确文档化）。

---

## 6. v0.4 建议路线

### 6.1 主题与"完成定义"

> **v0.4 主题：把"能跑的工具"提升为"可续读、可恢复、可授权"的工具内核；新工具只加 `todo` 一个。**

完成定义（可验收）：
- 读大文件能 `offset/limit` 续读，截断提示给出**下一步坐标**；
- 模型不可能再用"截断的 read 结果"覆盖写回原文件；
- `web_search`/`fetch` 在 plan 模式与 headless ask 下按"只读"放行（与文档一致）；
- 所有工具（含 MCP/扩展）统一结果预算 + 标准截断 notice；MCP 大结果不再无上限；
- TUI 能看到 bash 长命令的运行中输出（1s 节流）；
- `todo` 工具可用、状态跨 resume 保留、TUI 有 HUD 提示条。

### 6.2 P0（v0.4 必做，6 项 ≈ 350–450 行 + 测试）

| # | 改动 | 位置 | 规模 | 验收 |
|---|---|---|---|---|
| P0-1 | **权限元数据 + 只读分类修复**：`AgentTool.approval` 最小版（`read|write|exec` 或函数）；`web_search`/`fetch` 改只读；MCP 读 `annotations.readOnlyHint`，无标注保守按 write；同步修 `docs/guide/tools-permissions.md` | `za/agent/loop.py`、`za/app/permissions.py`、`za/tools/web/tools.py`、`za/mcp/manager.py` | ~80–120 行 | plan/ask/--read-only 下 web/fetch 放行；bash/write 仍拦；MCP 只读工具免问 |
| P0-2 | **统一结果预算 + notice**：`post_process_result(tool, result)` 在 loop 里统一做"字节阈值 + head/tail + 列宽 cap + 标准 notice"；各工具硬编码降级为兜底；MCP/扩展自动获得 | `za/agent/loop.py` + 新 helper（如 `za/tools/budget.py`） | ~100 行 | 超阈值输出有统一 `[truncated: …]`；工具内旧截断不双重触发；image block 保留 |
| P0-3 | **read_file 分页**：`offset?`/`limit?`；输出带行号（或至少续读提示带 next offset）；截断提示改为可执行 | `za/tools/fs.py` | ~60 行 | 首页/续页/越界/超限/二进制/abort 6 条测试 |
| P0-4 | **write_file 截断回写守卫**：content 以 read 截断提示结尾且比现文件短 → `is_error` 拒绝并提示重读 | `za/tools/fs.py` | ~25 行 | 拒绝/放行/新文件/CRLF 变体 4 条测试 |
| P0-5 | **bash 非交互 env + cwd**：合并 ~12 项关键 env（PAGER/TERM/CI/GIT_TERMINAL_PROMPT/npm_*/PIP_NO_INPUT…，用户 env 优先）；`cwd?` 参数 | `za/tools/bash.py` | ~30 行 | git log/man 不挂；用户 env 不被覆盖；相对 cwd 正确 |
| P0-6 | **partial result 接前端**：TUI 工具卡/状态带接一条 1s 节流的运行中输出行；REPL/headless 至少消费或安全忽略；`tool_progress_line` 从死代码复活 | `za/app/chat.py`、`za/cli/tui/widgets/tool_card.py`、`za/cli/render.py`、`za/cli/repl.py` | ~80 行 | bash 长命令能看到进度；事件不丢/不乱序；单测覆盖 update 分支 |

> 顺序建议：P0-1（半天）→ P0-3/4/5（一天）→ P0-2（一天，先做 helper + bash/read/fetch 接入）→ P0-6（半天）。P0-2 与其它可并行，但 P0-1 是审批/超时/后续所有声明式改造的前置。

### 6.3 P1（v0.4 第二阶段，9 项 ≈ 600–800 行）

| # | 改动 | 依据 | 规模 |
|---|---|---|---|
| P1-1 | **`todo` 工具 + sidecar + 简化 HUD + 5 条提示词**：9 op、按内容寻址、批量原子性；`<session>.todo.json` 原子替换（复用 `.titles.json` 写法）；HUD 挂 StatusBand 上方；**不做** eager 强制 tool_choice/停止自动续跑 | `10b` §3/§12.4 | ~250 行 + ~120 词 prompt + ~80 行 HUD |
| P1-2 | **grep 分页**：`skip?` + 每文件 20 条 + 一页 20 文件 | `10a` §6 | ~40 行 |
| P1-3 | **审批 v2**：tier + `targets`（路径/命令前缀）+ 会话级 `tier\0path` 缓存 + reason/正文；目录授权只覆盖一层；不做 SQLite | `10e` §4 | ~120 行 |
| P1-4 | **超时表 + 统一取消**：`TOOL_TIMEOUTS` + `clamp_timeout`（bash 已有、fetch 暴露 `timeout_s`）；loop 级 `asyncio.wait_for` 覆盖 fs/search/web/MCP | `10e` §5 | ~80 行 |
| P1-5 | **error_kind + 循环守卫**：结果加 `error_kind`；跨轮 `(name, canonical_args)` ≥5 注入纠正，exempt 纯等待类 | `10e` §7 | ~100 行 |
| P1-6 | **请求级 max_tokens 拟合**：`max(1024, min(cap, window − prompt_est − headroom))` | `10e` §3 | ~20 行 |
| P1-7 | **并发语义修正**：`exclusive` 真互斥（sequential 等并行组完成）；write/edit 标 exclusive | `10e` §1/§6 | ~50 行 |
| P1-8 | **edit replace_all + write/edit CRLF/BOM 保持** | `10a` §3/§4 | ~45 行 |
| P1-9 | **系统提示工具政策三段式**：Specialized / General / Exploration + "何时 todo / 何时并行 / 别猜文件" | `10b` §1 | ~120 词 prompt |

> P1 建议按 PR 切：PR-A 内核治理（P1-3/4/5/6/7）+ PR-B 工具质量（P1-2/8）+ PR-C todo（P1-1）+ PR-D 文案（P1-9）。

### 6.4 P2 / 条件项（v0.4.x / v0.5）

| 项 | 触发条件 | 规模 |
|---|---|---|
| background bash（`async` 参数 + job 表 + 完成注入）+ `wait` | 需要长构建/测试/服务时；这是 wait 的前置 | 独立版本主题 |
| `task` 同步单发子代理（child Agent、只读工具集+独立 ContextGuard、深度 1、结果 envelope、`subagent_cost`） | 需要并行/委派时；先单发，异步/批量/隔离/结构化全部后置 | ~250–400 行 |
| `ask` 结构化提问（复用现有 modal 机制） | plan 模式升级为"写 plan 文件→批准"工作流时升 P1 | ~120 行 + modal |
| LSP 最小版（diagnostics/symbols/references/definition/rename；5–8 server；缺 server 静默提示） | 多文件重构成为主线时；先做一个 server 探针 | 2–4 天 |
| `github` 只读子集（pr view/diff/checks/logs；`gh` 不存在则不注册） | 用户真实使用 PR 流程时 | 1–2 天 |
| bash shell 抽象（`(shell,args)`：POSIX/PowerShell/cmd） | 明确要支持 Windows 时；**不是新增 powershell 工具** | 0.5–1 天 |
| artifact 落盘（超阈值写会话 artifacts 目录，notice 给路径/行号，read 可回读） | 有"长输出需要回读"的真实诉求时 | ~1 天 |
| hidden/default_inactive + 统一 ToolRegistry/reconcile | 工具数 >15 或 TUI 需要热更新工具时 | ~1 天 |
| MCP 增强（readOnlyHint 已在 P0-1；结果预算已在 P0-2；剩重连重试/定义缓存） | MCP 使用变多时 | ~1 天 |
| checkpoint/rewind、run 工具统计、fetch 正文启发式、glob limit/gitignore | 分别评估，均非阻塞 | 各 0.5–1 天 |

### 6.5 明确不做（N）

browser、computer、debug/DAP、ida、security_scan、tts、generate_image、eval 内核（py/js + 工具桥）、memory backend（hindsight/mnemopi/local）、managed skill 自改写（learn/manage_skill）、goal/vibe/loop 模式、`xd://` 设备协议、BM25/lazy tool discovery、speculation、`openArgStream` 参数增量流、live steering、hashline/5 种 edit 模式、find 语义搜索、27 个搜索后端/75 个站点抓取器、跨会话 SQLite 审批缓存、工具内自动重试。

**理由统一**：它们要么是 omp 为 40+ provider / 多 agent / 垂直场景付的成本，要么与 ZenAgent「纯 Python + httpx 单运行时依赖 + 最小内核」的定位正面冲突。`10a` §10.2、`10b` §12.2、`10c` §16.2、`10e` 末节分别给了逐项理由。

### 6.6 与 v0.4 版本号的关系

建议把 P0 作为 **v0.4.0**（主题：工具内核治理），P1 作为 **v0.4.1–v0.4.3**（主题：工具面质量 + todo），P2 条件项进 **v0.5** 路线图。这样每个版本有一个可验收主题，而不是"一次对齐 omp"。

> **发布记录（2026-10-31 更新）**：实际执行把 **P0 + P1 + P2 已实现切片合并为 v0.4.0** 一次发布
> （三者共享同一套工具内核，拆开发版只会产生半成品接口）。本节的 v0.4.x 拆分建议**不再采用**；
> P2 中未实现的项（task/ask/LSP/github/artifact/registry/MCP retry/checkpoint）转入
> [`12-v0.5-plan.md`](12-v0.5-plan.md)。

---

## 7. 直接回答用户的问题

1. **"omp 还有 powershell 等一些工具，我们的差距？"**
   - powershell **不是 omp 的工具**，是 `bash` 的 shell 后端；omp 也没有 `fetch` 工具（是 `read` 的 URL 分支）。omp 名册 30+3，但模型默认顶层只有约 16–18 个，其余按条件/`xd://` 挂载。
   - 差距**不在数量**：真正该补的是 **read/write 的分辨率**、**bash 的执行环境**、**工具内核治理**、**todo/task/LSP 这类"放大模型能力"的工具**——按 §6 的顺序做。
2. **"search 需要配 provider，是不是没了？"**
   - search 一直在，且无 key 也能跑（bing/ddg 兜底）；工具层不检查 key。真正原因是 `READ_ONLY_TOOLS` 漏了 `web_search`/`fetch`，导致 **plan 模式/非交互 ask 模式直接拒**（§3.3 实测）。P0-1 修完即可恢复，同时改文档。
3. **"应该先做什么？"**
   - 先做 §6.2 的 P0（6 项，约 2–3 天量级）：权限分类 → read 分页 → write 防线 → bash env → 结果预算 → partial UI。
   - 然后 §6.3 的 P1，其中 **`todo` 是唯一建议在 v0.4 新增的工具**；`task`/`wait`/`ask`/`LSP`/`github` 全部按条件项后置。
   - 不建议为了"对齐 omp"去补 browser/computer/memory/eval 等，那会破坏当前定位。

---

## 8. 证据与局限

- **证据**：五份附录共 1349 行，全部带 `file:line`；`10d` 包含本机实测（工具清单、权限门、155 条测试）；本文 §3.3/§5 的关键结论 Lead 已本机复现。
- **局限**：
  1. omp 未实际运行（本机 bun 版本不满足），所有 omp 结论是**源码级推断/确认**；
  2. 未在真实网络/provider key 下验证 web_search 成功路径（provider 可达性沿用 `docs/internals/web-search.md` 的既有记录）；
  3. ZenAgent 的 400 溢出区间、并发写风险是**代码推断**，未构造故障实测；
  4. omp 版本钉在 18.14.0，后续版本可能变化；行号以本地安装包为准。
- **本次产出**：只新增 `docs/research/omp-cli-study/10-tools-gap.md` + `10a`–`10e` 五份附录；**未改任何代码/测试/现有文档**。工作树里的 TUI 改动与本调研无关。
