# ZenAgent Prompt 体系设计（2026-10-31）

状态：已批准（2026-10-31，用户选择路线 1 并指定在 `feat/prompt-system` worktree 执行；基线更新为 v0.4.1）
作者：Lead（与用户讨论后整理）
基线：**v0.4.1（`dde8bf0`，2026-10-31）**。设计初稿基于 v0.4.0；0.4.1 新增 `ask`/`task` 工具并更新 plan 附注，本文档的「今天」数字已按 0.4.1 重测（见 §1/§6.3）。
相关：`docs/research/omp-cli-study/11-prompt-inventory-and-comparison.md`（现状审计与 omp 对照）、
`zenagent/app/support.py`、`zenagent/app/modes.py`、`zenagent/context_files.py`、`zenagent/skills.py`、
`zenagent/app/compact.py`、`zenagent/extensions.py`、`zenagent/tools/`、`zenagent/app/runtime.py`、
`zenagent/cli/main.py`、`zenagent/cli/repl.py`、`zenagent/cli/rpc.py`、`scripts/eval.py`、
`zenagent/evals_runner.py`、`scripts/check.sh`

## 1. 意图与为什么

用户判断：zen 的 prompt「做的太薄了」，要求**成体系、可测试、可实验、系统性 lift**。

审计（`11-prompt-inventory-and-comparison.md`）确认了症状与病根：

- 症状（按 v0.4.1 `dde8bf0` 重测）：9 个 prompt 位置 / 22 个制品，固定文本 **5,949 字符**；基础 system prompt **802 字符**（11 内置 + todo/task/read_skill）、**14 条工具描述**、一条 plan 附注（294 字符）、4KB skills 索引、32KB 上下文注入、1 条中文压缩摘要。omp 同位置是 10,360+ 字符 system + 7,303 B 工具描述 + 261 个提示词文件。
- 病根：**散**。prompt 文本散在 6 个模块（`support.py`/`modes.py`/`context_files.py`/`skills.py`/`compact.py`/各工具模块）；装配逻辑在 4 个前端各写一遍（`app/runtime.py`、`cli/main.py`、`cli/repl.py`、`cli/rpc.py`），顺序还不完全一致；`scripts/eval.py` 另有一条自己的 base prompt；没有版本、没有 lint、没有 golden、没有 token 预算；工具描述焊死在工具实现里。

现有资产（要复用，不要重造）：

- `evals/`：18 个行为用例（真实模型、隔离 workspace、断言输出/工具序列/终止/成本/文件），成本约 $0.02/轮。
- `scripts/eval.py`：`--repeats`、`--report`、`--baseline`（回归即 exit 1）、`--min-pass-rate`、`--max-total-cost`；case 里已有 `system_prompt_override` 这个「临时变体」后门。
- `zenagent/evals_runner.py`：报告 load/write/compare/render，纯函数、可单测。
- `scripts/check.sh`：唯一闸门（uv sync → ruff → pytest），CI 只跑它；离线、无 key。
- `zenagent/frontmatter.py`、`zenagent/templates.py`：已有 frontmatter/模板机制可参考。

用户已选**路线 1：prompt 子系统 + 评测/实验双轨**（见 §4）。omp 在本设计中是**假设库**（Tool Policy 三分、交付契约、验证义务、plan 模式、压缩结构化），不是模板依赖。

## 2. 目标 / 非目标 / 反目标

**目标**

1. **单一装配契约**：所有前端（TUI/headless/REPL/RPC/evals）通过同一个 `compose()` 得到 prompt；P0 阶段默认配置的渲染结果与 v0.4.1 **逐字节一致**。
2. **prompt 数据化**：每个 block 有 `id`、`version`、`cache_class`、字符预算、依赖变量；内容在 Markdown，元数据在 Python（类型安全、无 YAML 依赖）。
3. **离线可测**：golden 渲染快照（配置矩阵）+ lint + token 预算 + 单元测试，全部走 pytest，进 `scripts/check.sh`；不需要网络/API key。
4. **在线可实验**：eval 支持 prompt variant（overlay 目录）与 block 级消融；A/B + repeats + 报告对比（复用 `evals_runner`）；保留 baseline 回归门。
5. **内容 lift 有协议**：每条 omp 假设 = 一个 variant + 目标指标 + 决策规则；没有评测证据的加长不合并。
6. **保持 zen 的定位**：纯 Python（运行时依赖仍只有 httpx）、无模板引擎依赖、内核契约（`StreamFn` 不抛、工具永远返回结果）不动。

**非目标（本期不做）**

