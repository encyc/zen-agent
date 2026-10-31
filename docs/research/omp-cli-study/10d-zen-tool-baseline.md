# 10d · ZenAgent v0.3.0 工具基线（工具面 + 工具内核）

> **基线**：tag `v0.3.0`，HEAD `2aab733`（2026-10-30）。工作树有 2 处未提交改动（`zenagent/cli/tui/widgets/rows.py`、`tests/test_tui_rows.py`），属 TUI 行渲染、与工具层无关，**不计入本报告**。
> **证据约定**：`[码]` = 源码事实，附 `file:line`；`[测]` = 本机实测（`.venv/bin/python` / pytest），附命令与关键输出；`[推]` = 由代码结构推导的判断，不冒充事实。所有“缺失”都做过全仓 grep，不凭印象。
> **复核命令**：`python -c "from zenagent.tools import ALL_TOOLS; print([t.name for t in ALL_TOOLS])"`；`pytest tests/test_tools_fs.py tests/test_tools_bash.py tests/test_tools_search.py tests/test_tools_web.py tests/test_tools_web_query.py tests/test_validate.py tests/test_permissions.py tests/test_modes.py tests/test_app_permissions.py -q` → **155 passed**（2026-10-31）。`[测]`
> **范围说明**：本文只盘工具面 + 工具内核；TUI 视觉细节另见 04/07。

## 0. 结论速览

用户原判断：**“最基础 tool 只有 bash/write/edit/grep，加一个需要配置 provider 的 search 没了。”**

| 说法 | 判定 | 依据 |
|---|---|---|
| “只有 bash/write/edit/grep” | **不准确** | 默认暴露 **9 个**内置工具：`read_file`、`write_file`、`edit_file`、`list_dir`、`bash`、`glob`、`grep`、`web_search`、`fetch`。`[码]` `zenagent/tools/__init__.py:27-37`；`[测]` 实测输出 `['read_file','write_file','edit_file','list_dir','bash','glob','grep','web_search','fetch']`。另有条件注册的 `read_skill`、MCP 工具、扩展工具（§2）。 |
| “search 没了” | **不准确** | `web_search` 无条件在 `ALL_TOOLS` 里，运行时直接暴露（`zenagent/tools/__init__.py:35`，`zenagent/app/runtime.py:179`）；无 key 也能调用。 |
| “需要配置 provider” | **部分准确，但因果说反了** | 工具层**不检查任何 key**（`zenagent/tools/web/tools.py:65-84`）；key 只在 provider 内部检查（`providers.py:107-109`、`145-147`、`189-192`）。无 key 时链仍会试 **keyless 的 Bing / DuckDuckGo**（`registry.py:30`、`providers.py:333-346`）。真正让它“像没了”的原因通常是：① 无网络/引擎被墙 → 返回 `is_error` 并附带 5 家尝试原因；② **plan/ask 模式把 `web_search`/`fetch` 当“非只读工具”拦掉**（见 §3、§5-P0），这很可能就是用户体感“search 没了”的直接来源 `[推]`。 |
| 真正的差距 | **成立** | 差距不在“工具数量”，而在**工具内核成熟度**：无 per-tool 超时/取消框架（除 bash 自带）、partial result 无 UI 消费、`web/fetch/MCP` 只读分类缺失、写类工具全部并发、无 lazy tool 加载、审批按工具名缓存等（§5，15 项逐条给证据）。 |

一句话：**工具面比用户以为的宽（9+3 类），但工具内核确实薄；最该先修的是权限分类、partial 流式、工具级超时/取消三件事。**

## 1. 默认工具面（9 个，顺序即 prompt 顺序）

`ALL_TOOLS` 定义 `zenagent/tools/__init__.py:27-37`；`Runtime.bootstrap` 直接 `tools = list(ALL_TOOLS)`（`zenagent/app/runtime.py:179`），headless/REPL 同（`cli/main.py:253`、`cli/repl.py:518`）。模型看到的是 `ToolDef(name, description, parameters)` 全量列表（`agent/loop.py:401`），system prompt 里再以名字清单重复一遍（`app/support.py:37-45`，第 42 行 `Available tools: {tools}`）。

