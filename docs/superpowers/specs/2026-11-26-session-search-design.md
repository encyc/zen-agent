# 跨会话检索（session search）设计

日期：2026-11-26
状态：待批准

## 意图与为什么

用户在对话里提到"我以前某个 session 里做过 X"，今天 Agent 唯一的办法是让用户
复制粘贴，或者自己猜 `~/.zenagent` 下的路径再撞权限门。目标：Agent 能用**内容线索**
（而不是会话标题）自己找到那段历史，只把相关部分读进当前上下文。

为什么现在做：ZCode 前两天上线了同类能力。它的演进值得抄的是**结论**，不是实现——
他们 2026-11-03 删掉了 `ReadSessionContext`（小模型把另一个会话提炼成
relevant/handoff 两档），理由是"模型自己读就够了"；现在的形态是
`ListSessions`（标题子串 + workspace 过滤 + 分页）配 `ReadSession(sessionId)` 分页读正文，
明确**不预生成 capsule**、**不把整段会话塞进上下文**。本设计接受这个结论，
但补上它的短板：纯标题检索答不了内容线索。

## 论文式前提（本机实测，103 会话 / 35MB / 23059 行）

设计里每个数字都是量出来的，不是估的：

| 层 | 体积 | 全量扫一遍 | "哪个会话碰过 `app/permissions.py`" 命中会话数 |
|---|---|---|---|
| 对话正文（user + assistant text） | 0.59MB | 141ms（带预筛 56ms） | **5** |
| + 工具名与参数 | 5.39MB | 同上 | **16** |
| + toolResult 正文 | 24.19MB | 同上 | **36** |
| （参考）thinking | 7.79MB | — | 不计入任何层 |

三条结论直接塑造设计：

1. **不需要索引。** 全量扫描 56–145ms。`_index.json` 那套 `(size, mtime_ns)` 校验缓存
   是为"打开一次面板读 100 个文件"发明的，这里没有那个场景。薄到零索引。
2. **层要比大小。** 同一句话，只搜对话正文命中 5 个会话，加工具参数命中 16 个。
   差 7 倍的原因是真实信息大量藏在工具调用参数里——"我改过 permissions.py"这话模型
   经常不说，但它 `edit_file` 过。所以要**分层 + 由模型逐级升级**。
3. **thinking 一律不搜不读。** 它是模型的自我对话，既不是"说了什么"也不是"做了什么"，
   占 7.79MB 纯噪音。

## 目标

- Agent 能从内容线索定位到会话，再读其中与当前任务相关的部分。
- 薄：零预生成、零索引文件、零后台任务。
- 分层：默认便宜准确，搜不到再由模型自己升级，而不是一上来就灌 24MB。
- 只读：不产生、不改写、不 fork 任何会话。

## 非目标

- 不生成 capsule / 摘要 / 交接文档（ZCode 删掉的就是这个）。
- 不做 embedding 或语义检索。查询是正则，模型自己组织关键词。
- 不做 `#sess_` 之类的 prompt 内语法，也不在每轮注入"某某会话可用"的提醒。
  入口就是工具描述 + 一条 tool policy 规则。
- 不做写侧：不 fork、不 `send_to_session`、不中断别人。
- 不搜子代理转录（`sessions/tasks/<stem>/<call_id>.jsonl`）与 artifacts。v1 之外。
- 不跨机器。

## 约束（改代码前必须守住的既有契约）

1. **`~/.zenagent` 在权限门里是 `protected`。** 实测
   `classify_read_path(~/.zenagent/projects/<key>/sessions)` → `protected`。
   只要工具带 `path` 参数，第一次调用就会被门挡住（`--allow-protected` 只在非交互
   或显式授权时放行）。**所以三个工具都不接受任何文件路径参数**，数据源在工具内部
   解析。这不是绕过：`bash cat ~/.zenagent/...` 今天在 ask 模式下本来就是放行的
   （`assess()` 只检查 bash 的 catastrophic/destructive，不检查读），新工具在这点
   上不比现状更宽，也不更窄。这个不对称单独记一笔，不在本设计里修。
2. **工具声明 `approval="read"`**，因此 plan 模式与 `--read-only` 下可用，子代理
   也能拿到（`child_allowed`：非 auto 父级只委派 read 工具）。
3. **一个解析器。** 搜和读必须走同一份文本提取逻辑，否则"搜到了第 12 条"和
   "读出来第 12 条"会漂移。
4. **工具结果是不可信输入**，不是指令。摘要文本原样返回，不加"以下是安全的"这类词，
   由既有 system prompt 的不可信内容规则兜底。