- 不引入 Jinja2/Handlebars 等模板引擎；不引入 omp 的 30+ 工具、子代理、记忆、标题生成、goal/vibe 等功能面。
- 不做自动 prompt 优化（DSPy/GEPA/OPRO）；未来可接，本期只把「可实验」的地基打好。
- 不做 provider-specific prompt 分支（先统一一套，多模型差异交给实验）。
- 不改工具 JSON Schema、权限/审批逻辑、压缩算法本身（只改提示词文本与装配方式）。
- 不把 system prompt 写进 session JSONL（现状保持：每轮请求临时构造）。
- 不做 prompt caching 的协议级改造（cache_class 只做元数据与未来准备，P0 不发 cache_control）。

**反目标（出现即视为失败）**

- P0 默认配置渲染文本与迁移前不一致。
- 离线测试需要网络或 API key；`scripts/check.sh` 不再能本地=CI 地跑。
- prompt 变长但没有评测证据；预算被随手突破。
- 前端行为改变（RPC 仍只 A→D、eval 仍用其自定 base prompt——除非实验显式改变）。
- 引入新运行时依赖或模板引擎。
- prompt 内容与工具实现脱节（例如 system prompt 列出不存在的工具）。

## 3. 约束

- Python ≥3.11；运行时依赖只有 `httpx`；模板渲染自己实现（目标 ≤120 行，纯标准库）。
- 内容文件：`zenagent/prompts/blocks/*.md`（UTF-8，Markdown）。渲染语法只有 `{{var}}` 单次插值；**不支持条件/循环**（条件与顺序在 Python 里，见 §5.3）。数据（AGENTS.md、扩展片段、skill 描述）插入后**不二次渲染**。
- 元数据：Python dataclass（`spec.py`），不用 YAML/TOML；新增 block 需要登记（这是有意的：显式、可校验）。
- 装配顺序固定为今天的 A→D→E→G→C（见 §5.3）；各前端的 include 组合作为参数显式保留。
- prompt 文本默认英文；压缩摘要当前是中文，语言作为实验变量（§8 H8）。
- `scripts/check.sh` 仍是唯一闸门：prompt 测试全部走 pytest，不新增 CI job（P0/P1）。在线行为评测保持 opt-in（有 key、花钱），不默认进 CI；nightly 另议。
- 工具描述迁移不得造成 import cycle：`zenagent.prompts` 只依赖标准库；`zenagent.tools` 单向依赖 `zenagent.prompts`。
- `Agent.system_prompt: str` 接口不变；provider 层不动。
- 包数据：wheel 必须包含 `zenagent/prompts/blocks/**/*.md`；P0 加一条打包检查（见 §10 风险）。
- 版本号：`PROMPT_VERSION`（日期序号，如 `2026-10-31.1`），block 级 `version` 只用于审计，不参与渲染。

## 4. 方案（用户已选 1）

| | 路线 | 结果 |
|---|---|---|
| **1（已选）** | prompt 子系统 + 评测/实验双轨：数据化 + 单一 compose + 离线 golden/lint + 在线 variant/消融 | 结构、可测、可实验三件事各归其位；纯 Python；omp 是假设库 |
| 2 | omp 式模板分层（Jinja/Handlebars + 条件变量 + 块数组 + 缓存分层） | 形态快，但依赖/复杂度/测试面激增，一次变量太多不可归因，与极简定位冲突 |
| 3 | 评测先行、prompt 只做最小拆分 | 最快，但不解决「散」的结构债，实验只能整体 A/B |

选 1 的理由：用户要的「成体系 / 可测试 / 可实验」正好对应它的三层——block 结构让实验有颗粒度，golden+lint 让离线可测，variant+消融让在线可实验。路线 2 的「一次到位」会让实验失去归因能力；路线 3 把结构债留到以后。

## 5. 架构

### 5.1 模块布局

```text
zenagent/prompts/
  __init__.py          # 公共 API：compose_system() / compose_compact() / PromptContext /
                       # RenderedPrompt / PROMPT_VERSION / tool_description()
  spec.py              # BlockSpec / PromptSpec / 预算与版本常量（类型安全）
  registry.py          # 加载 blocks、校验、按 id 查找；variant overlay 支持（P1）
  render.py            # {{var}} 单次渲染器（~100 行，纯标准库）
  blocks/
    system/
      identity.md        # "You are zenagent, ..."
      environment.md     # {{cwd}} + {{tool_list}}
      tool_policy.md     # Specialized / General / Exploration
      tool_policy_todo.md# 条件块：todo 行（工具面含 todo 时）
      tool_policy_parallel.md # "- Parallel: ..." 行
      closing.md         # "Answer concisely. ..."
    plan_mode.md         # C plan 附注
    context_files.md     # D 上下文包装（## Project instructions + 逐文件节）
    skills.md            # E skills 索引页眉
    compact_system.md    # F 压缩 system
    compact_summary.md   # F 压缩 user（含 {{transcript}}）
    compact_result.md    # F 压缩结果注入（# 此前会话摘要 + {{summary}}）
    task_child_system.md # task 子代理的只读子代理 system prompt
    tools/
      read_file.md write_file.md edit_file.md list_dir.md bash.md wait.md
      ask.md task.md glob.md grep.md web_search.md fetch.md todo.md read_skill.md
  CHANGELOG.md         # 版本 → 改了什么 → 实验证据链接
```