| 工具 | 定义 | 参数（`*`=required） | 权限门分类 | 关键限制 / 行为 | 测试 |
|---|---|---|---|---|---|
| `read_file` | `fs.py:237-244` | `path*` | 只读（`permissions.py:32`） | 2000 行截断并带提示 `fs.py:32,130-137`；二进制/非 UTF-8 拒绝 `118-127`；**无 offset/limit、输出无行号**（schema 只有 `path`，`fs.py:237-244`；截断提示却说 “read a smaller range”，`134-136`，参数并不支持） | `test_tools_fs.py:33-100` |
| `write_file` | `fs.py:246-256` | `path*`、`content*` | 写（需确认） | 覆盖写；自动建父目录 `148`；返回 ≤200 行 unified diff `41,152-155` | `test_tools_fs.py:201-240` |
| `edit_file` | `fs.py:258-272` | `path*`、`old_string*`、`new_string*` | 写（需确认） | 必须唯一匹配；0/多匹配都拒绝且不写 `174-188`；空 old 拒绝 `164-165`；diff ≤200 行 `195-197` | `test_tools_fs.py:103-148` |
| `list_dir` | `fs.py:274-280` | `path*` | 只读 | 最多 500 条 `35,223-233`；目录在前、带大小 `214,224-231` | `test_tools_fs.py:150-176` |
| `bash` | `bash.py:206-224` | `command*`、`timeout_s?` | 写/执行（需确认；灾难命令策略层直接拒，`permissions.py:149-154`） | 默认 120s `107`；30,000B 头+尾截断 `31-34,56-87`；stderr 合并 `114`；`start_new_session` + `killpg` 杀进程组 `90-100,117`；**非零退出不是 `is_error`**（`8-9,183-203`）；`on_update` 流式 `127-141` | `test_tools_bash.py:17-118` |
| `glob` | `search.py:161-176` | `pattern*`、`path?` | 只读 | 最多 500 条 `24,52-56`；`Path.rglob`，**不读 .gitignore** `43-47`；目录不存在报错 `40-41` | `test_tools_search.py:27-55` |
| `grep` | `search.py:178-197` | `pattern*`、`path?`、`glob?` | 只读 | 最多 200 条 `26,125-126,155-158`；优先 `rg --json`，缺失/启动失败退回 Python 扫描 `81-131,145-150`；跳过二进制 `70-71` | `test_tools_search.py:57-100` |
| `web_search` | `web/tools.py:111-143` | `query*`、`limit?`、`recency?` | **不在只读集合**（见 §3/§5） | 最多 8 条 `28,76`；provider 链 + 60s 缓存（`registry.py:30-33,93-112,114-146`）；失败原因逐家回给模型 `registry.py:81-84`、`tools.py:43-45` | `test_tools_web.py:51-118` |
| `fetch` | `web/tools.py:145-157` | `url*` | **不在只读集合** | 12,000 字符 `29,104-106`；HTML 剥标签 `87-88,103`；只接受 http(s) `93-94`；`follow_redirects=True` 且无内网/SSRF 防护（`web/net.py:23-31`）`[推]` | `test_tools_web.py:121-154` |

两个整体事实：① 全部 9 个工具都是 `execution_mode` 默认值 `"parallel"`（`agent/loop.py:96`；构造处 `fs.py:81-97`、`bash.py:206-224`、`search.py:161-197`、`web/tools.py:111-157` 均未覆盖）；② `ALL_TOOLS` 本身**没有任何测试锁定**（`grep ALL_TOOLS tests` 无命中），所以文档写“7 个内置工具”也没被发现（§6 文档漂移）。

## 2. 条件与动态工具

