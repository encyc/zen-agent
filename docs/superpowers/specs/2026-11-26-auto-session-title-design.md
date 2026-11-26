# 会话自动命名（auto session title）设计

日期：2026-11-26
状态：待批准

## 意图与为什么

会话在侧栏、TUI 选择器和 `zen --list-sessions` 里的展示名今天只有两种来源：手写的
标题（`.titles.json`），或者没有标题时用**第一条用户消息的首行截 40 字**
（`session.py::brief`）。后者让列表里全是「帮我把这个文件……」这样的半句话：不是名字，
是消息的截断。用户要的是开聊之后自动调一次 LLM，把第一条 prompt 归纳成一个短名
（约 10 个汉字 / 5–6 个英文词），写进已有的标题 sidecar——所有前端读到的就是新名字。

成本是一个极小的 provider 调用（`max_tokens` 128），每个新会话一次。

## 现状（核对过的代码事实）

- `zenagent/agent/session.py`：`TITLES_FILENAME = ".titles.json"`；`set_session_title`
  （空白压缩、上限 `TITLE_LIMIT = 80`、临时文件 + `os.replace` 原子替换、空标题抛
  `ValueError`）；`session_title`（有存储标题用存储的，否则 `_derive_title`）；
  `_title_from_messages` 对 goal 会话取 `<objective>`。
- 展示面：`SessionInfo.title` → `SessionEntry.as_wire()["title"]` → RPC `session list`
  → 桌面 `session.title || session.name`（`SidebarCoding.tsx`、`ConversationTopbar.tsx`）；
  TUI 选择器与 `/rename` 走同一个 `SessionManager.rename`。
- 装配点：`bootstrap.prepare`——REPL、TUI、headless、RPC/桌面的**唯一** app 装配点
  （它内部再调 `Runtime.bootstrap`）；`agent.subscribe` 的 listener 在 loop 里**串行
  await**，所以 LLM 调用必须 `create_task` 出去，否则会挡住宅的流式输出。挂在
  `prepare` 而不是 `Runtime.bootstrap`，是因为测试与嵌入方会直接构造 Runtime，产品默认
  行为不该改变它们的 provider 调用序列。
- 先例：`app/compact.py` 是第二个调用 provider 的地方——复用 `agent.model` /
  `agent.api_key`，`StreamOptions(thinking=None, session_id=agent.session_id)`。
  session id 必须带：网关按它路由（opencode-go），丢了会 400。
- 用户消息在 `message_end` 之前已经进入 `agent.messages`（`loop.py:166`），所以
  listener 里能数出"这是不是第一条"。

## 目标 / 非目标

目标：

- 新会话的第一条用户消息之后，后台一次调用生成展示名，写入 `.titles.json`。
- 所有前端零改动（它们已经在读 `title`）。
- 不阻塞首包；失败静默、不重试；`/rename` 永远优先。
- 与已有标题机制共用 sidecar，不新增会话文件格式。

非目标：

- 不回填已有会话（"第一次输入的 prompt"只对新会话成立）。
- 不给 subagent 的 task 目录命名。
- 不新增 `config.toml` 开关（用户要求"每一个 session"）。
- 不新增 rename 事件帧（见"已知取舍"）。

## 方案

A. **app 层 `AutoTitler`**，`bootstrap.prepare` 订阅第一个用户消息 → 后台任务 →
   写 sidecar。**选它。** 一处覆盖 `zen -p`、REPL、TUI、RPC/桌面。
B. 前端层（`ChatController` 或桌面 RPC `session rename`）。✗ headless 没有
   `ChatController`，TUI/RPC/桌面要各写一遍；而且改名发生在 UI 层会让 kernel 的
   `SessionInfo` 与磁盘语义分叉。
C. 纯启发式（首行截 10 字）。✗ 用户要"归纳"，截断只会得到半个句子。

## 设计

新模块 `zenagent/app/title.py`：