### 5.2 数据模型

```python
# spec.py（示意）
@dataclass(frozen=True)
class BlockSpec:
    id: str                 # "system" | "plan_mode" | "tool:read_file" | ...
    path: str               # 相对 blocks/ 的文件名
    version: str            # "2026-10-31.1"
    cache_class: str        # "stable" | "session" | "volatile"
    budget_chars: int       # lint 硬上限
    required_vars: tuple[str, ...]
    optional: bool = False  # 是否允许 --disable-block 消融

@dataclass(frozen=True)
class PromptContext:
    cwd: Path
    tool_names: Sequence[str]
    mode: str = "ask"                 # plan | ask | full
    context_text: str = ""            # 已渲染好的 AGENTS.md 节
    skill_entries: str = ""           # skills.py 预格式化（条目 + 截断哨兵），避免 prompts 反向依赖 skills
    extension_fragments: Sequence[str] = ()
    include_context: bool = True
    include_skills: bool = True
    include_extensions: bool = True
    include_mode: bool = True
    system_override: str | None = None  # eval case 的临时覆盖（保留现有后门）
    root: Path | None = None            # P1 variant overlay 根；None = package blocks

@dataclass(frozen=True)
class RenderedPrompt:
    text: str
    blocks: tuple[RenderedBlock, ...]  # id / chars / cache_class / version
    version: str
```

- `PromptSpec`（`spec.py`）持有每个 prompt 的有序 block id 列表；`compose_system()` 在 Python 里按条件组装（`include_*` 开关 + 工具面判断），**模板里没有控制流**。
- 连接规则：system 子块之间用 `\n`（保持今天的逐行文本）；顶层段落（system / context / skills / extensions / plan）之间用 `\n\n`。
- `optional=True` 的块允许 `--disable-block` 消融（`tool_policy`/`closing`/`plan_mode`/`context`/`skills`/`extensions`）；`identity`/`environment` 不允许。
- system 拆成子块的原因：实验颗粒度（H2 交付契约、H3 workflow、H1 tool policy 各自独立预算/消融/覆盖），同时保持 P0 拼接后逐字节一致。

### 5.3 装配契约

固定顺序（与今天一致）：

```text
system(A) → context(D，默认开) → skills(E，有 skill 时) → extensions(G，有扩展时) → plan note(C，仅 plan)
```

- 顶层块用 `\n\n` 连接；system 内部子块按 `identity → environment → tool_policy → tool_policy_todo（条件） → tool_policy_parallel → closing` 用 `\n` 连接（拼出来必须与 0.4.1 的 802 字符逐字节一致）。D 的包装保持 `## Project instructions` + `### <path>` 的现有格式；E 保持现有页眉；C 直接追加。
- 条件在 Python：`include_*` 开关由前端决定；`system_override` 非空时替换 A（用于 eval 的 case 级覆盖）。
- 前端 include 组合（P0 保持现状）：

| 前端 | A | D | E | G | C | 工具面（0.4.1） |
|---|---|---|---|---|---|---|
| TUI/app（Runtime） | ✓ | 默认 ✓ | 有 skill | 有扩展 | plan 时 | ALL_TOOLS(11)+todo+task+MCP+read_skill+扩展 |
| headless | ✓ | 默认 ✓ | 有 skill | 有扩展 | plan 时 | ALL_TOOLS(11)+MCP+read_skill+扩展+todo+task（顺序与 TUI 不同） |
| REPL | ✓ | 默认 ✓ | 有 skill | 有扩展 | plan 时 | ALL_TOOLS(11)+todo+task+MCP+read_skill+扩展 |
| RPC | ✓ | 默认 ✓ | ✗ | ✗ | ✗ | ALL_TOOLS(11)+todo+task |
| `scripts/eval.py` | `system_override` 或现有短句 | case 开关 | 有 skill | 有扩展 | ✗ | ALL_TOOLS(11) only（含 ask；无 todo/task） |

- 0.4.1 的四个前端工具顺序仍不一致（headless 把 MCP/read_skill/扩展放在 todo/task 前）；P0 不强行统一，golden 按前端分别记录。
- 条件工具集合从 0.4.0 的 `{todo, read_skill}` 变成 0.4.1 的 `{todo, task, read_skill}`；`task` 在 TUI/headless/REPL/RPC 都注册。

- 压缩（F）不走 system 装配：`compose_compact(transcript) -> (system_text, user_text)`；调用点仍是 `zenagent/app/compact.py`。

### 5.4 渲染规则

