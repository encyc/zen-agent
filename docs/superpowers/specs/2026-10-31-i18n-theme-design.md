# ZenAgent i18n 与明暗主题设计（2026-10-31）

状态：已批准（2026-10-31，用户拍板：theme 方案 A + i18n 方案 1）；实现计划见 `docs/superpowers/plans/2026-10-31-theme-appearance.md` 与 `...-i18n.md`
作者：Lead
基线：v0.4.4 + TUI 工作面板打磨第二轮（main `76d564f`，2026-10-31 21:20；本地领先 `origin/main` 11 个提交）

相关：`zenagent/cli/tui/{app.py,theme.tcss,palette.py,widgets/*,screens/*,status/segments.py}`、
`zenagent/cli/{theme.py,repl.py,main.py}`、`zenagent/app/{commands.py,permissions.py,slash_args.py,chat.py,views.py,runtime.py}`、
`zenagent/settings.py`、新增 `zenagent/cli/appearance.py`、新增 `zenagent/i18n/{__init__,en,zh}.py`、
新增 `zenagent/cli/tui/textutil.py`
参考：omp 的 theme / settings 体系（`/tmp/oh-my-pi/docs/{theme,settings}.md`）、Textual 8.2.8 的 theme API。

## 1. 意图与为什么

TUI 打磨第二轮把 Rich 文本的颜色收口到了 `cli/tui/palette.py`（17 个 widget 已 import，9 个基础色 +
section/state/context 三张语义表），但主题本身还只有一套暗色，而且散落的硬编码仍在：

- `theme.tcss` 43 处 6 位 hex（布局样式，CSS 里不能 import Python）；
- Python 里 palette 之外还有 35 处（`screens/*`、`app.py` 的 `DEFAULT_CSS`，以及
  `status/segments.py`、`screens/todo_list.py` 里 import 期捕获的 `_TEXT = palette.TEXT`）；
- REPL 另有一套 `cli/theme.py` 的 7 字段 rich 主题（default / mono / solarized / JSON 文件），
  与 TUI palette 互不相干；`--theme` 只对 REPL 生效，TUI 完全不读。

i18n 则是空白：CLI + app 层约有 **820 条**候选界面文案（启发式统计，61 个文件；大头
`cli/repl.py` 83、`app/commands.py` 70、`cli/tui/app.py` 67、`cli/main.py` 65、
`screens/modals.py` 53、`app/permissions.py` 35），TUI 没有一条文案走 catalog，全部英文硬编码。
界面默认英文、无法切换中文。

用户点的方向：一套设置（theme / language 等）、一套 i18n、一套 dark/light 主题。本设计先落
**theme + i18n** 两件；settings 只加 `theme` / `language` 两个 pref，不做完整 settings 注册表。

## 2. 目标 / 非目标 / 反目标

**目标（逐条可验收）**

1. **一套 token 源**：所有 TUI/REPL 颜色来自 `cli/appearance.py` 的语义 token；`theme.tcss` 与
   palette 不再出现字面 hex（token 定义文件除外）。
2. **dark / light / auto 三态**：`theme` 设置（+ `--theme` flag）取 `auto|dark|light`；
   auto 用 `COLORFGBG` 探测终端背景，失败回退 dark；TUI 与 REPL 共用同一套 token。
3. **即时切换**：`/theme`、`/settings → Theme…` 切换后，CSS、状态带、侧栏、转录、工具卡、
   提示行在同一帧内换成新外观；不丢会话、不打断流式输出、滚动位置不跳。
4. **i18n 基础设施**：纯 stdlib 的 dict catalog + `t()` / 复数 `tn()`；locale 支持 `auto|en|zh`；
   en 为权威、zh 缺键回退 en；key 集与占位符由测试强约束。
5. **界面可切换中文**：TUI 全部界面文案 + REPL + slash 命令描述 + 审批/通知/错误文案；
   默认 `auto`（`LANG=zh_CN.*` 自动中文，其余英文），默认行为与现有测试保持一致。
