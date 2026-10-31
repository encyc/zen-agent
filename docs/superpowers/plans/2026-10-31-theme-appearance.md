# Theme / Appearance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 ZenAgent 一套单一颜色 token 源，TUI 与 REPL 都从它派生，支持 `auto|dark|light` 三态、即时切换、`--theme`/`/theme`/设置面板三处可改，并把所有散落 hex 收进 token 源。

**Architecture:** 新增 `zenagent/cli/appearance.py`（无 UI 依赖的 token 源 + 自动探测）。TUI 由 `zenagent/cli/tui/app_theme.py` 把它翻译成两个 Textual Theme（`harbor-dark`/`harbor-light`），`theme.tcss` 与各 widget 的 `DEFAULT_CSS` 全部改用 CSS 变量；`zenagent/cli/tui/palette.py` 改成按当前外观动态取色（模块 `__getattr__` + 实时 Mapping）。REPL 的 rich 主题由 `cli/theme.py::theme_from_appearance` 从同一套 token 派生，旧的 `mono`/`solarized`/JSON 主题保留为 legacy。切换主题时 CSS 由 Textual 自动重算，Rich 文本走一次显式重建（`transcript.reset()+sync()`）。

**Tech Stack:** Python 3.11+、Textual 8.2.8（`Theme`/`register_theme`/CSS 变量）、rich（`cell_len`、`Style`）、pytest + Textual `run_test()`。

**Spec:** `docs/superpowers/specs/2026-10-31-i18n-theme-design.md`（已批准，theme 方案 A）

## Global Constraints

- 无新依赖；只用 stdlib + 现有 `textual`/`rich`。
- `zenagent/app/*` 不得 import textual/rich；`zenagent/cli/appearance.py` 也不得 import textual/rich。
- 默认外观必须与现在完全一致：dark 的 token 值就是当前 `palette.py`/`theme.tcss` 里的 hex。
- 默认 locale/语言行为不受本计划影响；i18n 是后续独立计划。
- 每个 task 结束必须跑该 task 的测试 + `bash scripts/check.sh`（ruff + 全量 offline pytest）。
- 分支工作区：本 worktree；commit 信息按仓库既有风格（`feat(tui): …` / `refactor(tui): …` / `test: …` / `docs: …`）。

## Review Focus

这些是 spec 隐含、最容易在真实终端上咬人的输入，各自有 task 里的测试钉住：

1. `COLORFGBG` 缺失、乱码或形如 `"15;0"`/`"0;15"`：`auto` 必须稳定回退 dark，绝不 crash（Task 1）。
2. `--theme nonsense` / settings 里存了非法值：warn 一次 + 回退 auto，app 照常启动（Task 3/5/6）。
3. Textual 内置变量碰撞：`$surface`/`$accent`/`$error`/`$success` 必须解析成我们的 token，而不是 Textual 默认色（Task 3/4）。
4. 运行中切主题：不 abort 流式输出、不丢 transcript、滚动仍贴尾（Task 5）。
5. light 主题的深色文本可读性：`dim` 至少 3:1、正文/状态色 ≥4.5:1（Task 1）。

---

### Task 1: appearance token 源

**Files:**
- Create: `zenagent/cli/appearance.py`
- Test: `tests/test_appearance.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True, slots=True) class Appearance: name: str; dark: bool; tokens: Mapping[str, str]`
  - `APPEARANCES: dict[str, Appearance]`（键 `"dark"`/`"light"`）
  - `THEME_CHOICES: tuple[str, ...] = ("auto", "dark", "light")`
  - `detect_dark(environ: Mapping[str, str] | None = None) -> bool`
  - `resolve_theme(flag: str | None = None, *, pref: str | None = None, environ: Mapping[str, str] | None = None, warnings: list[str] | None = None) -> Appearance`
- token 键（21 个，dark/light 完全一致）：`ink surface raised line text muted dim accent ok error warn tool-running-bg tool-ok-bg tool-error-bg error-banner-bg sec-session sec-model sec-runtime sec-context sec-subagent sec-jobs`（`sec-todo` 不单列，复用 accent）。
- 取值见 spec §5.1 的 token 表（dark 原值 + light 值），`warn` light 用 `#7a5c1a`。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_appearance.py
import pytest

from zenagent.cli.appearance import APPEARANCES, THEME_CHOICES, detect_dark, resolve_theme


