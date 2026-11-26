# 上下文管理：两层策略

模型窗口有限，而会话会无限增长。ZenAgent 用**两层**处理，一层便宜无损、一层贵而有损：

```
每次请求前（便宜、无损、不改 state）      每轮结束后（贵、有损、写进同一个 session）
ContextGuard.transform            →     maybe_auto_compact
裁剪最老的整轮                          LLM 总结 → 追加摘要 + 边界记录
PRUNE_TRIGGER=0.85                      AUTO_COMPACT_THRESHOLD=0.85
→ 降到 0.70                             用户可 /compact 手动触发
```

两个阈值都是 0.85 是刻意的：同一触发线避免"刚裁完又立刻压缩"的抖动；prune 降到 0.70
留出空间，下一轮的工具结果和输出不会立刻再越线。

## 第一层：请求前裁剪（不改变 state）

`ContextGuard`（`config.py`）实现 `transform_context` 钩子：

```python
async def transform(self, messages):
    return await naive_overflow_prune(messages, self.context_window, self.last_usage)
```

- **估算用量**：优先用上一轮 provider 返回的真实 `usage`（`ContextGuard.on_event` 记录），
  没有则用「字符数 / 4」的启发式（`token_estimate`）。
- 用量 ≤ 窗口 × `PRUNE_TRIGGER`（0.85）→ 原样返回。
- 超了 → 按「整轮」丢弃最老的 user turn，直到估算 ≤ 窗口 × `PRUNE_TARGET`（0.70）。
- **永不丢**：开头的 system 消息、最新一轮。
- **配对安全**：一个 turn 里的 `tool_call` 和 `tool_result` 一起丢，不会产生悬空调用。
- 只影响本次请求；`state.messages` 不变，所以历史完整保留。

为什么是「整轮丢弃」而不是截断大文本：整轮丢弃的语义边界清晰（不会留下半个工具轮），
实现也简单；代价是可能丢得比需要的多，但对 agent 场景够用。

## 第二层：总结压缩（原地、只追加）

`zenagent/app/compact.py::compact`。**会话身份不变**——摘要写进同一个 JSONL 文件，
不新建文件、不切换会话。

### 写了什么

一次压缩向文件尾部追加三样东西，顺序固定：

1. 一条 **`compaction` 边界记录**（`{"type": "compaction", "tokensBefore": N,
   "reason": "manual"|"auto", "kept": K}`）；
2. 一条 **摘要消息**，内容形如 `# 此前会话摘要\n<summary>`（块来自
   `prompts/blocks/compact_result.md`，渲染进用户轮）；
3. **保留的尾部消息**，原样重放一遍。

然后 `agent.replace_messages([摘要, *tail])`——模型下一轮看到的正好是"摘要 + 尾部"。
注意 `state.messages` 里**不含** system 消息：它是 `loop.py` 每次请求时临时拼在最前面的。

### 为什么这样写

文件保持**只追加**的语义（最简单，也最耐崩溃），而**边界感知的重放**
（`app/records.open_session` 传 boundary 谓词）只返回最后一个有效边界之后的消息。
于是同一份文件有两个视角：

| 读者 | 看到 |
| --- | --- |
| 模型 / Agent | 最新边界之后：摘要 + 尾部 |
| `/export`、裸 `Session`、列表 | **全部历史**，边界之前原样保留 |

压缩前的字节永不改写 —— 那是排障时最有价值的证据，也是 `/undo` 能回到压缩前的前提。
"有效边界"的定义是"它后面至少有一条可读消息"：被截断的尾巴绝不会让整个 transcript 消失，
这种情况下重放退回上一段（或整份文件），`segment_start` 保持 `None`。

### keep-recent 尾部与安全切点

不是把所有东西都总结掉。最新一批消息按估算 token 预算原样保留（`KEEP_RECENT_TOKENS` = 20k），
切点（`_choose_cut`）有三条规则：

- 尾部取"从后往前能装进预算的最长一段"，且至少留 1 条给摘要（若开头已是上次的摘要，则留 2 条）；
- **切点绝不落在 tool_call 与它的 result 之间** —— 必要时往回走，宁可超出预算；
- 若下限顶住了、尾部仍持有孤儿 result，则改为缩小尾部（往后挪切点），
  因为孤儿 result 会毒化下一次请求。