6. **CJK 宽度正确**：中文文案不破坏 TUI 单行/定宽契约（cell-based 裁剪替换 char-based `_clip`）。
7. **设置持久化**：`theme` / `language` 存 `~/.zenagent/settings.json` 的 `prefs`（沿用
   `search_provider` 先例），TUI 设置面板、slash 命令、CLI flag 三处都能改；flag 覆盖 pref。
8. 全部离线可测；`bash scripts/check.sh` 全绿。

**非目标（本期不做）**

- 完整 settings 注册表 / `zen config list|get|set` / config.toml 新键（单独议题）。
- 翻译模型面向内容：`zenagent/prompts/**`、工具描述与 schema、skill 正文。
- docs/README 的翻译（保持中文文档现状）。
- 第三方主题包 / 主题 JSON 自定义 / 热加载（omp 式，方案 C，留到以后；token 设计兼容）。
- OSC 11 查询终端背景、跟随 macOS/系统亮暗实时变化；`auto` 只看 `COLORFGBG`，启动时判定。
- CLI 子命令（`doctor`、`usage`、`export`、`sessions`、`--help`）的翻译：第二刀。
- 移除 REPL 的 `mono` / `solarized` / 自定义 JSON 主题（保留为 REPL-only legacy 逃生口）。
- 复数/性/格等完整 i18n 引擎；只做 one/other 二分支的数量短语。

**反目标（出现即视为失败）**

- `zenagent/app/*` import textual/rich 或 `zenagent/i18n` 之外引入 UI 依赖（分层不变）。
- Rich 文本里继续写死颜色（除 token 定义与 palette 绑定层）。
- 界面文案绕过 `t()`（module 级 import 期常量、f-string 拼接英文）继续扩散。
- 切换主题/语言导致 transcript 丢失、滚动跳底、流式中断，或需要重启才生效。
- 翻译命令名、flag、tool 名、模型可见 prompt；或 zh 下命令补全失效。
- CJK 文案把侧栏/状态带/浮层撑破（行宽、列对齐、单行契约）。
- 默认 locale=en 时任何现有行为/测试文本变化。

## 3. 约束

- 基线 main `76d564f`；TUI 单列 dock 结构（`#transcript` 1fr → `#status-band` → `#prompt-row`）不变。
- Textual 8.2.8：`Theme(name, ..., variables={...})` 可加自定义 CSS 变量，`App.register_theme()`
  必须在设置 `App.theme` 之前；`App.theme` 是 reactive，切换自动 invalidate CSS 并刷新；
  `$background/$surface/$text/$accent/$error/$success/$warning` 等内置变量由 Theme 字段派生，
  `variables` 在 color system 之后合并（可覆盖）。
- Rich Text 内写死的颜色不会跟随 CSS；切换必须显式重建（复用
  `TranscriptView.reset()` + `sync(chat.views, chat.status)`，状态带/侧栏本来就按
  `_refresh_status()` / 1s 轮询重建）。
- 无新依赖：只加 stdlib；`rich.cells.cell_len` 已在 `widgets/tool_card.py` 使用。
- `zenagent/app/*` 不 import textual/rich；i18n 模块是叶子模块，app 层可 import。
- `settings.json` 已有 `prefs` 通道（`get_pref/set_pref`，0600，写入 temp+rename）。
- 测试默认 en；现有 1486 个测试必须全部保持绿的语义。

## 4. 方案对比与选择

### 4.1 Theme

| 方案 | 做法 | 代价 / 风险 |
| --- | --- | --- |
| **A（采用）双外观统一 token** | `cli/appearance.py` 定义为唯一 token 源（dark/light 各 ~20 个语义 token）；TUI 由它注册 `harbor-dark`/`harbor-light` 两个 Textual Theme（tcss 全走 `$var`），palette 动态取当前外观；REPL rich 样式由同一 token 派生；切换走一次显式重建 | 要动 tcss、palette、REPL 三处；但一套色、亮暗不分裂，后续可平滑升级到 C |
| B 只给 TUI 加亮暗 | 新 Textual 双主题 + 启动时选 palette，REPL 不动 | 改动最小，但 TUI/REPL 仍是两套主题体系，`--theme` 语义继续分裂 |
| C omp 式可扩展主题 | `~/.zenagent/themes/<name>.json`（~40 token + 校验 + 热加载），内置 harbor dark/light | 扩展性最好，但 schema/校验/加载/选择器/文档都要做，这轮不划算；A 的 token 设计可以无痛升级到 C |