**2.1 `read_skill`（条件注册）**
- 仅当发现到 skill 才注册：`Runtime.bootstrap` 中 `if runtime.skills: tools.append(build_skill_tool(...))`（`app/runtime.py:186-193`）；headless `cli/main.py:281-288`、REPL `cli/repl.py:557-565` 同逻辑。发现根：项目根 `.zenagent/skills` + `~/.zenagent/skills`（`skills.py:168-177`）。
- 工具本体 `skills.py:104-147`：按 name 精确查表，未知名字报错并列出可用名 `112-120`；body 上限 32,768B `41,122-127`；prompt 索引上限 4,096B `38,150-165`。
- 权限分类里被算作只读（`permissions.py:32`），因此 plan 模式可用。

**2.2 MCP 工具（条件注册）**
- 配置文件默认 `~/.zenagent/mcp.json`（叠加项目 `.zenagent/mcp.json`，`mcp/config.py:46` 及文件头 `1-12`）；`--mcp-file`/`--mcp-server` 可覆盖（`cli/main.py:508-515`）。
- 启动后 `tools.extend(runtime.mcp.tool_defs())`（`app/runtime.py:180-183`），命名 `mcp__<server>__<tool>`（`mcp/manager.py:31-33`），JSON schema 原样透传 `mcp/manager.py:182`。
- 调用经 `McpClient.call_tool`，有 **30s 传输层超时**（`mcp/manager.py:62,195`；`mcp/client.py:404-411`）；结果映射支持 text/image/resource（`mcp/manager.py:198-223`），**结果体量不设上限**。
- 启动失败不致命，进 warnings；`retry_failed` 与 `refresh_tools` 可后补（`mcp/manager.py:92-127`；`runtime.py:334-343`）。
- **注意**：MCP 工具一律不在 `READ_ONLY_TOOLS`，所以在 plan 模式全被拒、在 ask 模式全部需确认（`permissions.py:158-162,167-188`），即使对端是只读查询工具——分类粒度只有“工具名”，没有 per-tool 只读元数据。

**2.3 扩展工具（opt-in）**
- 来源 = 用户配置 `extensions` + `--extension`（`runtime.py:201-211`）；扩展模块 `register(api)` 里 `api.register_tool(...)`（`extensions.py:43-68,91-130`）；坏扩展只告警跳过 `114-117`。
- 扩展工具拿到与内置相同的执行契约，但**没有额外 schema 校验/权限元数据**；扩展 hook 只能订阅事件，不能挂 `before/after_tool_call`（`extensions.py:67-68`；见 §4.5）。

**2.4 注册表与内省**
- 没有独立“工具注册表”类，注册表就是 `ALL_TOOLS` 列表 + 运行时拼接（`runtime.py:179-214`）。
- 已有内省命令 `/tools`：`app/commands.py:274-279` → `describe_tools`（`app/support.py:22-35`，只输出 `name: description`）；REPL 同（`cli/repl.py:344-346`）。注意 09 文档里 `/tools ❌` 是旧基线（09 的 HEAD `9606d33` 早于加入 `/tools` 的提交 `439a7cf`），v0.3.0 已有。
- 动态增删：`Agent` 在**每次 run 开始时**快照 `tools=list(self.tools)`（`agent/agent.py:68-70,202-205`），所以只能在两次 run 之间换工具；MCP 刷新会重建工具列表和 prompt（`runtime.py:334-343`），REPL `reload` 只允许空闲时重发现 `cli/repl.py:662-668`。没有 run 内增删、没有模型可调用的 tool_search/延迟加载。

## 3. `web_search` / `fetch` 的真实行为

**3.1 是否可调用与 provider 依赖**
- 注册无条件（`tools/__init__.py:35`），工具入口不做 key 检查（`web/tools.py:65-84`）。
- 默认链 `deepseek → tavily → brave → bing → duckduckgo`（`registry.py:30`）；keyed 三家没 key 会抛 `ProviderError`（`providers.py:107-109,145-147,189-192`），链捕获后继续（`registry.py:128-132`）；**keyless 的 Bing、DuckDuckGo 永远在链里**（`providers.py:223-311`；`available_providers` 对二者返回 `(True, "keyless")`，`providers.py:333-346`）。
- 因此“需要 provider 配置”不成立；**需要的是可用网络**。全部失败时模型看到的是逐家原因（`[测]`，清空三个 API key 并把网络打崩后调用 `WEB_SEARCH.execute`）：