- 单次扫描模板，替换 `{{name}}`；值原样插入，**不再扫描值**（AGENTS.md 里出现 `{{` 不会破坏渲染）。
- 未知变量：lint 报错；运行时 `render()` 抛 `PromptRenderError`（测试会先抓到）。
- 未使用的 `required_vars` 缺失 = 错误；`{{var}}` 在模板里出现但不在 `required_vars` = lint 错误。
- 不做转义/HTML 处理（纯文本 prompt）。

### 5.5 工具描述

- 每个工具一份 `blocks/tools/<tool_name>.md`；工具模块通过 `prompts.tool_description(name, **vars)` 获取（registry 缓存，import 时读取一次）。
- **模板变量承载实现常量，避免第二份真相**（审计发现 4 个内置描述插值了工具模块常量）：
  - `read_file`：`{{default_read_limit}}` / `{{max_read_limit}}`（`fs.py` 的 `DEFAULT_READ_LIMIT` / `MAX_READ_LIMIT`）
  - `glob`：`{{max_glob_results}}`；`grep`：`{{max_grep_files_per_page}}` / `{{max_grep_hits_per_file}}`；`web_search`：`{{max_results}}`
  - `read_skill`：`{{skill_names}}`
- `spec.py` 只声明变量名（stdlib-only 的 `spec.py` 不能 import 工具常量）；值由工具模块在调用 `tool_description()` 时传入；lint 校验「模板里的变量 == 声明的 required_vars」，运行时缺值抛错。
- `ask`（509 字符）与 `task`（323 字符）为静态文本；工具描述文件共 **14 个**（11 内置 + todo + task + read_skill）。
- 防漂移测试：`tests/test_prompts_tools.py` 用工具模块的常量渲染 prompt，与 `AgentTool.description` 实际值比对——数字只有一个来源，`.md` 里只有占位符。
- 工具 JSON Schema 完全不动；`describe_tools()`（`/tools` 命令）继续读 `AgentTool.description`。
- 条件工具（`todo`、`read_skill`）也必须有文件；`tests/test_prompts_tools.py` 校验覆盖与孤儿。

### 5.6 版本与元数据

- `PROMPT_VERSION` 在 `prompts/__init__.py`；`CHANGELOG.md` 每条记录：日期、版本、改动的 block、原因、实验记录链接（`docs/prompts-experiments/...`）。
- `RenderedPrompt.version` 暴露给调试/评测报告；**不写入 session JSONL**。
- 报告（eval）带 `prompt_version`、`variant`、`blocks`（id/chars），保证实验可复现。

### 5.7 CLI：`zen prompts`

在 `main()` 里像 `rpc`/`sessions` 一样前置分发（`raw[0] == "prompts"`），只读、不调模型：

- `zen prompts list`：id / version / chars / cache_class / budget。
- `zen prompts show <id>`：渲染当前配置（可选 `--mode plan`、`--with skills,context`）。
- `zen prompts estimate`：各块 chars 与 token 估算（chars/4，标注为近似），并对照预算。

## 6. 可测试性（离线）

### 6.1 单元测试（pytest，进 `scripts/check.sh`）

- `tests/test_prompts_render.py`：变量替换、缺失变量报错、数据不二次渲染、换行/连接规则。
- `tests/test_prompts_registry.py`：id 唯一、文件存在、required_vars 与模板一致、预算字段合法、无未登记文件。
- `tests/test_prompts_compose.py`：装配顺序、各前端 include 组合、plan 附注只在 plan、skills/extension 顺序、context 上限与截断、`system_override` 行为。
- `tests/test_prompts_tools.py`：`ALL_TOOLS`+条件工具全覆盖、无孤儿 prompt、`read_skill` 名字插值、工具 roster 行与实际工具一致。
- `tests/test_prompts_compact.py`：压缩 system/user 渲染、transcript 渲染（仍跳过 toolResult）、摘要注入前缀不变。
- `tests/test_prompts_golden.py`：见 6.2。

### 6.2 Golden 渲染快照

- `tests/golden/prompts/<config>.txt`，配置矩阵：
  `default-11`（11 内置）、`tui-14`（+todo/task/read_skill）、`plan`、`skills`、`context`、`extension`、`rpc-13`（+todo/task）、`eval-11`、`compact`。
- 生成方式：`ZEN_UPDATE_GOLDEN=1 pytest tests/test_prompts_golden.py`；测试失败打印 unified diff。
- 归一化：cwd 替换为 `<CWD>`；skills/extension 用固定 fixture；不写绝对路径。
- **P0 验收方式**：先用迁移前的代码生成 golden 基线，再重构；默认配置必须逐字节一致。

### 6.3 Lint + 预算

- 预算（硬上限，lint 失败；调整预算需改 `spec.py` 并在 CHANGELOG 说明）。预算是**上限不是目标**：P0 实际值不变，P2 的 lift 在预算内做，超预算必须先改 spec 并说明理由：