选 **A**。C 的反向兼容点：token 名字与语义在这版固定下来，将来 JSON 主题只是另一份 token 映射。

### 4.2 i18n

| 方案 | 做法 | 代价 / 风险 |
| --- | --- | --- |
| **1（采用）dict catalog + `t()`** | `zenagent/i18n/{__init__,en,zh}.py`；点分 key + named placeholder；en 权威、zh 回退；测试强约束 key 集/占位符 | 纯 stdlib、零构建；翻译是普通 Python dict，review 友好；缺键由测试兜住 |
| 2 gettext .po/.mo | 标准工具链（Poedit 等） | 仓库要带二进制 `.mo` 或加构建步骤，和"零依赖、离线可跑"的风格冲突 |
| 3 双语内联 `tr(en, zh)` | 代码里成对写两种文案，最省事 | 代码混双语、加第三语言难、review 噪音大；文案散落不可审计 |

选 **1**。复数只做 one/other 两分支（`tn()`），不做完整 ICU。

## 5. 详细设计

### 5.1 外观与 token 源

新增 `zenagent/cli/appearance.py`（无 UI 依赖，TUI/REPL/CLI 共用）：

```python
@dataclass(frozen=True)
class Appearance:
    name: str          # "dark" | "light"
    dark: bool
    tokens: Mapping[str, str]

APPEARANCES = {"dark": ..., "light": ...}   # 内置两套
def resolve_theme(spec: str | None, *, environ=None) -> Appearance  # auto 探测 + 非法值回退
def detect_dark(environ) -> bool            # COLORFGBG
```

token 表（这是 TUI/REPL 唯一颜色来源；数值已做 WCAG 校验，light 均 ≥4.5:1，dim 属 hint 级 ≥3:1）：

| token | dark | light | 用途 |
| --- | --- | --- | --- |
| `ink` | `#0d1319` | `#f7f5f0` | 页面底色 |
| `surface` | `#141c24` | `#efece4` | 状态带 / 侧栏 / 浮层表面 |
| `raised` | `#1b222a` | `#f0ece3` | 用户消息条 |
| `line` | `#263441` | `#d8d3c8` | 边框 / 分隔 |
| `text` | `#d7e0e8` | `#1f262c` | 主文本 |
| `muted` | `#8296a6` | `#55616a` | 次要文本 |
| `dim` | `#5d6f7e` | `#6b747c` | 三级提示 / 时间戳 |
| `accent` | `#d9a86c` | `#8a5a20` | 活动 / 焦点 / 强调 |
| `ok` | `#8fbf8f` | `#356b40` | 成功 / 新增 |
| `error` | `#e08787` | `#a63e36` | 失败 |
| `warn` | `#d9c06c` | `#7a5c1a` | 超时 / 截断 |
| `tool-running-bg` | `#121a22` | `#eaf1f4` | 运行中工具卡底 |
| `tool-ok-bg` | `#10171e` | `#ecf1e8` | 成功工具卡底 |
| `tool-error-bg` | `#241a1a` | `#f7e8e5` | 失败工具卡底 |
| `error-banner-bg` | `#3a1d1f` | `#f6dcda` | 错误横幅底 |
| `sec-session` | `#7fb3c9` | `#2a6478` | 分区色：Session |
| `sec-model` | `#9ab87a` | `#566f39` | 分区色：Model |
| `sec-runtime` | `#c9a97f` | `#7d5f28` | 分区色：Runtime |
| `sec-context` | `#7f9ec9` | `#39657f` | 分区色：Context |
| `sec-subagent` | `#b79ad0` | `#67527f` | 分区色：Subagent |
| `sec-jobs` | `#7fb398` | `#396f61` | 分区色：Jobs |