```
web search failed — DeepSeek: DEEPSEEK_API_KEY is not set; Tavily: TAVILY_API_KEY is not set;
Brave: BRAVE_API_KEY is not set; Bing: no route to host; DuckDuckGo: no route to host
```

  `is_error=True`，`details.attempts` 同步携带 5 条（`web/tools.py:77-84`；`ChainResult.failure_summary` `registry.py:81-84`）。对应测试 `tests/test_tools_web.py:108-118`。
- 配置面：`/settings` 的 `search_provider`（`registry.py:36-43`）、`ZENAGENT_SEARCH_PROVIDERS` 覆盖顺序（`registry.py:46-61`）、`ZENAGENT_SEARCH_TIMEOUT` 单请求超时（`web/net.py:14-20`）、`/search [query]` 手动跑（`app/commands.py:319-331`）。
- 结果面：`limit` 被夹到 1–8（`web/tools.py:28,73-76`；provider 层另夹到 20，`providers.py:41,94-95`）；`recency` ∈ day/week/month/year（`tools.py:30,69-71`）；结果去重按规范化 URL（`registry.py:149-159`、`types.py:43-56`）；60s/32 条链级缓存（`registry.py:32-33,93-112`）。

**3.2 真正会“搜不到”的场景（按可能性排序）**
1. **权限层拦截**：`READ_ONLY_TOOLS` 只有 `{read_file, list_dir, glob, grep, read_skill}`（`permissions.py:32`），不含 `web_search`/`fetch`。`[测]`：

```
mode=plan  web_search allowed=False  "blocked by plan mode (read-only)"
mode=plan  fetch      allowed=False  同上
mode=ask   web_search allowed=False  "permission denied ... non-interactive run without --yes"
mode=full  web_search allowed=True
```

   即：plan 模式**不能联网调研**；headless ask 模式（默认）直接拒绝；交互 ask 模式每次都要确认。这与文档冲突（`docs/guide/tools-permissions.md:14-15` 称二者“只读，免确认”；`:65` 称 `--read-only` 下放行），属代码/文档不一致（§5-P0、§6 文档漂移）。
2. 网络不可达 / DuckDuckGo 被墙（`providers.py:273` 注释；`docs/internals/web-search.md` 的 provider 可达性表）→ 返回上面的错误文本。
3. `fetch` 只接受 `http(s)://`（`web/tools.py:93-94`），非 http 直接 `is_error`；页面超 12k 字符截断并带 `… (truncated)`（`104-106`）。

## 4. 工具内核机制

**4.1 契约**
- `ToolResultAgent`：`content`（唯一给模型）、`details`（只给 UI/日志，明确不发给模型）、`is_error`、`terminate`（`agent/loop.py:68-79`，`details` 的注释在 `72-73`）。
- `AgentTool`：`name/description/parameters(JSON Schema)/execution_mode("parallel"|"sequential")/execute(tool_call_id, args, abort, on_update)`（`agent/loop.py:82-105`）；`execute=None` 只会在调用时降级为错误结果（`97-105,244-245`）。
- 不变量：工具**不应抛异常**；抛了会被 loop 兜住转 `is_error`（`loop.py:24-25,254-259`），但这是兜底不是契约允许。

**4.2 参数校验（故意窄的子集）**
- 只查：顶层 object、`required` 存在、已声明顶层 `type` 的标量/容器；拒绝 bool 冒充 number/integer（`validate.py:48-87`，设计说明 `10-18`，bool 特判 `83-84`）。
- **不做**：递归、anyOf/oneOf、format、default 填充、额外字段拒绝（`validate.py:10-18,73-76`）。
- 顺序：找工具 → 参数 JSON 错误（`ToolCall.arguments_error`，`types.py:99-110`）→ 非 dict → schema 校验 → `before_tool_call` → 执行（`loop.py:227-243`）。即权限 hook 与工具只看到校验过的输入。