| block | 预算 chars | 今天（0.4.1，cwd 归一化） |
|---|---|---|
| `system/identity` | 120 | 66 |
| `system/environment` | 240 | 170 |
| `system/tool_policy` | 700 | 252 |
| `system/tool_policy_todo` | 220 | 163 |
| `system/tool_policy_parallel` | 120 | 90 |
| `system/closing` | 100 | 55 |
| **system 合计** | **1,600** | **802** |
| plan_mode | 700 | 294 |
| context wrapper 头 | 160 | 26 |
| skills 页眉 | 120 | 83 |
| compact system | 200 | 125 |
| compact summary | 1,600 | 201 |
| compact result | 40 | 30 |
| task_child_system | 600 | 368 |
| 单个工具描述 | 900 | 最大 719（todo） |
| 工具描述合计 | 4,400 | 4,041（11 内置 2,918 + todo 719 + task 323 + read_skill 81） |
| **固定文本合计** | **9,000** | **5,949** |

- 预算依据：0.4.1 的 system 已到 802；H2（交付契约 ~250）+ H3（workflow ~350）会把 system 推到 ~1,400，system 预算给到 1,600 留余量；H8 的 8 节 handoff 需要 ~1,200–1,600 的 compact summary；H6 的「更深规则覆盖」提示需要 context wrapper 从 26 到 ~110。预算给到「能容纳预期 lift 但不无限膨胀」的位置，超出即视为实验失败或需要重新设计。

- token 估算：`ceil(chars / 4)`（写进文档，明确是近似，不引 tokenizer）；报告里给出估算值。
- lint 规则：未知/缺失变量、重复 id、文件未登记、工具覆盖、roster 一致性、预算超限。
- 全部通过 pytest 执行（不新增脚本），保持 `check.sh` 单闸门。

### 6.4 CI

- `scripts/check.sh` 结构不变（`uv sync → ruff → pytest`），prompt 测试随 pytest 跑；无网络、无 key。
- 在线评测不进 CI（见 §7.5）。

### 6.5 文本来源闸门（P0 grep gate）

- 目标：固定 prompt 文本只在 `zenagent/prompts/blocks/`。`tests/test_prompts_sources.py` 扫描源码里已知的 prose 标记，只允许 blocks/ 与显式豁免清单命中。
- 显式豁免（数据序列化/格式化，不是 prompt 正文）：
  - `zenagent/app/compact.py` 的 transcript 序列化标记（`## {role}`、`[thinking]`、`[tool_call …]`、`[image]`）；
  - `zenagent/skills.py` 的条目格式 `- {name}: {description}` 与截断哨兵 `- ... more skills omitted`；
  - `scripts/eval.py` 的 dev 短句（P1 迁到 `evals/variants/dev-short/` 后从豁免表删除）。
- 豁免清单写在测试里（文件 + 字符串精确匹配）；新增豁免必须显式加，这样未来漏网的 prompt 字面量会被测试抓住。
- `compact_result.md` 承载 `# 此前会话摘要\n{{summary}}`（今天在 `compact.py:159-161`），纳入 blocks，不再豁免。

## 7. 可实验性（在线）

### 7.1 Variant 机制

- **Overlay 目录**：`evals/variants/<name>/` 镜像 `zenagent/prompts/blocks/` 的相对路径；存在即覆盖，缺省回落 base。
- `scripts/eval.py` 新增 `--variant <name>`（默认 `baseline`，无 overlay）；报告记录 variant 与 prompt_version。
- 单次消融：`--set-block <id>=<path>`（临时覆盖某块）、`--disable-block <id>`（仅 `optional=True` 的块：tool_policy/closing/plan_mode/context/skills/extensions）。
- **工具描述的变体在 eval 侧生效**：10 个静态工具是模块级单例（`READ_FILE = _tool(...)`，import 时固化描述），所以 runner 解析参数后用 `dataclasses.replace(tool, description=...)` 生成工具副本（`AgentTool` 是可变 dataclass，`zenagent/agent/loop.py:91`）；运行时路径不变。registry 的 `compose_*` 支持 `root` 参数（默认 package blocks，variant 时指向 overlay 根）。
- 不在 import 时读 variant：那会要求进程启动前设环境变量（`scripts/eval.sh` 可以，但 `zenagent evals` 不行），还会污染运行时；eval 侧副本更小、更可控。
- case 级 `system_prompt_override` 保留（现有后门，也是最小变体）。

### 7.2 指标与报告

- 每 run：pass/fail、cost、tokens_in/out、turns、tools、duration（已有）。
- 每 variant 聚合：pass rate、总成本、总 token、p50 turns、每 case 的 pass/fail。
- 报告新增字段：`prompt_version`、`variant`、`blocks`（id/chars）。
- `zenagent/evals_runner.py` 扩展：`compare_variants(reports)` 输出 per-case delta（fix/regression/same）与成本/token 差值；现有 `--baseline` 语义不变。

### 7.3 实验协议（写进文档模板）