（`sec-todo` 直接复用 `accent`。）light 的 tint/bg 值第一版由设计给定，实现时用
`/theme` 在真终端里过一次，允许只调数值不改 token 名。

### 5.2 TUI 接入

- 新增 `zenagent/cli/tui/app_theme.py`：`textual_theme(appearance) -> textual.theme.Theme`，
  注册名 `harbor-dark` / `harbor-light`（加 `harbor-` 前缀避开 Textual 内置 `textual-*`）。
  Theme 字段映射：`background=ink`、`foreground=text`、`surface=surface`、`panel=surface`、
  `primary=accent`、`accent=accent`、`error=error`、`success=ok`、`warning=warn`、`dark=...`；
  `variables` 补 `ink / line / raised / muted / dim / tool-*-bg / error-banner-bg / sec-*`。
- `ZenAgentApp.__init__`：`super().__init__()` 后立刻 `register_theme()` 两个主题并按
  `resolve_theme()` 设置 `self.theme`，保证首帧就是正确外观（不能等 `on_mount` 再切，否则暗→亮闪一下）。
- `theme.tcss`：43 处 hex 全替换为变量（`#0d1319→$ink`、`#141c24→$surface`、`#1b222a→$raised`、
  `#121a22→$tool-running-bg`、`#10171e→$tool-ok-bg`、`#241a1a→$tool-error-bg`、
  `#3a1d1f→$error-banner-bg`、`#263441→$line`、`#d7e0e8→$text`、`#8296a6→$muted`、
  `#5d6f7e→$dim`、`#d9a86c→$accent`、`#e08787→$error`）。
- `palette.py` 改为**外观绑定**：
  - `bind(appearance)` 设置模块级当前 token 表；
  - `INK/TEXT/...` 通过模块级 `__getattr__` 按当前绑定解析（widget 在 render 时取色）；
  - `SECTION_ACCENTS` / `STATE_COLORS` / `CONTEXT_COLORS` 改为实时映射（或 bind 时重建），
    `state_colors` 里原来写死的 `#d9c06c` 收编为 `warn` token；
  - 现有 import 期快照（`status/segments.py:_TEXT`、`screens/todo_list.py:_TEXT` 等）
    必须改成调用期取色（这是本设计最容易漏的一处）。
- 测试 `tests/test_tui_palette.py` 改为绑定后的断言（不再是"常量就是这些 hex"）。

### 5.3 REPL 接入

- `cli/theme.py` 保留 `Theme` dataclass / JSON 加载（legacy，不破坏既有自定义主题），新增
  `theme_from_appearance(appearance) -> Theme`：`thinking=dim`、`diff_add=ok`、`diff_del=error`、
  `diff_hunk=accent`、`diff_meta="bold"`、`dim=dim`、`accent=accent`。
- `--theme` 语义：新增 `auto|dark|light`；旧的 `default|mono|solarized|<path.json>` 继续可用
  （REPL-only legacy）。TUI 只认 `auto|dark|light`，拿到 legacy 值时按 auto 处理并 warn 一次。
  这样不删用户已有的自定义主题文件，也不把 mono/solarized 塞进 TUI 选择器。
- REPL 的 `/theme` 命令：接受新值 + 持久化 pref；`/theme export` 之类保持现状。

### 5.4 自动亮暗与优先级

- `resolve_theme(spec)`：`spec` 为空或 `auto` → `detect_dark()`；`dark`/`light` 直接用；
  其他值 → 默认外观 + 一次 warning（不抛异常）。
- `detect_dark(environ)`：读 `COLORFGBG`（形如 `15;0` 或 `0`）：背景色 index `< 8` → dark，
  `>= 8` → light；缺失/非法 → dark。不查 OSC 11（首版）。
- 优先级：`--theme` flag > `settings.prefs["theme"]` > `auto`。`ZENAGENT_THEME` 作为
  无 pref 时的 CI 兜底（可选，实现者决定；我倾向加，便于测试/脚本）。