5. **prompt 预算**：`TOOL_TOTAL_BUDGET_CHARS = 5200`，现有 14 个工具用掉 5074，
   余量 126。三个新描述约 913 字符，**必须上调到 6200**（沿用 spec.py 里
   "4800 -> 5200" 的注释惯例，在 CHANGELOG 记一笔）。

## 方案

考虑过三条，前两条被否：

（下面的 B 是选中方案，但它的**注册方式**在 builder check 后被改掉了，见"注册与预算"。）

**A. `list_sessions` + `read_session`（ZCode 现状形态）。** 最省。否，理由：用户给的是
内容线索不是标题，而标题只有首条 user 消息前 40 字——实测 100 个会话里 10 个标题是
`[skill:using-superpowers] 我想给Agent一个搜索其…`（skill 前言把真意图挤到 40 字之外）。
只有标题等于让模型猜。

**C. 单入口多 action（像 `memory`）。** 省描述预算。否：三个动作的参数差异很大
（分层/分页语义完全不同），塞进一个 schema 会让模型每一次都要先学词表，而描述预算
本来只需上调一次常量。

**B（选中）. 三个独立工具：`list_sessions` / `search_sessions` / `read_session`。**
照搬 `grep` 的语义到会话层：搜 → 拿 id → 读。方案由用户拍板。

## 三个工具

### `list_sessions`

- 参数：`all`（boolean，默认 false＝当前项目）、`limit`（默认 20）。
- 复用 `agent/session.list_sessions(scope=...)`，它已经做了 `_index.json` 缓存、
  pin/rename 侧信道、空壳会话过滤。不新写列表逻辑。
- 每行：`id`、标题、消息数、更新日期、项目名、`derived_from`（fork/rewrite 来源）、
  pin 标记。
- 明确写在描述里：**列表不往里看**，内容线索要 `search_sessions`。

### `search_sessions`

- 参数：`query`（必填，正则）、`include`、`all`、`skip`、`max_hits`。
- `include` 三档，就是上面测的三层：
  - `conversation`（默认）：user 正文 + assistant 的 text 块。
  - `tools`：再加 assistant 的 toolCall 名与 arguments。
  - `all`：再加 toolResult 的 text 正文。
- **按会话聚合，不按行。** 这是与 `grep` 最重要的区别：`grep` 按文件分页，
  这里按**会话**分页，`skip` 数的是会话数。同一个会话里 `permissions.py` 命中 40 次，
  模型要的不是 40 行，是"这个会话碰过它"。单会话最多展示 `max_hits`（默认 5）条摘录。
- 输出形态：

  ```
  20261031-111222-rewrite  8 hits  (2026-10-31, 279 msgs, rewrite of 20261031-091741)
    #12 user      你说权限门里 ~/.zenagent 是 protected…
    #13 assistant 实测 classify_read_path 返回 protected…
    #14 toolCall   bash: {"command": "uv run python -c ..."}
  ```

- `#N` 是该消息在文件里的**消息序号**（不数旁路记录），可直接传给 `read_session` 的
  `offset`。
- 实现：`q in line` 做预筛（省掉九成的 `json.loads`，141ms → 56ms），命中的行才解析
  并验证。不用 ripgrep：会话目录没有 `.gitignore` 可尊重，而 rg 匹配的是**原始 JSON
  行**——一行 tool 输出可能是 12KB 单行、拿不到上下文，且搜 `arguments`/`toolCall`
  这类词会把每条 assistant 行都匹配上，纯结构性噪音。
- 扫描包在 `asyncio.to_thread` 里（典型 56ms，但 `all` + 大机器可能到秒级），
  每个文件之间检查 abort。
- 无命中时返回**升级提示**而不是空串：
  `no matches in conversation text; the same query matches 3 sessions in tool arguments — retry with include="tools"`。
  这是"初级搜不到就逐渐升级"落地的地方：模型不用猜上哪一档，工具告诉它下一档有没有货。
  为算这个提示，`conversation` 档无命中时会额外扫一遍 `tools` 档（只用于计数，不返回内容）。

### `read_session`

- 参数：`id`（必填）、`offset`（消息序号，默认 0）、`limit`（默认 60 条）、`include`。
- `include` 语义与 `search_sessions` 完全一致（同一个提取器）。
- 页尾报 `next_offset`，模型自己翻。
- 输出每条前带 `#N` 与角色，assistant 的 toolCall 渲染成 `toolCall name: {json}`。
  超过 `limit` 或超预算时按既有 `_result.clip` 规矩截断并说明。