手动 `/compact` 用 `manual_keep_recent(messages)` = `min(20k, 总量/4)`：自动压缩只在接近上限时
触发，套用 20k 会把整段都留下、几乎没压缩；手动压缩发生在正常大小的会话上，必须真的变小。

### 摘要会合并，不重建

如果 transcript 的第一条**就是**上次的摘要，这次只总结"摘要之后新增的部分"，然后用
`prompts/blocks/compact_update.md` 把新增内容合并进已有摘要，保持三节结构：
**任务目标** / **已做出的决定** / **未竟事项**（已完成的未竟事项移进"已做出的决定"）。

检测靠"首条消息是否以 `compact_result` 块里 `{{summary}}` 之前的文案开头"，
所以文案改了检测也不会漂。

### 触发与失败

- **自动**：每轮结束后，若上一轮真实 usage（`input + cache_read`）≥ 窗口 × 0.85，
  且不在流式中、transcript 非空。失败只打印一条 notice（`notice.auto_compaction_failed`），
  会话继续。
- **手动**：`/compact`。压缩期间控制器保持 busy，此时到来的 `submit` 会**排队**而不是和
  summarizer 抢同一个 transcript。空 transcript / 无新内容会被拒绝。
- **失败即无副作用**：错误或空摘要 → `RuntimeError`，一个字节都不追加，Agent 也不变。

### 它是一次普通的 provider 调用

summarizer 是**第二个调用 provider 的地方**（第一个是 `agent/loop.py`）。所以它必须带上
loop 同款的字段，否则会出现"正常对话能跑、`/compact` 必挂"的分裂：

- `session_id=agent.session_id` 必须传 —— opencode-go 网关靠 `x-opencode-session` 路由，
  缺了就 400（v0.15.5 修的就是这个）；
- **不带 abort token** —— `AbortToken` 由每次 run 新建（`_Run(abort=AbortToken())`），
  而压缩不在任何 run 里；继承一个已结束 run 的 token 会让"取消上一轮"误杀压缩。
  这条由 `tests/test_compact.py::test_compact_sends_no_abort_token_because_no_run_owns_it` 钉住。

砍掉尾部时会先 `set_session_title` 钉住标题：切完之后第一条消息是摘要，派生标题会塌成它的 header。

## 两个阈值为什么都是 0.85

- prune 与 compact 用同一触发线，避免「刚裁剪完又立刻压缩」的抖动。
- prune 把用量降到 0.70 留出空间，下一轮工具结果 + 输出不会立刻再次越线。

## 常见陷阱

1. **估算不是精确 token 数**：字符/4 对中文偏差大，但只用来触发，宁可保守。
2. **裁剪会让模型「忘记」早期内容**：需要长期记忆的内容应写进 AGENTS.md / skills，而不是指望历史。
3. **压缩后 `usage` 归零**：新上下文要重新积累 usage，所以压缩后第一轮不会再触发压缩。
4. **`/undo` 与压缩交叉**：压缩之后的 `/undo` 只回退压缩后写入的内容；压缩前的内容在同一个文件里，
   但已位于边界之前。
5. **文件变大而不是变小**：压缩**只追加**，`kept` 条尾部会被重写一遍。磁盘占用增长是设计代价，
   换来的是完整审计与"绝不破坏已有历史"。

## 源码对照

- 裁剪：`zenagent/config.py::naive_overflow_prune`、`token_estimate`、`_split_turns`、`PRUNE_TRIGGER/PRUNE_TARGET`
- 钩子：`zenagent/config.py::ContextGuard.transform` + `Agent` 的 `transform_context`
- 压缩：`zenagent/app/compact.py::compact`、`_choose_cut`、`manual_keep_recent`、
  `_previous_summary`、`should_auto_compact`、`maybe_auto_compact`
- 边界与重放：`zenagent/app/records.py::compaction` / `open_session`、
  `zenagent/agent/session.py::Session.load`（`segment_start`）
- 提示词：`zenagent/prompts/blocks/compact_{system,summary,update,result}.md`
- TUI/REPL 入口：`zenagent/app/chat.py::ChatController.compact_now` + `take_notices`
  （分隔线文案 `notice.compacted`）
- 测试：`tests/test_compact.py`、`tests/test_prompts_compact.py`、`tests/test_session.py`、
  `tests/test_app_chat.py`、`tests/test_tui_compact.py`