- 环境变量 `NO_COLOR` 不参与外观选择（它影响 rich 着色，不是亮暗）。

### 5.5 i18n 基础设施

新增 `zenagent/i18n/`：

```python
# __init__.py
def set_locale(code: str | None) -> str        # 规范化 + 记录；None/"auto" → 探测
def active_locale() -> str                     # "en" | "zh"
def t(key: str, **fmt) -> str                  # 当前 locale → en → key
def tn(key: str, n: int, **fmt) -> str         # 复数：key.one / key.other
def available() -> tuple[str, ...]
```

- catalog：`en.py` / `zh.py` 是扁平 `dict[str, str]`；key 点分命名：
  `tui.sidebar.session`、`tui.settings.search_provider`、`cmd.settings.description`、
  `perm.approval.title`、`status.ctx.total`、`repl.help.hint` ……
- 占位符用 `str.format` 的 named field（`{used}` / `{window}`），不用位置参数，便于翻译重排。
- 回退链：当前 locale → en → key 本身（运行时绝不抛）；测试里用 strict 模式断言 en/zh 完全对齐：
  key 集一致、每个 key 的占位符集合一致、`tn` 的 `.one/.other` 成对存在。
- locale 规范化：`zh`/`zh-cn`/`zh_cn`/`zh-hans*` → `zh`；`en*` → `en`；未知 → en。
- locale 解析优先级：`--lang` flag > `prefs["language"]` > `ZENAGENT_LANG` >
  `LANG`/`LC_ALL`/`LC_MESSAGES`（zh 前缀 → zh，其余 → en）；pref 的 `auto` 走环境探测。
- 切换：`set_locale()` 后由前端触发重建（见 5.9）；i18n 模块本身无 UI 依赖。
- 复数只做 one/other：中文两分支同文案即可（v1 只 en/zh）。

### 5.6 文案范围与不翻清单

**第一刀（本设计覆盖）**

| 面 | 位置 | 规模（启发式） |
| --- | --- | --- |
| TUI chrome | `cli/tui/app.py`、`widgets/*`、`screens/*`、`status/segments.py` | ~230 |
| TUI 设置/浮层 | `screens/{modals,secrets,palette,todo_list,context_breakdown,job_log}.py` | ~90 |
| slash 命令描述 | `app/commands.py` | ~70 |
| 审批/通知/状态 | `app/{permissions,chat,runtime,views,skill_commands,sessions,slash_args}.py` | ~120 |
| REPL | `cli/repl.py`、`cli/completion.py` | ~110 |

合计约 **450–500 个 key**（同一文案复用 key，实际少于字面量数）。

**不翻（明确排除）**

- `zenagent/prompts/**`、工具描述/schema、skill 正文、`plan_mode` 等模型面向文本。
- 命令名、flag 名、tool 名、keybinding 名、span/class id、模型 id、路径、代码块。
- `--help` argparse 文本、`doctor`/`usage`/`export`/`sessions` 子命令输出、crash/telemetry/RPC 协议错误
  （第二刀；这些是开发者/机器面向，英文本轮可接受）。
- docs/README（中文文档现状保留）。

### 5.7 CJK 宽度与单行契约

中文是双宽字符，`len()` 与终端列宽不等。现有：
`widgets/sidebar.py::_clip`、`screens/modals.py::_clip` 都是 char-based；`widgets/tool_card.py::_clip_cells`
已是 cell-based。

- 新增 `zenagent/cli/tui/textutil.py::clip_cells(text, width, ellipsis="…")`（基于 `rich.cells.cell_len`），
  sidebar / modals / context 行 / job log 等一律用它；tool_card 的私有实现收编。
- 审查所有对展示文本用 `len()` 的地方（状态带宽度、command hints、rows 行宽、浮层 body 折行），
  换成 `cell_len`。
- 测试：`clip_cells("中文测试", 5) == "中文…"`；侧栏/状态带/审批浮层在 zh 下渲染宽度不超限
  （`cell_len(row) <= width`），继续覆盖 polish 的侧栏宽度契约。