### 会话 id

- 默认就是文件名 stem（`20261031-091741`）。实测本机 105 个文件 105 个不同 stem，
  裸 stem 几乎总能唯一。
- 跨项目重名时，`all=true` 的列表返回 `<project-slug>/<stem>` 限定形式；`read_session`
  两种形式都接受，裸 stem 解析出多个候选项时**报错并列出候选**，不猜。

## 文本提取器（唯一的解析路径）

新模块 `zenagent/agent/session_text.py`：

```python
@dataclass(frozen=True)
class Turn:
    index: int        # 消息序号（只数消息行，旁路记录不占号）
    role: str         # user | assistant | toolResult
    kind: str         # text | thinking | toolCall | result
    text: str         # 已提取的可搜文本
    tool_name: str = ""

def iter_turns(path: Path, include: str = "conversation") -> Iterator[Turn]
def turn_text(turn: Turn) -> str
```

- 逐行 `json.loads`，坏行跳过（与 `Session.load` 同样的宽容策略），非 UTF-8 用
  `errors="replace"`。
- 不实例化 `Session`：`read_session` 不解析 blob/artifacts，图片这类内容 v1 不呈现。
- `thinking` 块在这个模块里就被丢掉，任何 `include` 都拿不到——"一律不搜不读"在这里
  落实一次，而不是散在三个工具里。
- 放在 `agent/` 而不是 `tools/`：它不认识 `AgentTool`，只是会话文件的读取语义，
  和 `session.py` / `session_index.py` 同层。

## 注册与预算

**builder check 改掉了原来的"静态实例"决定。** `agent/session.list_sessions()` 现在
**没有 `root` 参数**：它对 `sessions_dir()` 不传参，因此跟着**进程 cwd** 走。实测：

```
进程 cwd = /Users/mac/zen-agent        → …/projects/zen-agent-c2897371/sessions
runtime 服务 /tmp/zt（root 已设）      → …/projects/zt-fc093932/sessions   ← 工具会拿错
```

CLI 在仓库根跑时两者重合，看不出问题；但 `RuntimeOptions.root` 是公开参数，RPC 宿主
（`cli/rpc/host.py` 的 `_spawn` 用 `replace(self._options, root=root, ...)`）会为任意根
起 slot，桌面三种模式都走这条路。静态工具实例会把"别处项目的会话"当成"这个项目的"。
所以：

- 三个工具改成**按 Runtime 构造**（`build_session_tools(root: Path)`），在
  `app/skills_recompose.recompose()` 里和 `memory_store` 同一处注入 `runtime.root`——
  这是既有模式，不是新机制。
- 给 `agent/session.list_sessions()` 补一个 `root: Path | str | None = None` 关键字参数，
  透传给 `sessions_dir(root=...)`；`scope="all"` 已走 `all_session_dirs()`，不受影响。
  这是一处对 `session.py` 的最小改动（原计划是"不动 `session.py`"，现撤回）。
- `read_session` 的 id → 路径解析同样以注入的 root 为基准；`all=true` 时才去
  `all_session_dirs()` 里找。

其余不变：

- 三个工具加进 `tools/__init__.base_tools()` 的 prompt 顺序，排在 `grep` 之后——
  先文件、后历史。`base_tools()` 因此需要一个可选的 `session_root` 参数（与既有的
  `cwd`/`shell_max_output_bytes` 同风格），`None` 时按进程 cwd（保持现有调用点、
  `prompt_fixtures.golden_tools()` 和测试不破）。
- 描述走 `prompts/blocks/tools/*.md` 块，进 `TOOL_BLOCK_NAMES`；golden 重生成
  （`uv run python scripts/prompt_goldens.py`）。
- `TOOL_TOTAL_BUDGET_CHARS` 5200 → 6200，`TOOL_BUDGET_CHARS` 900 不变。
- `prompts/blocks/system/tool_policy.md` 加一条：
  用户提到"以前的会话/上次/那个 session"时，先 `search_sessions` 再猜。

### 第二个缺陷：`list_sessions` 会写盘

`list_sessions()` 在末尾调 `SessionIndex.flush()`，会把 `_index.json` 落在
`~/.zenagent/projects/<key>/sessions/` 里。实测（临时 home，一条会话）：
列一次，`_index.json` 就出现了。

一个声明 `approval="read"` 的工具在 plan 模式 / `--read-only` 下写盘，是契约上的意外。
**我的决定：工具路径关掉缓存写入**——给 `SessionIndex` 加 `cache=False`（只读模式，
不 flush），三个工具都传它。代价实测：66 个文件 `index.read` 走缓存 0.010s，
不走缓存直接 `read_info` 0.336s。只有 `list_sessions` 付这个代价，可接受；
换来的是一条干净的不变量：**本功能的三个工具永不写盘**。