def _luminance(value: str) -> float:
    value = value.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _ratio(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


def test_both_appearances_define_the_same_tokens():
    dark, light = APPEARANCES["dark"], APPEARANCES["light"]
    assert set(dark.tokens) == set(light.tokens)
    assert len(dark.tokens) >= 21
    assert dark.dark is True and light.dark is False
    assert THEME_CHOICES == ("auto", "dark", "light")


def test_tokens_are_lowercase_hex():
    for appearance in APPEARANCES.values():
        for name, value in appearance.tokens.items():
            assert value.islower() and len(value) == 7 and value.startswith("#"), (appearance.name, name)


def test_dark_tokens_keep_todays_values():
    tokens = APPEARANCES["dark"].tokens
    assert tokens["ink"] == "#0d1319"
    assert tokens["surface"] == "#141c24"
    assert tokens["accent"] == "#d9a86c"
    assert tokens["text"] == "#d7e0e8"


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"COLORFGBG": "15;0"}, True),   # fg bright, bg black → dark terminal
        ({"COLORFGBG": "0;15"}, False),  # bg bright → light terminal
        ({"COLORFGBG": "0"}, True),      # only bg index, black
        ({"COLORFGBG": "15"}, False),    # only bg index, white
        ({}, True),
        ({"COLORFGBG": "junk"}, True),
    ],
)
def test_detect_dark_uses_colorfgbg_background(environ, expected):
    assert detect_dark(environ) is expected


def test_resolve_theme_prefers_flag_then_pref_then_auto():
    assert resolve_theme("light", pref="dark").name == "light"
    assert resolve_theme(None, pref="light").name == "light"
    assert resolve_theme(None, pref=None, environ={"COLORFGBG": "0;15"}).name == "light"
    assert resolve_theme("auto", environ={"COLORFGBG": "0;15"}).name == "light"


def test_resolve_theme_warns_once_and_falls_back():
    warnings: list[str] = []
    appearance = resolve_theme("nonsense", warnings=warnings)
    assert appearance.name == "dark"
    assert len(warnings) == 1 and "nonsense" in warnings[0]


def test_light_text_colours_meet_contrast_on_surface():
    tokens = APPEARANCES["light"].tokens
    surface = tokens["surface"]
    for name in ("text", "muted", "accent", "ok", "error", "warn"):
        assert _ratio(tokens[name], surface) >= 4.5, name
    assert _ratio(tokens["dim"], surface) >= 3.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_appearance.py -q`
Expected: FAIL (`ModuleNotFoundError: zenagent.cli.appearance`)

- [ ] **Step 3: Implement `zenagent/cli/appearance.py`**

`resolve_theme` 语义：`flag` 与 `pref` 依次尝试，第一个非空值被解析；`auto`/空/缺省 → `detect_dark()`；`dark`/`light` 直接取表；其他值 → `warnings.append(f"unknown theme {raw!r} (choose auto|dark|light); using auto")` 后按 auto 处理。`detect_dark` 取 `COLORFGBG` 最后一个 `;` 分段当背景索引，`< 8` → dark，其他/缺失/非法 → dark。模块 docstring 说明"TUI/REPL 唯一颜色来源"。

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_appearance.py -q` → PASS
Run: `bash scripts/check.sh` → 全绿

- [ ] **Step 5: Commit**

```bash
git add zenagent/cli/appearance.py tests/test_appearance.py
git commit -m "feat(cli): appearance token source with dark/light and auto detection"
```

---

### Task 2: palette 改为外观绑定

**Files:**
- Modify: `zenagent/cli/tui/palette.py`
- Modify: `zenagent/cli/tui/status/segments.py`（删掉 `_TEXT/_MUTED/_ACCENT/_ERROR` 快照，改调用期取色）
- Modify: `zenagent/cli/tui/screens/todo_list.py`（删掉 `_TEXT/_MUTED/_DONE/_BLOCKED/_ACCENT` 快照）
- Modify: `zenagent/cli/tui/widgets/sidebar.py`（若存在模块级 `palette.*` 快照，一并改）
- Test: `tests/test_tui_palette.py`

**Interfaces:**
- Produces: `palette.bind(appearance: Appearance) -> None`；模块级 `__getattr__` 提供 `INK/SURFACE/LINE/TEXT/MUTED/DIM/ACCENT/OK/ERROR/WARN`；`SECTION_ACCENTS`/`STATE_COLORS`/`CONTEXT_COLORS` 是实时 `Mapping`（每次取值读当前绑定）。
- Consumes: Task 1 的 `Appearance`/`APPEARANCES`。