### 5.8 设置项、命令与 flag

- `settings.json`：`prefs.theme ∈ {auto,dark,light}`，`prefs.language ∈ {auto,en,zh}`；
  读的时候做规范化与回退，不合法值 warn + 用默认（不静默改写用户文件）。
- TUI `/settings` 菜单新增两项（复用 `ChoicePicker`）：
  `Theme…（auto/dark/light）`、`Language…（auto/en/zh）`；选中即持久化 + 即时重建。
- slash 命令：`/theme [auto|dark|light]`、`/lang [auto|en|zh]` 进共享命令注册表
  （TUI 目前没有 `/theme`；REPL 已有特殊实现，统一到注册表或让两边共享同一个 handler）。
  `app/slash_args.py` 增加两组 picker 值，补全自动生效。
- CLI flag：`--theme` 扩展取值（`auto|dark|light` + legacy）；新增 `--lang auto|en|zh`；
  两者都覆盖 pref，只影响本进程。
- REPL 启动时：`resolve_theme(pref/flag)` → `theme_from_appearance()`；`set_locale()` 在构建
  renderer/commands 之前调用（避免命令描述先被英文构建）。

### 5.9 切换生效（repaint pipeline）

同一套机制服务 theme 与 language，集中在 `ZenAgentApp`：

```python
def _apply_appearance(self, appearance):      # theme 变化
    palette.bind(appearance.tokens)
    self.theme = appearance.textual_name       # 触发 CSS 重算
    self._repaint_rich()                       # Rich 内容重建

def _apply_language(self, spec):               # language 变化
    set_locale(resolve_language(spec))
    self.commands = build_default_commands(...)  # 命令描述重建（很便宜）
    self._repaint_rich()

def _repaint_rich(self):
    transcript = self.query_one(TranscriptView)
    transcript.reset()
    transcript.sync(self.chat.views, self.chat.status)
    self._refresh_status()                     # 状态带 + 侧栏 + work strip
    self._refresh_prompt_chrome()              # 输入框 placeholder / hint 等
```

- `reset()+sync()` 会重新挂载并渲染全部历史（welcome 行由 `sync`/`reset` 的既有逻辑处理，
  实现时确认一次）；切换只发生在用户操作时，代价可接受。
- 不打断运行：agent 若正在流式输出，重建后后续事件继续走既有 `sync` 增量路径；
  切换动作本身不 abort、不改 queue。
- 侧栏/状态带本来就按 1s/`_refresh_status()` 重建，不需要额外通知。
- 失败兜底：`_apply_*` 包一层 try/except，重建失败时保留旧外观并 notify（不让主题切换崩 app）。

### 5.10 错误与降级

- 非法 `theme`/`language`（pref、flag 或命令参数）：warn 一次、回退 `auto`，不改写 settings.json。
- `COLORFGBG` 缺失/格式错：dark。
- catalog 缺 key：当前 locale → en → key；strict 测试保证发布前不缺。
- `settings.json` 损坏：沿用现有 `settings.problem` 路径（`/settings` 显示、env 兜底），
  theme/language 用默认值。
- Textual theme 注册冲突/未知：启动时 assert `harbor-dark/harbor-light` 存在，失败回退
  `textual-dark`/`textual-light` 并 warn（不 crash）。

## 6. 验收与测试

**theme**

- `tests/test_appearance.py`：token 表完整性（dark/light key 集一致）、WCAG 对比度
  （text/muted/accent/ok/error/warn vs ink/surface ≥4.5，dim ≥3.0）、`resolve_theme` 优先级与
  非法值回退、`detect_dark` 的 COLORFGBG 分支（`"15;0"`→dark、`"0;15"`→light、缺失→dark）。
- `tests/test_tui_appearance.py`：pilot 下两个主题注册成功、`app.theme` 正确、CSS 变量解析出
  对应 hex；切主题后转录行/状态带取到新颜色（采样几处 `Text.spans`/widget 渲染）；
  "tcss 不含字面 hex" 的规范测试（扫描 `theme.tcss` 与 `widgets/*.py` 的 `DEFAULT_CSS`）。