**4.3 调度与结果顺序**
- `_run_tool_batch` 把调用分两轨：默认 parallel 的 `asyncio.gather` 并发；`execution_mode=="sequential"` 的按 request 顺序串行；两轨再并发（`loop.py:278-332`，列表 `292-303`，并发 `322-331`）。
- 结果按 assistant 源顺序写回，不按完成顺序（`loop.py:291,320,332,499-519`；测试 `tests/test_loop.py:495-539`、串行 `541-570`）。
- **没有任何内置工具声明 `sequential`**（全仓 `execution_mode=` 只出现在 loop 自身：`loop.py:96`）。所以同一轮里两个 `edit_file`/`write_file` 会并发读写同一文件、两条 `bash` 也会并发——内核提供了机制但工具面没使用 `[码]`。

**4.4 partial result（`on_update`）——机制在，UI 没接**
- 链路：工具调用 `on_update(partial)` → `_execute_tool` 的 `forward`（工具 settled 后丢弃，`loop.py:249-259`）→ `_run_tool_batch.run_one` 包成 `tool_execution_update` 事件（`loop.py:305-320`）→ `agent_loop` 经 queue 逐个 yield（`470-493`）。
- 事件字段 `partial_result`（`events.py:60-76`）。
- 内置工具里**只有 `bash` 真的发**（`bash.py:127-141,181`）；fs/search/web/skill/MCP 的 execute 都接收但不用 `on_update`（`fs.py:105-108`、`search.py:37`、`web/tools.py:65,91`、`skills.py:109`、`mcp/manager.py:171`）。
- **消费端为空**：只有测试断言（`tests/test_loop.py:780-818`）；`ChatController._on_event` 只处理 start/end，无 update 分支（`app/chat.py:320-352`，`else: return` 在 `352`）；REPL `_EventPrinter` 同（`cli/repl.py:367-413`，仅 `399` end）；headless `_PlainOutput` 同（`cli/main.py:118-147`）；渲染层甚至有现成的 `tool_progress_line`（`cli/render.py:131-140`）但**生产代码零调用**（只有 `tests/test_render.py:124-128`）。结论：bash 的长命令在 UI 上只有 “running”，看不到进度。

**4.5 hooks**
- `before_tool_call(tool_call, args)` 可返回 `{"block": True, "reason"}`；`after_tool_call(tool_call, result)` 可整体替换结果或 patch `content/is_error/terminate`（契约 `loop.py:112-125`，实现 `240-243,262-273`）。
- 生产装配只接了 `before_tool_call`（`app/runtime.py:233`；`cli/main.py`/`cli/repl.py` 构造 Agent 时同样只传 before）；`after_tool_call` 全仓只在 loop/agent 自身出现，**运行时从未接线**。
- 扩展 hook 只订阅 AgentEvent 字符串（`extensions.py:67-68`；`runtime.py:240-250`），拿不到工具调用前后的拦截/改写能力。

**4.6 错误降级与终止**
- 未知工具、参数 JSON 坏、参数非对象、schema 不符、hook 拦截、execute 异常——全部变 `is_error` 结果，loop 继续（`loop.py:227-259`）；异常文本截 500 字符（`loop.py:64-65,256-257`）。
- `stop_reason=="length"` 且带 tool calls：**一律不执行**，每个 call 生成 is_error 让模型重发；连续 2 次后结束（`loop.py:447-460,524-525`）。
- 全部结果 `terminate=True` 结束 run（`loop.py:521-523`）；`agent_end` 在 `finally` 必发（`534-536`）；turn/cost 上限 `375-384`。注意：**没有内置工具设置 `terminate`**（全仓 grep 仅 MCP client 进程 terminate）。

**4.7 取消传播**
- `AbortToken` 是协作式事件（`events.py:85-109`），loop 每轮开头检查（`loop.py:371-374`），并把它传给 `execute`（`255`）。
- **只有 `bash` 消费它**：wait abort → killpg → 返回已收到的输出（`bash.py:151-165,181-194`；测试 `tests/test_tools_bash.py:53-72`）。fs/search/web/skill/MCP 都忽略 `abort`——一个卡住的 `grep`/`fetch`/MCP 调用不会被取消，也没有 kernel 级 `asyncio.wait_for` 包住（`loop.py` 无超时结构）。`[码]`