- [ ] **Step 1: Update the failing tests**

在 `tests/test_tui_palette.py` 里：结构测试（section/state 覆盖）保留；hex 断言改为"绑定 dark 时等于 dark token"、"绑定 light 时等于 light token"；新增 live-map 测试：

```python
def test_bind_switches_palette_tokens():
    from zenagent.cli.appearance import APPEARANCES
    palette.bind(APPEARANCES["dark"])
    assert palette.TEXT == "#d7e0e8"
    palette.bind(APPEARANCES["light"])
    assert palette.TEXT == "#1f262c"
    assert palette.SECTION_ACCENTS["Session"] == "#2a6478"
    assert palette.STATE_COLORS["timed_out"] == "#7a5c1a"


def test_state_and_context_maps_track_the_binding():
    from zenagent.cli.appearance import APPEARANCES
    palette.bind(APPEARANCES["light"])
    assert palette.CONTEXT_COLORS["system"] == palette.SECTION_ACCENTS["Session"]
    palette.bind(APPEARANCES["dark"])  # restore for the other tests
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_palette.py -q`
Expected: FAIL（`bind` 不存在 / light 值不符）

- [ ] **Step 3: Implement the dynamic palette**

要点：删除 palette 里所有 hex 字面量（值全部来自 `appearance.tokens`）；`_BASE = {"INK": "ink", ...}` + 模块 `__getattr__`；三张语义表用读当前 token 的 `Mapping` 实现；模块 import 时默认 `bind(APPEARANCES["dark"])`，保证未显式绑定的旧测试/工具仍有合理默认。`STATE_COLORS["timed_out"/"truncated"]` 用 `warn` token。修掉所有模块级 `palette.*` 快照（`grep -n "palette\." zenagent/cli/tui -r` 逐个确认它在函数体内）。

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_tui_palette.py tests/test_tui_sidebar.py tests/test_tui_todo.py tests/test_tui_status_band.py -q` → PASS
Run: `bash scripts/check.sh` → 全绿

- [ ] **Step 5: Commit**

```bash
git add zenagent/cli/tui/palette.py zenagent/cli/tui/status/segments.py zenagent/cli/tui/screens/todo_list.py zenagent/cli/tui/widgets/sidebar.py tests/test_tui_palette.py
git commit -m "refactor(tui): bind the semantic palette to an appearance"
```

---

### Task 3: Textual theme 桥 + TUI 启动接线

**Files:**
- Create: `zenagent/cli/tui/app_theme.py`
- Modify: `zenagent/cli/tui/app.py`（`__init__` 增 `theme: str | None = None`；注册主题、设置 `self.theme`、`palette.bind`）
- Test: `tests/test_tui_app_theme.py`

**Interfaces:**
- Produces:
  - `DARK_THEME_NAME = "harbor-dark"`, `LIGHT_THEME_NAME = "harbor-light"`
  - `textual_theme(appearance: Appearance) -> Theme`
  - `theme_name(appearance: Appearance) -> str`
- Consumes: Task 1、Task 2。
- Theme 字段映射：`background=ink`、`foreground=text`、`surface=surface`、`panel=surface`、`primary=accent`、`accent=accent`、`error=error`、`success=ok`、`warning=warn`、`dark=appearance.dark`；自定义变量：`ink/raised/line/muted/dim/ok/tool-running-bg/tool-ok-bg/tool-error-bg/error-banner-bg`。两个主题都必须注册；`self.theme` 在 `__init__` 里就设置（首帧即正确，不能等 on_mount 闪一下）。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tui_app_theme.py
from zenagent.cli.appearance import APPEARANCES
from zenagent.cli.tui import palette


def test_textual_theme_carries_both_fields_and_variables():
    from zenagent.cli.tui.app_theme import textual_theme

    dark = textual_theme(APPEARANCES["dark"])
    assert dark.name == "harbor-dark"
    assert dark.dark is True
    assert dark.background == "#0d1319"
    assert dark.foreground == "#d7e0e8"
    assert dark.variables["ink"] == "#0d1319"
    assert dark.variables["tool-running-bg"] == "#121a22"
    light = textual_theme(APPEARANCES["light"])
    assert light.name == "harbor-light" and light.dark is False
    assert light.variables["ink"] == "#f7f5f0"


async def test_app_starts_with_the_configured_theme(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp

    app = ZenAgentApp(fake_runtime, theme="light")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == "harbor-light"
        assert palette.INK == "#f7f5f0"


async def test_app_defaults_to_dark_without_a_theme(fake_runtime, monkeypatch):
    from zenagent.cli.tui.app import ZenAgentApp

    monkeypatch.setattr("zenagent.settings.get_pref", lambda name, default=None: None)
    app = ZenAgentApp(fake_runtime)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == "harbor-dark"
```