## 影响的既有文件

| 文件 | 改动 |
|---|---|
| `zenagent/agent/session_text.py` | 新增：提取器 |
| `zenagent/tools/sessions.py` | 新增：`build_session_tools(root)` |
| `zenagent/tools/__init__.py` | `base_tools(session_root=...)` + `ALL_TOOLS` |
| `zenagent/agent/session.py` | `list_sessions(..., root=None)` 透传 |
| `zenagent/agent/session_index.py` | `SessionIndex(cache=False)` 只读模式 |
| `zenagent/app/skills_recompose.py` | 注入 `runtime.root` 构造三个工具 |
| `zenagent/prompts/blocks/tools/{list,search,read}_session.md` | 新增三块 |
| `zenagent/prompts/spec.py` | `TOOL_BLOCK_NAMES`、`TOOL_TOTAL_BUDGET_CHARS` |
| `zenagent/prompts/blocks/system/tool_policy.md` | 一条规则 |
| `tests/golden/prompts/*` | 重生成 |
| `tests/test_tools_sessions.py` | 新增 |
| `docs/guide/tools-permissions.md` | 三行表格 |
| `docs/guide/sessions.md` | 一节"跨会话检索" |

不动：`app/permissions.py`（无 path 参数即无需分类）、`app/safety.py`。

## 测试

- 提取器：三层各拿到什么、thinking 永不出现在任何一层、坏行跳过、消息序号只数消息行。
- `search_sessions`：按会话聚合并分页（`skip` 数会话）、`max_hits` 生效、
  无命中时的升级提示确实基于第二档的实际计数。
- `read_session`：分页与 `next_offset`、`offset` 指向 `search_sessions` 报的 `#N` 时
  拿到同一条、越界 offset 报错而不是空。
- id：裸 stem、限定形式、跨项目歧义报错。
- **root 隔离**（builder check 发现的缺陷，要有回归测试）：把一条会话写进
  `<tmp home>/projects/<keyA>/sessions`，把工具 root 设成另一个项目 `keyB`，
  `list_sessions` / `search_sessions` 必须看不到 A 的会话；再验证
  `list_sessions(root=keyA)` 能看见——同一个测试覆盖 `session.list_sessions` 的新参数。
- **不写盘**（第二个缺陷）：在只读目录（或用 `os.chmod` 去掉写位）里跑
  `list_sessions` / `search_sessions` / `read_session`，三个都必须成功且不留
  `_index.json`。
- 权限：三个工具都没有 `path` 参数（结构性断言——遍历 schema 的 properties）；
  `approval == "read"`；plan 模式与子代理可见。
- 预算：`test_prompts_budget.py` 通过，golden 无漂移。
- 全部离线：fixture 写在 `tmp_path` 的 `zenagent_home` 里（沿用
  `conftest.isolated_project_home` 的 monkeypatch 点），不读真实 `~/.zenagent`。
  注意 `isolated_project_home` 是显式 fixture，不是 autouse——新测试要自己声明它。

## 留给 builder 的判断

- 摘录窗口大小（现在定 60 字符前 / 90 字符后）。
- `max_hits` 默认 5 还是 3。
- 升级提示是否也覆盖 `tools → all` 这一跳（我倾向覆盖，成本是又一次计数扫描）。
- 无命中且第二档也无命中时，是否顺带提示"试试 `list_sessions all=true` 看别的项目"。

## 已知取舍

- fork / rewrite 会话会重复同一段内容（本机有 3 条 `derivedFrom` 链），搜索结果里同一段
  可能出现两三次。不在 v1 去重：`derived_from` 已经在列表和结果里显示，模型能自己合并。
- 直接解析 JSONL 而不走 `Session.load()`，意味着 blob/artifacts（图片等）不参与检索。
- 单行最长实测 59KB（toolResult），摘录时按字符切不会有性能问题，但 `q in line` 预筛
  在这些行上会整行读进内存——已测，全量扫描仍 <150ms，不需要流式优化。
- `bash cat ~/.zenagent/...` 不经过权限门这件事，本设计不改变也不掩盖。
- `list_sessions` 关掉缓存后从 0.010s 涨到 0.336s（66 个文件）。如果将来这个数变得碍事，
  正确的修法不是恢复写盘，而是给进程内加一层内存缓存（无副作用），本设计不做。