| 符号 | 职责 |
|---|---|
| `AUTO_TITLE_LIMIT = 60` | 安全上限（保护 UI），**不是目标长度**；目标由提示词表达 |
| `compose_title(prompt)` | 返回 `(system, user)`，读新 block `title_system` / `title_user` |
| `clean_title(raw)` | 去代码围栏、去包裹引号、去「会话名：/Title:」前缀、取首行、压空白、去尾随标点、截 60，空串表示放弃 |
| `AutoTitler` | 事件订阅者 + 后台任务；`async finish()` 供 `Runtime.close()` 收尾 |

流程：

1. `bootstrap.prepare` 在 Runtime 建好后实例化 `AutoTitler(runtime)` 并
   `agent.subscribe(autotitler)`，存到 `runtime.auto_titler`。
2. `message_end` 且消息是 `UserMessage`，且满足全部条件才触发：
   该 session 还没跑过、该 session 没有存储标题、`agent.messages` 里正好一条用户
   消息、文本非空（纯图片跳过）。
3. `create_task` 里：goal 消息取 `<objective>` 文本（把 `session.py` 的私有
   `_goal_objective` 公开成 `goal_objective`），其余取正文；截 2000 字进提示词；
   `safe_stream(agent.stream_fn or stream_fn_for(agent.model), agent.model, request, [],
   StreamOptions(api_key=..., thinking=None, max_tokens=128, session_id=...))`；
   15s 超时；只取 `TextContent`。
4. `clean_title` 非空 → **写前复核**该路径仍无存储标题 → `set_session_title(path, name)`。
   写入用触发时捕获的路径，不是 `runtime.session.path`（任务跑完前用户可能切了会话）。
5. 任何异常/超时/空结果：不写、不提示、不重试。
6. `Runtime.close()` 调 `titler.finish()`：等在飞任务（默认 2s），超时取消——进程
   退出不因一次标题请求卡住。

提示词（两个新 block）：

- `title_system.md`：把会话名当产品文案——名词短语、用户语言、约 10 个汉字或 5–6 个
  英文单词、不加引号/句号/解释，只输出名字。
- `title_user.md`：用户第一条消息 + "只输出会话名"。

`zenagent/prompts/spec.py` 的 `BLOCKS` 加两条，`registry.validate_registry()` 会校验
模板变量；`compose_title` 一并导出到 `zenagent/prompts/__init__.py`。

并发与优先级：

- 读-改-写全程同步（没有 await），同一事件循环里的其他 slot 不会插进中间；跨进程的
  竞争与今天 `/rename` 相同，不扩大。
- `/rename` 与自动命名的竞争窗口只有一次 provider 往返；写前复核后到 `os.replace`
  之间是同步代码，窗口可以忽略。

## 已知取舍

- 桌面侧栏在 `turn_end` / `agent_end` 刷新会话列表。标题通常在那一轮结束前就写好；
  极快的一轮可能晚一拍，下一次刷新（再发消息、切会话、切页）补上。为此新增一条 wire
  帧不值当。
- 这次调用不进 usage 行（compaction 的摘要调用同样不计）；它是一次真实但极小的开销。
- `zen -p` 也会多这一次调用——它就是一个会话。若以后要跳过 headless，加一个
  `RuntimeOptions` 字段即可，现在不加。

## 测试

- `tests/test_title.py`
  - `clean_title` 表驱动：围栏、引号、前缀、多行、尾标点、超长、空白。
  - `compose_title` 的模板变量渲染。
  - `AutoTitler` + `text_stream` 假 provider：写入 sidecar；已有标题不覆盖；恢复的
    会话不触发；provider 报错静默；同一会话只跑一次；goal 消息用 objective。
- 现有 prompt registry 校验自动覆盖新 block。
- `docs/guide/sessions.md` 的标题小节补一段（自动命名 + 失败回退到首行 brief）。

## 留给实现者

- 两个 block 的文案措辞（长度、语气）。
- 超时值（默认 15s）与 `max_tokens`（默认 128）。
- 是否给 `AutoTitler` 加 metrics/trace 记录（默认不加）。