**4.8 输出上限（各工具各管各的）**

| 工具 | 上限 | 越界提示 | 证据 |
|---|---|---|---|
| bash | 30,000B（头+尾） | 文本内 `... [truncated N bytes] ...` | `bash.py:31-34,56-87` |
| read_file | 2,000 行 | 文本内提示 + `details.truncated` | `fs.py:32,130-137` |
| list_dir | 500 项 | 文本内 `... N more entries` | `fs.py:35,232-233` |
| edit/write diff | 200 行 | 文本内 `... [N more diff lines]` | `fs.py:41,58-61` |
| glob | 500 条 | 文本内提示 | `search.py:24,52-56` |
| grep | 200 条 | 文本内 `... stopped at 200` | `search.py:26,155-158` |
| web_search | 8 条结果 / 摘要 400 字符 | 无（截断静默） | `web/tools.py:28,76`；`providers.py:40,67` |
| fetch | 12,000 字符 | 文本内 `… (truncated)` + `details.truncated` | `web/tools.py:29,104-108` |
| read_skill | 32,768B | 文本内 `[... skill truncated ...]` | `skills.py:41,122-127` |
| MCP 工具 | **无** | 无 | `mcp/manager.py:198-223` |

kernel 层没有统一的“结果预算/截断标记”字段，`truncated` 只散落在个别工具的 `details` 里（`bash.py:201`、`fs.py:137`、`web/tools.py:108`）；也没有按批次的 tool-result token 预算。仅有的上下文保护是 `ContextGuard` → `naive_overflow_prune`：超窗口时**整轮丢弃最老 turn**（`config.py:321-347,350-363`），不是对单个 tool result 做裁剪。

**4.9 TUI ToolCard 的数据面（brief，视觉细节见 04/07）**
- 数据模型 `ToolCallView`：`call_id/name/args_preview/args/details/status/result_text/is_error/duration_ms/diff`（`app/views.py:45-59`）；`details` 明确定义为“仅 UI、不入库、不发模型”（`views.py:46-55`），历史回放为空。
- 填充：live 路径在 `tool_execution_start` 建卡（`app/chat.py:334-343`），在 `tool_execution_end` 填 `result_text/details/duration/status/is_error`（`app/chat.py:344-351`）；从历史重建时只恢复 args/result_text/status，`details` 缺失（`app/views.py:152-167`），卡片退化为通用渲染（`tool_card.py:116-121`）。
- 渲染：单行头 = 状态点 + 工具名 + 家族详情 + 状态 + 耗时（`tool_card.py:97-114`）；展开只显示结果**尾部** 12 行（`25,175-182`）；per-family 分支 `_family_detail` 按 name 硬编码 `bash/read_file/edit_file/write_file/grep/glob`（`116-165`），未知工具退回 `args_preview`（`165`）；diff 正文分支依赖 `view.diff`（`183-193`），但 live/history 都不赋值（§5-17）。
- 刷新：`transcript.sync` 检测 status 变化后调 `_update_tool` → `card.refresh_view()`（`cli/tui/widgets/transcript.py:181-190,244-249`；`tool_card.py:75-76`）。
- **未来 per-tool renderer 的 hook 点**只有两处：`_family_detail`（头部一行）与 `_renderable`（正文）；没有 renderer 注册表，新增家族必须改 `tool_card.py`；`details` 是唯一开放的扩展通道（自由 dict，无 schema/版本约定）。omp 的 60 个 renderer 家族对照见 04 文档。

## 5. 现状缺口清单（对照成熟工具内核的最小 checklist）