（`fake_runtime` 是 `tests/conftest.py` 现有 fixture；monkeypatch `zenagent.settings.get_pref` 是为了不读开发机上的真实 settings.json。）

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_app_theme.py -q`
Expected: FAIL（`app_theme` 不存在 / 主题未设置）

- [ ] **Step 3: Implement bridge + startup wiring**

`app.py` `__init__` 在 `super().__init__()` 之后：用 `resolve_theme(theme, pref=settings.get_pref("theme"))` 解析，注册两个 theme，设置 `self.theme`，`palette.bind(self._appearance)`；把启动 warning 存起来（`self._appearance_warnings`），在 `on_mount` 里用 `transcript.append_notice(...)` 展示。注意 `register_theme` 必须在 `self.theme = ...` 之前调用。

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_tui_app_theme.py tests/test_tui_palette.py -q` → PASS
Run: `bash scripts/check.sh` → 全绿

- [ ] **Step 5: Commit**

```bash
git add zenagent/cli/tui/app_theme.py zenagent/cli/tui/app.py tests/test_tui_app_theme.py
git commit -m "feat(tui): register harbor themes and drive the palette from the active appearance"
```

---

### Task 4: CSS 全面 token 化

**Files:**
- Modify: `zenagent/cli/tui/theme.tcss`（43 处 hex → 变量）
- Modify: `zenagent/cli/tui/screens/*.py`、`zenagent/cli/tui/app.py`、`zenagent/cli/tui/widgets/*.py` 的 `DEFAULT_CSS` 里的 hex
- Test: `tests/test_tui_theme_css.py`

**Interfaces:**
- 映射：`#0d1319→$ink`、`#141c24→$surface`、`#1b222a→$raised`、`#263441→$line`、`#d7e0e8→$foreground`、`#8296a6→$muted`、`#5d6f7e→$dim`、`#d9a86c→$accent`、`#e08787→$error`、`#8fbf8f→$success`、`#d9c06c→$warning`、`#121a22→$tool-running-bg`、`#10171e→$tool-ok-bg`、`#241a1a→$tool-error-bg`、`#3a1d1f→$error-banner-bg`。
- Consumes: Task 3 注册的 theme variables。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_theme_css.py
import re
from pathlib import Path

TIU = Path(__file__).resolve().parents[1] / "zenagent" / "cli" / "tui"
HEX = re.compile(r"#[0-9a-fA-F]{6}")