- REPL：`theme_from_appearance` 映射正确；旧 `mono/solarized/<path>.json` 仍能加载。
- 回归：切主题后 `test_tui_rows` 的折叠/单行契约、跟随滚动不回归。

**i18n**

- `tests/test_i18n.py`：en/zh key 集一致、占位符一致、`.one/.other` 成对；`t()` 回退链；
  strict 模式缺 key 报错；locale 规范化与优先级；`tn` 英文 one/other。
- `tests/test_i18n_tui.py`：zh 下 Settings 标题、侧栏分区、状态带、审批浮层、welcome、
  `/help` 输出；CJK 宽度不变量（侧栏/状态带渲染 cell 宽 ≤ 限）。
- 回归：默认 en 下现有 1486 个测试全部保持通过（这是硬约束）；命令补全在 zh 下仍出现
  命令名（命令名不翻）。

**闸门**：每个切片结束 `bash scripts/check.sh` 全绿。

## 7. 实施切片

单个 worktree/分支（`feat/i18n-theme`，从合入后的 main 切），按提交切片：

- **S1 外观基础设施**：`cli/appearance.py` + token 表 + `app_theme.py` + tcss 变量化 +
  palette 动态绑定 + 修复 import 期颜色快照 + REPL 派生 + `--theme`/pref/`/theme` + 自动探测 +
  repaint pipeline（theme 部分）+ 测试。
- **S2 i18n 基础设施 + TUI 文案**：`zenagent/i18n/` + `t/tn` + 语言解析/持久化 + TUI 全部文案
  （app/widgets/screens/segments）+ `textutil.clip_cells` 与宽度修复 + `/lang` + 测试。
- **S3 共享层 + REPL 文案**：`app/{commands,permissions,chat,runtime,views,...}` + `cli/repl.py`
  + 命令描述 + `--lang` flag + 补全 meta + 测试。
- **S4 文档与收尾**：`docs/guide/{config,cli,tui}.md` 的设置项/主题/语言章节、feature-map、
  CHANGELOG（下一版本）、`/settings` 帮助文案、i18n 术语表（en/zh 对照，便于后续扩充）。

S1/S2 可并行度低（palette/tcss 与 TUI 文案同一批文件），建议顺序做；S3 依赖 S2 的 `t()`。

## 8. 风险与回退

- **repaint 回归**（滚动/跟随/折叠行高）：S1 先补 pilot 测试；如出现闪烁/跳底，回退方案是
  "主题切换在下一帧全量重建"（已有 reset/sync 路径），不改变交互。
- **light 配色实际观感**：token 数值可调，不改结构；必要时先只交付 dark 的 token 化。
- **CJK 宽度遗漏点**：S2 用 `grep -n "len("` 做一次展示文本审计，加 zh 宽度测试兜住。
- **翻译量**（~450–500 key）：按面切片提交，每片自带 zh 填充；不追求一次翻完美，先可用后润色。
- **测试文本耦合**：默认 en 不变是硬约束；任何 en 文案改动按普通改动处理（该改测试就改）。
- **legacy 主题兼容**：REPL 旧主题继续加载；TUI 不认的值按 auto + warn，不 crash。

## 9. 留给实现者的决定

- palette 动态实现细节（模块 `__getattr__` vs bind 时重建字典）——两者都满足契约。
- zh 文案的具体措辞/术语表（设计给方向，实现时定稿；命令名、flag、状态枚举值不翻）。
- `/theme`、`/lang` 是否走共享注册表（TUI 与 REPL 统一）还是各自 handler——建议统一。
- `ZENAGENT_THEME` / `ZENAGENT_LANG` 是否提供（我倾向提供，便于 CI 与脚本）。
- light 的 tint 值（`raised`/`tool-*-bg`/`error-banner-bg`）第一版设计给定，允许实现时微调。