| # | 能力 | 现状（证据） | 判断 / 建议 |
|---|---|---|---|
| 1 | 只读工具分类（per-tool 元数据） | 硬编码名字集合，缺 `web_search`/`fetch`，MCP 无标注（`permissions.py:32,158-162`）；plan 不能联网、ask 对 web 也要确认（`[测]` 见 §3.2） | **P0**：`AgentTool` 加 `read_only`/`category` 元数据，gate 读取它；修正 docs |
| 2 | partial result 到达 UI | 事件与 bash 发射都在（`loop.py:305-320`、`bash.py:127-141`），但四个前端全不消费（`app/chat.py:320-352`、`cli/repl.py:367-413`、`cli/main.py:118-147`）；`tool_progress_line` 是死代码（`cli/render.py:131-140`） | **P0**：接一个 update 分支 + 1s 节流的“运行中输出”行/卡片 |
| 3 | per-tool 超时 / 取消框架 | 仅 bash 自带 `timeout_s`（`bash.py:107`）；MCP 有 30s 传输超时（`manager.py:62,195`）；web 靠环境变量 20s（`web/net.py:14-20`）；fs/search 无界；loop 无 `wait_for`，abort 仅 bash 响应（§4.7） | **P0**：`AgentTool.timeout_s` + loop 级 `asyncio.wait_for` + 统一取消（含 fs/search/web/MCP） |
| 4 | 写类工具串行化 | `execution_mode` 机制存在（`loop.py:96,292-303`）但 9 个内置全部 parallel（§1 末尾），同轮两个写/两条 bash 会并发 | **P0**：写/执行类默认 `sequential`（或按路径加锁） |
| 5 | `read_file` 范围读取 | schema 只有 `path`（`fs.py:237-244`）；截断提示却建议 “read a smaller range”（`134-136`）；文档称“带行号”（`docs/guide/tools-permissions.md:7`）但输出是原文（`fs.py:129-138`） | **P1**：加 `offset/limit`，输出带行号（或改文档） |
| 6 | 审批缓存粒度 | “always allow” 存的是**工具名**集合（`permissions.py:129-130,198-199`；broker `230,252-253`）→ 对 `bash` 一次 always = 整个会话任意命令 | **P1**：按 `(tool, 目标 pattern)` 缓存（命令/路径/主机） |
| 7 | 统一结果预算 + 截断通知 | 各工具自管（§4.8），MCP 无上限（`mcp/manager.py:198-223`），无 kernel 字段 | **P1**：`ToolResultAgent` 增加 `truncated` 语义 + 批次预算/通知 |
| 8 | lazy / discoverable 工具 | 每轮把全部工具 schema 发给模型（`loop.py:401,416`）；无 tool search、无 defer-load、无按需披露 | **P1**（工具 >20 个时再值）：先做 `/tools` 已有，不急着上惰性加载 |
| 9 | 动态工具增删 | 只能在 run 之间换（`agent/agent.py:68-70,202-205`）；MCP 刷新会重建（`runtime.py:334-343`）；run 内不可变；无 enable/disable | **P2**：先补 run 前 enable/disable + 每 run 快照告警 |
| 10 | 工具级重试 | 只有 assistant 流重试（`loop.py:142,412-419`）；工具执行无重试/无幂等标记 | **P2**：给 `AgentTool` 加 `retryable`/`retries`，只对 read 类 |
| 11 | 结构化工具错误 | 只有 `is_error: bool` + 文本（`loop.py:68-79`）；无 error code / retryable / 结构化 details 约定 | **P1**：`details["error"]={code,retryable}`，先内部用 |
| 12 | 注册表内省 | `/tools` 只输出 name+description（`app/support.py:22-35`）；无 schema 输出、无 JSON、无 RPC/RPC 接口 | **P2**：`/tools --json`（schema + read_only + 输出上限） |
| 13 | `after_tool_call` 接线 | 契约在（`loop.py:112-125,262-273`），运行时从未传（`runtime.py:225-238` 等） | **P2**：接上后可做结果脱敏/预算，扩展可复用 |
| 14 | grep/glob/bash 参数面 | grep 无 context/case/multiline（`search.py:189-193`）；glob 不读 .gitignore（`43-47`）；bash 无 cwd/env/background（`bash.py:213-221`，固定 `os.getcwd()` `116`） | **P2**：按真实需求补；background 与 jobs 一起评估 |
| 15 | fetch 出网安全 | 仅校验 http(s)（`web/tools.py:93-94`）；跟随重定向、无私有网段/元数据地址拦截（`web/net.py:23-31`）`[推]` | **P2**（安全）：默认禁内网/链路本地地址 |
| 16 | subagent / todo / LSP / 后台作业 / ask 工具 | 全仓 grep 无实现；`ALL_TOOLS` 与扩展 API 均无（`tools/__init__.py:27-37`） | **N（本轮）**：确认不存在即可；若 v0.4 含长任务，`todo` 参照 09 文档升 **P1**，但那是新工具面而非内核缺口 |
| 17 | TUI 工具卡 diff 体 | 卡头有 `⟨+a/-b⟩` 统计（`tool_card.py:142-145`），展开区的 `view.diff` 分支（`183-193`）但 `view.diff` 生产路径从未赋值（`app/views.py:59` 默认 None；`app/chat.py:347-351` 只填 `details`）→ 正文不显示 diff | **P2（TUI）**：把 `details["diff"]` 落到 `view.diff`（或直接渲染 details）；REPL 已能显示（`cli/repl.py:399-410`） |
| 18 | 文档漂移 | `docs/feature-map.md:34`、`docs/architecture.md:98` 仍写 7 个内置工具；`docs/guide/tools-permissions.md:7`（行号）、`:14-15`（web 免确认）、`:25`（称无内置工具用 `on_update`，实际 bash 用，`bash.py:127-141`）、`:65`（read-only 放行 web）均与代码不符 | **P2**：把本表 §1/§3 回写文档 |