1. 一个假设 = 一个 variant（只改一个变量）；写 `docs/prompts-experiments/YYYY-MM-DD-<slug>.md`：假设、来源（omp 条款/观察）、variant、用例、结果、决策。
2. 跑全量用例 + 目标用例，≥3 repeats，同一模型/参数；记录成本。
3. 决策规则（三条全满足才采纳）：
   - 安全/负向用例零回归（`read_only_refusal`、`dangerous_command_refused`、`tool_deny_policy`、`read_file_secret`）；
   - 全量 pass rate 不低于 baseline（允许最多 1 个 case 的翻转，且必须重复出现才认定回归）；
   - 目标指标改善，或 token 下降 ≥10% 且 pass rate 不降。
4. 采纳后：合并 variant 内容 → 更新 `PROMPT_VERSION`/`CHANGELOG` → 保留实验记录链接。
5. 未采纳也记录（避免重复试错）。

### 7.4 新增 prompt 敏感用例（P1）

| 用例 | 断言 | 目的 |
|---|---|---|
| `instruction_conciseness` | 输出 ≤ N 句/字符，且不含套话 | 验证「Answer concisely」类条款 |
| `specialized_tool_preference` | 读文件用 `read_file` 而非 `bash cat`；搜索用 `grep` 而非 shell grep | Tool Policy |
| `verify_before_done` | 修复后实际运行 `pytest`（已有 `multi_step_investigation` 强化） | 验证义务 |
| `plan_mode_read_only` | `--mode plan` 下拒写 + 输出计划 | plan 附注 |
| `compaction_continuation` | warmup 埋关键事实 → 强制压缩 → 第二问仍能引用事实/文件 | 压缩摘要保真 |
| `context_file_respect` | AGENTS.md 规则被遵守（已有 `agents_md_respected` 强化） | 上下文注入 |
| `skills_use` | 触发 skill 时先 `read_skill`（已有） | skills 索引 |

- `compaction_continuation` 需要 eval harness 新字段：`warmup_prompt` + `compact: true`（先跑 warmup，再 `compact()`，再跑 `prompt`）；断言沿用现有机制。

### 7.5 成本、运行与所有权

- **P1 起 eval 必须跑真实 composed system prompt**：否则 §8 的 H1–H4（system/plan 内容）在 eval 里完全不可测——现在 `scripts/eval.py:213` 用的是 `system_prompt_override or "You are zenagent, a coding agent. Be concise."`，overlay 改 `system/` 对结果没有影响。P1 把默认改成 `compose_system()`；旧短句保留为 `dev-short` variant，切换本身作为一次 A/B 记录；切换后重跑 baseline（旧 `--baseline` 报告作废，记录在实验目录）。
- 18 cases × 3 repeats ≈ $0.02；variant 矩阵成本线性，用 `--max-total-cost` 封顶。
- 在线运行保持本地手动（`scripts/eval.sh`）；nightly 需要仓库 secrets，本期不做，只在设计里留接口。
- P2 付费实验由实现者用仓库 `.env` 执行；canonical model = catalog `default: true` 的模型（当前 README 示例用 `deepseek-flash`，实验记录里写明实际 id）；baseline 与 candidate 必须同模型同参数；单实验花费上限 $0.10、总计 $0.50。

## 8. 内容 lift（omp 假设清单）

每条 = 一个 variant + 目标指标；先做 H2/H3/H4/H8，再做 H5/H6/H7；H9 留未来。

| # | 假设 | omp 来源 | zen 的改动 | 目标指标 |
|---|---|---|---|---|
| H1 | Tool Policy 三分 + 边界条款能减少 shell 兜底/乱猜 | `system-prompt.md:95-180` | 在现有 Specialized/General/Exploration 上加「禁止 sed 编辑」「失败重读」「独立调用并行」 | specialized_tool_preference、成本 |
| H2 | 交付契约（不编造、不扩范围、完成可达工作）减少假完成/范围蔓延 | `system-prompt.md:235-257` | 新增 ~250 字符的 `<contract>` 段 | 全量 pass rate、`verify_before_done` |
| H3 | 6 步 workflow（scope→research→implement→verify→cleanup）提升多步任务成功率 | `system-prompt.md:182-233` | 压缩成 ~350 字符的 4–5 步 | `multi_step_investigation`、turns |
| H4 | plan 附注升级（计划要可执行、要 file:line 证据）提升 plan 模式质量 | `plan-mode-active.md` | 294 → 400–700 字符 | `plan_mode_read_only`、人工评审 |
| H5 | 工具描述寄存器化（边界、错误恢复、示例）提升工具选择正确率 | `prompts/tools/*.md` | 逐条改 1–2 个工具描述做 A/B | 对应工具用例、tokens |
| H6 | 上下文文件「更深规则覆盖更浅」提示减少漏读 | `project-prompt.md:18-24` | D 块加 ~80 字符 | `agents_md_respected` |
| H7 | skills 索引 hint 化（≤12 词）降低 token 且不降触发率 | `prompts/skills/compress-description.md` | 对 skills 索引做压缩实验 | `skills_use`、tokens |
| H8 | 压缩摘要 3 节中文 → 8 节 handoff（中文/英文各一版）提升续作保真 | `pi-agent-core/src/compaction/prompts/compaction-summary.md` | 改 `compact_summary.md` | `compaction_continuation` |
| H9 | cwd/日期移出 system、做缓存稳定前缀 | `date-cwd-reminder.ts`、`system-prompt.ts:1063-1067` | 结构实验（需要 provider cache 证据；zen 目前不主动设 cache_control） | 成本/cache 命中；留未来 |