def test_no_hardcoded_hex_in_tui_styles():
    offenders = []
    for path in [*TIU.rglob("*.py"), *TIU.rglob("*.tcss")]:
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if HEX.search(line):
                offenders.append(f"{path.relative_to(TIU)}:{number}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


async def test_theme_variables_resolve_to_tokens(fake_runtime):
    from zenagent.cli.appearance import APPEARANCES
    from zenagent.cli.tui.app import ZenAgentApp

    app = ZenAgentApp(fake_runtime, theme="light")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme_variables["ink"] == APPEARANCES["light"].tokens["ink"]
        assert app.theme_variables["surface"] == APPEARANCES["light"].tokens["surface"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_theme_css.py -q`
Expected: FAIL（列出所有 hex）

- [ ] **Step 3: Replace every hex**

先跑一次 Step 2 拿到完整清单，再逐文件替换（`theme.tcss` + `DEFAULT_CSS` 字符串）；`screens/palette.py` 的整段 CSS 也要替换。不要改 CSS 布局/尺寸，只换颜色值。替换后 `grep -rn "#[0-9a-fA-F]\{6\}" zenagent/cli/tui` 应为空。

- [ ] **Step 4: Run tests + visual sanity**

Run: `.venv/bin/python -m pytest tests/test_tui_theme_css.py tests/test_tui_sidebar.py tests/test_tui_rows.py tests/test_tui_job_log.py -q` → PASS
Run: `bash scripts/check.sh` → 全绿
（dark 的 token 值就是原 hex，所以现有快照/颜色断言应无缝通过。）

- [ ] **Step 5: Commit**

```bash
git add zenagent/cli/tui tests/test_tui_theme_css.py
git commit -m "refactor(tui): drive every stylesheet colour from theme variables"
```

---

### Task 5: 切换主题（命令 + 设置面板 + 即时重绘）

**Files:**
- Modify: `zenagent/app/commands.py`（`CommandResult.appearance` + `/theme` handler）
- Modify: `zenagent/app/slash_args.py`（`/theme` 值改为 auto/dark/light）
- Modify: `zenagent/cli/tui/app.py`（`set_appearance`、`_repaint_rich`、`theme-picker` 分支、设置面板入口、dispatch 处理 `result.appearance`）
- Modify: `zenagent/cli/tui/screens/modals.py`（`SettingsPicker` 增加 `Theme…` 行）
- Test: `tests/test_tui_app_theme.py`（追加）、`tests/test_commands.py` 或现有命令测试（追加）

**Interfaces:**
- Produces: `ZenAgentApp.set_appearance(spec: str) -> str`（返回生效的 appearance 名；`spec` 只接受 `auto|dark|light`，持久化 `settings.set_pref("theme", …)`）；
- Produces: `ZenAgentApp._repaint_rich() -> None`（`transcript.reset()` + `sync(chat.views, chat.status)` + `_refresh_status()`）；
- Consumes: Task 3 的桥。

- [ ] **Step 1: Write the failing tests**

```python
async def test_theme_command_switches_and_persists(fake_runtime, monkeypatch):
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.widgets.transcript import TranscriptView

    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "zenagent.settings.set_pref", lambda name, value: saved.append((name, value))
    )
    app = ZenAgentApp(fake_runtime)
    async with app.run_test() as pilot:
        await pilot.pause()
        reset_calls = []
        transcript = app.query_one(TranscriptView)
        original_reset = transcript.reset
        monkeypatch.setattr(transcript, "reset", lambda: (reset_calls.append(1), original_reset())[1])
        await app._dispatch_command("theme", "light")
        await pilot.pause()
        assert app.theme == "harbor-light"
        assert reset_calls, "switching must rebuild the rich transcript"
        assert ("theme", "light") in saved


async def test_theme_command_rejects_unknown_value(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp

    app = ZenAgentApp(fake_runtime)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._dispatch_command("theme", "nonsense")
        await pilot.pause()
        assert app.theme == "harbor-dark"


def test_theme_slash_values():
    from zenagent.app.slash_args import argument_values

    values = [value for value, _ in argument_values("/theme")]
    assert values == ["auto", "dark", "light"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_app_theme.py -q` → FAIL

- [ ] **Step 3: Implement**

- `CommandResult` 加 `appearance: str | None = None`（共享层，TUI/REPL 都能用；本计划只接 TUI）。
- `commands.py` 加 `@_command("theme", "set the colour theme: auto|dark|light")`：空参数 → `screen="theme-picker"`；不在 `THEME_CHOICES` → message 报错；合法 → `CommandResult(appearance=requested)`。
- `app.py`：`set_appearance` 解析→持久化→`self._appearance=…`→`self.theme=theme_name(...)`→`palette.bind(...)`→`_repaint_rich()`；`_dispatch_command` 增加 `theme-picker` 分支（`open_theme_picker()`，用 `argument_values("/theme")` + `ChoicePicker`）和 dispatch 后的 `result.appearance` 处理；`open_settings` 菜单加 `Theme…` 行（note=当前值）。
- 重绘用 `try/except` 兜底：失败保留旧外观并 `notify`。

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_tui_app_theme.py tests/test_tui_settings.py tests/test_commands.py -q` → PASS
Run: `bash scripts/check.sh` → 全绿

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/commands.py zenagent/app/slash_args.py zenagent/cli/tui/app.py zenagent/cli/tui/screens/modals.py tests/test_tui_app_theme.py
git commit -m "feat(tui): switch themes from /theme and the settings panel"
```

---

### Task 6: CLI flag + REPL 接入

**Files:**
- Modify: `zenagent/cli/main.py`（`--theme` help；TUI 构造处传 `theme=args.theme`）
- Modify: `zenagent/cli/theme.py`（`theme_from_appearance`、`resolve_theme_spec`）
- Modify: `zenagent/cli/repl.py`（启动主题解析 + `/theme` 新值 + 持久化 + usage 文案）
- Test: `tests/test_theme.py`、`tests/test_repl_logic.py`（追加）

**Interfaces:**
- Produces（`zenagent/cli/theme.py`）：
  - `theme_from_appearance(appearance: Appearance) -> Theme`（`thinking=dim`、`diff_add=ok`、`diff_del=error`、`diff_hunk=accent`、`diff_meta="bold"`、`dim=dim`、`accent=accent`）
  - `LEGACY_THEME_NAMES: frozenset[str] = frozenset({"default", "mono", "solarized"})`
  - `resolve_theme_spec(spec: str | None = None, *, pref: str | None = None, warnings: list[str] | None = None) -> Theme`：legacy 名/存在的 JSON 路径 → `load_theme`；否则 `theme_from_appearance(resolve_theme(spec, pref=pref, warnings=warnings))`。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_theme.py 追加
def test_theme_from_appearance_maps_tokens():
    from zenagent.cli.appearance import APPEARANCES
    from zenagent.cli.theme import theme_from_appearance

    theme = theme_from_appearance(APPEARANCES["light"])
    tokens = APPEARANCES["light"].tokens
    assert theme.diff_add == tokens["ok"]
    assert theme.diff_del == tokens["error"]
    assert theme.diff_hunk == tokens["accent"]
    assert theme.dim == tokens["dim"]


def test_resolve_theme_spec_keeps_legacy_names(tmp_path):
    from zenagent.cli.theme import resolve_theme_spec

    assert resolve_theme_spec("mono").name == "mono"
    assert resolve_theme_spec("auto", pref="light").diff_add == "#356b40"
```

```python
# tests/test_repl_logic.py 追加（按该文件既有 fake 风格）
def test_set_theme_accepts_the_new_vocabulary(...):
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_theme.py tests/test_repl_logic.py -q` → FAIL

- [ ] **Step 3: Implement**

- `theme.py` 加两个新 helper；`Theme` dataclass 不变（保持 legacy JSON 兼容）。
- `repl.py`：启动用 `resolve_theme_spec(theme, pref=settings.get_pref("theme"), warnings=theme_warnings)`；`set_theme(spec)` 对 `auto|dark|light` 走 `resolve_theme_spec` + `settings.set_pref("theme", spec)`，legacy 仍走 `load_theme`；`/theme` usage 文案改为 `auto|dark|light`（并说明 legacy 仍可用）。
- `main.py`：`--theme` help 更新为 `"colour theme: auto|dark|light (repl also accepts default|mono|solarized|<path.json>)"`；TUI 构造 `ZenAgentApp(runtime, theme=args.theme)`。

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_theme.py tests/test_repl_logic.py tests/test_cli.py -q` → PASS
Run: `bash scripts/check.sh` → 全绿

- [ ] **Step 5: Commit**

```bash
git add zenagent/cli/theme.py zenagent/cli/repl.py zenagent/cli/main.py tests/test_theme.py tests/test_repl_logic.py
git commit -m "feat(cli): theme flag and repl theme share the appearance tokens"
```

---

### Task 7: 文档 + 全量闸门

**Files:**
- Modify: `docs/guide/config.md`（settings.json 的 `theme` pref、`--theme` 优先级）
- Modify: `docs/guide/cli.md`（`--theme` 值、light/dark/auto 行为）
- Modify: `docs/guide/tui.md`（`/theme`、设置面板 Theme…、即时切换、legacy 主题说明）
- Modify: `docs/feature-map.md`（TUI 行加 theme）
- Modify: `CHANGELOG.md`（Unreleased：theme）
- Test: 无新测试；跑全量

- [ ] **Step 1: Write the docs**（按上面文件各加一小节，链接到 spec `docs/superpowers/specs/2026-10-31-i18n-theme-design.md`）

- [ ] **Step 2: Full gate**

Run: `bash scripts/check.sh`
Expected: `passed` 全绿（预计 ≈1500+ tests）

- [ ] **Step 3: Commit**

```bash
git add docs CHANGELOG.md
git commit -m "docs: theme and appearance configuration"
```