## 6. 强项与弱项总结

**强项（v0.3.0 已经做对、v0.4 不要回退）**
1. 清晰的零依赖内核契约：`AgentTool`/`ToolResultAgent`（`loop.py:68-105`），工具可单测、可注入（`tests/test_tools_*.py` 共 155 条相关测试通过 `[测]`）。
2. 错误永不炸 loop：未知工具/坏参数/异常/hook 拦截全部降级为模型可见的 `is_error`（`loop.py:227-259`），且有 500 字上限。
3. 并行调度 + 源顺序写回；串行档位已预留（`loop.py:278-332,499-519`），只差给写类工具打标。
4. bash 的进程组治理扎实：默认超时、`start_new_session`+`killpg`、头尾双保留、非零退出不误判 `is_error`（`bash.py:31-34,90-100,107,183-203`）。
5. `details` 与 `content` 分离（模型只见 content），UI 侧已有 per-family 渲染钩子（`tool_card.py:116-165`），`/tools`、MCP 生命周期与重连、skills 按需加载都已可用。
6. 每个工具都有硬上限（§4.8），不会把无界输出灌给模型（MCP 除外）。

**弱项（按 v0.4 优先级）**
1. **权限分类与工具面脱节**（P0-1）：web/fetch/MCP 的只读性没有建模，plan/ask 行为与文档相反，直接造成“search 没了”的体感。
2. **工具内核缺少统一的生命周期治理**（P0-2/3/4）：没有 per-tool 超时、取消只对 bash 有效、partial 流不达 UI、写类并发无保护。
3. **结果面治理是“各工具手写”**（P1-5/6/7/11）：无统一预算/截断语义、无结构化错误、审批只按工具名、无范围读取。
4. **可扩展但不可编排**（P1/P2-8/9/10/12/13）：无 lazy tool、run 内不可增删、无工具级重试、`after_tool_call` 空悬、无 schema 内省。
5. **工具面本身仍小**（N-16）：无 todo/subagent/LSP/后台作业/ask 工具——这是路线选择，不是 v0.3.0 的 bug；若要“对得上 omp 的最小面”，优先 todo 与后台 command，而不是数量对齐。
6. **证据/文档一致性弱**（P2-18）：7 vs 9、行号、web 只读、`/tools` 旧判断（09 基线早于 `439a7cf`）都说明缺少“工具面契约测试”来锁事实。

**局限**：本报告基于源码 + 本地最小实测，未在真实网络/真实 provider key 下跑 web_search 成功路径；provider 端点可用性以 `docs/internals/web-search.md` 的既有记录为准。`[推]` 项（SSRF 风险、并发写风险、用户体感因果）是结构性推断，不是故障复现。