## 9. 迁移步骤

**P0（重构，零行为变化，1 个迭代）**

1. 用迁移前代码（v0.4.1 `dde8bf0`）生成 golden 基线（`default-11`/`tui-14`/`plan`/`skills`/`context`/`extension`/`rpc-13`/`eval-11`/`compact`）。
2. 新建 `zenagent/prompts/`：迁入 6 处固定文本 + 14 条工具描述；实现 `render.py`/`registry.py`/`compose`；`PROMPT_VERSION=2026-10-31.1`。
3. 改 4 前端 + `scripts/eval.py` 调 `compose()`（保留各自 include 组合）；删除旧常量与 `system_prompt()`/`mode_prompt_note()`/`system_prompt_with_context()`/`skills_prompt_index()` 的直接文本拼接（函数可保留为薄封装，便于渐进迁移）。
4. 单元测试 + golden + lint/预算；`scripts/check.sh` 全绿。
5. `zen prompts list/show/estimate`。
6. 打包检查：build wheel 后确认 `zenagent/prompts/blocks/**/*.md` 在包内（可加进 `scripts/release-check.sh` 或一条 pytest 读取 `importlib.resources` 的用例；二者都做更稳）。

验收：默认渲染逐字节一致；`grep` 确认 prompt 文本只在 `zenagent/prompts/blocks/`；全测试绿；无新依赖。

**P1（实验基建，1 个迭代）**

1. eval variant overlay + `--variant`/`--set-block`/`--disable-block` + 报告字段 + `compare_variants`。
2. 新增 §7.4 用例（含 `compaction_continuation` 的 harness 支持）。
3. 实验模板与决策规则落地；`docs/prompts-experiments/` 建目录。
4. **把 eval 默认切到真实 composed system prompt**（旧短句变 `dev-short` variant），重跑 baseline 并记录。这一步必须在 P2 之前完成，否则 system 内容实验测不到。

验收：同一批用例可跑 baseline vs variant 并输出 per-case delta；eval 默认使用真实 prompt 且 baseline 已重跑；实验模板可用；CI 仍离线。

**P2（内容 lift，持续）**

1. 按 §8 顺序逐条做实验；每条一个 variant + 记录；胜出者合并 + CHANGELOG + 版本号递增。
2. 实施计划里先给出**第一批候选文本**（H2 交付契约、H3 workflow、H4 plan 附注、H8 压缩摘要）的 variant 草案；H1/H5/H6/H7 在各自实验时写文本，不预先批量生产。
3. 付费运行按 §7.5 执行（实现者用 `.env`、canonical model、单实验/总花费上限），实验记录含模型 id、成本、原始报告路径。
4. 每次合并后重跑全量 + golden 更新（若文本变化则显式更新 golden 并在 CHANGELOG 说明）。
5. 每季度 review 预算与评测趋势。

验收：≥3 条实验有记录；≥1 条采纳且全量非回归；所有改动在 §6.3 预算内（超预算必须先改 spec 并说明）。

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 重构破坏行为 | P0 golden 字节级基线 + 前端 parity 测试 + 小步提交；旧函数保留薄封装 |
| import cycle（prompts↔tools） | `zenagent.prompts` 只依赖标准库；tools 单向依赖 prompts；测试里 import 顺序检查 |
| wheel 漏打 .md | build 后检查包内容（release-check 或 pytest + importlib.resources）；CI 覆盖 |
| golden 脆弱（cwd/平台） | 归一化 cwd、固定 fixture、只快照渲染结果；diff 输出可读 |
| 评测噪声/过拟合 | repeats ≥3、只认重复出现的回归、安全用例零容忍、成本封顶 |
| 内容膨胀 | 预算硬上限 + 每条改动必须带实验记录 |
| 版本漂移 | `PROMPT_VERSION` + CHANGELOG + 报告带版本 |
| eval.py 与产品 prompt 漂移 | P1 起把「eval 用真实 prompt」作为实验候选；P0 显式记录差异 |

## 11. 验收标准（汇总）

- **P0**：`compose()` 被 5 个调用方使用；默认渲染与 v0.4.1 迁移前逐字节一致；固定 prompt 文本只存在于 `zenagent/prompts/blocks/`（豁免清单见 §6.5）；golden/lint/预算/单元测试全绿；`zen prompts` 可用；无新依赖；CI 绿。
- **P1**：`zenagent evals --variant X` 可跑并出 per-case delta；eval 默认使用真实 composed prompt 且 baseline 已重跑；新用例可用；实验模板/目录就绪；工具描述变体可覆盖。
- **P2**：≥3 条实验记录、≥1 条采纳且非回归、版本与 CHANGELOG 更新、所有改动在 §6.3 预算内。

## 12. 留给实施计划 / 待定

- `zen prompts diff` 是否进 P0（倾向 P1）。
- 渲染器是否支持行内条件（当前决定：不支持，条件在 Python）。
- 压缩摘要语言（中文保留 vs 英文）作为 H8 的实验变量，不预设。
- nightly 在线评测：需要 secrets，本期不做，仅留接口。
- block 元数据是否需要 `owner`/`source` 字段（审计用）：倾向不加，保持最小。
- system 子块拆分后的 golden 配置命名/数量由实施计划定（建议 `default-11`/`tui-14`/`plan`/`skills`/`context`/`extension`/`rpc-13`/`eval-11`/`compact`）。

## 13. 已做的决定（calls，供评审核对）

1. 纯 Python 渲染器，不引 Jinja/Handlebars。
2. 内容 Markdown + 元数据 Python，不用 YAML。
3. 条件/顺序在 Python，模板只做 `{{var}}`。
4. P0 零行为变化，golden 字节级证明。
5. omp 是假设库，不照搬模板引擎/功能面。
6. 在线评测 opt-in，CI 只离线。
7. 工具描述抽离但 JSON Schema 不动。
8. 不新增功能面（标题/子代理/记忆/goal）。
9. 预算按 §6.3 表（system 1,600、compact summary 1,600、总 9,000），调整需显式改 spec + CHANGELOG。
10. `system_override` 后门保留，供 eval case 与最小变体使用。
11. system 拆成 5 个子块（identity/environment/tool_policy/tool_policy_todo/closing），P0 拼接后字节级不变；子块是实验与消融的颗粒度。
12. 工具描述里的实现常量用模板变量（`{{max_read_limit}}` 等），`spec.py` 只声明变量名，值由工具模块传入；防漂移测试比对 `AgentTool.description`。
13. 工具描述变体在 eval 侧用 `dataclasses.replace(tool, description=...)` 副本实现，运行时路径不变。
14. eval 从 P1 起默认跑真实 composed system prompt（旧短句变 `dev-short` variant），并重跑 baseline；否则 system 内容实验不可测。
15. P2 首批候选文本（H2/H3/H4/H8）在实施计划里起草；付费实验由实现者用仓库 `.env` 执行，canonical model = catalog 默认模型，单实验 ≤$0.10、总计 ≤$0.50。
16. 文本来源闸门用显式 allowlist 豁免（§6.5），新增豁免必须显式加。
17. 基线按 **v0.4.1** 重测（`ask`/`task` 新工具、plan 附注 294、固定文本 5,949，含 review 补入的 `task_child_system` 368 字符）；P0 的字节级基线以 v0.4.1 为准，设计初稿的 0.4.0 数字已全部替换。

## 14. Builder check 的问题与处理（2026-10-31）

派 fresh subagent 按 `builder-check-prompt.md` 读过本文档后提出 6 个问题，全部已处理：

| # | 问题 | 处理 |
|---|---|---|
| 1 | eval 是否跑真实 system prompt？否则 H2/H3/H4 测不到 | 采纳：P1 起默认真实 prompt，旧短句变 `dev-short` variant，重跑 baseline（§7.5、§9 P1） |
| 2 | 工具描述里的常量（read_file/glob/grep/web_search）静态写死还是变量？ | 用模板变量，`spec.py` 只声明名字，值由工具模块传入 + 防漂移测试（§5.5） |
| 3 | 工具描述在 import 时固化，variant 怎么生效？ | eval 侧 `dataclasses.replace` 工具副本；registry 的 compose 支持 `root` overlay；不改运行时（§7.1） |
| 4 | 预算与 §8 假设冲突（H2+H3 超 1,200；H8 超 400） | system 拆子块并按 lift 预期重设预算：system 1,600、compact summary 1,600、总 9,000（§6.3，按 0.4.1 重算） |
| 5 | P2 范围与谁跑付费实验？ | 实施计划起草首批候选文本；实现者用 `.env` 跑，canonical model + 花费上限（§9 P2、§7.5） |
| 6 | P0 grep gate 是否覆盖压缩前缀/transcript 标记/skills 哨兵？ | `compact_result.md` 纳入 blocks；transcript 序列化与 skills 格式化进显式豁免清单；新增 `test_prompts_sources.py`（§6.5） |
