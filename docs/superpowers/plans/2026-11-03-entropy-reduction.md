# 熵减重构(ZenAgentApp 拆解等)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `ZenAgentApp` 的十摊职责、`ChatController` 的三块状态、`Runtime` 的 metrics 各自拆成有条理的模块,行为零变化。

**Architecture:** TUI 侧照仓库既有惯例拆——真组件进 `screens/`、`widgets/`,必须挂 App 实例的动作/状态进新包 `cli/tui/app_parts/` 的分域 mixin;`_dispatch_command` 的 elif 链换 `SCREEN_OPENERS` 注册表字典并配守卫测试。App 层抽三个纯状态对象(`PendingQueue`/`Attachments`/`SubagentTracker`);Runtime 抽 `MetricsSession`。验收是结构清单不是行数(spec §裁决记录)。

**Tech Stack:** Python 3.11+,Textual 8.2.x,pytest;闸门 `bash scripts/check.sh`(= uv sync + ruff + pytest)。

**Spec:** `docs/superpowers/specs/2026-11-03-entropy-reduction-design.md`

## Global Constraints

- 每个提交 `bash scripts/check.sh` 全绿;提交信息用仓库 conventional 风格(`refactor(tui): …`、`chore: …`)。
- 行为零变化:按键、命令、i18n 文案、启动顺序、TUI 布局都不动。
- **公开面冻结**:37 个测试文件引用的 `ZenAgentApp` 方法名、38 处内部方法调用,以及测试直接读写的属性(`app._viewing`、`app._menus`、`app._theme_spec`、`app._language_spec`、`app._appearance_warnings`、`app._language_warnings`、`app._work_timer`、`app._sidebar_width`、`app._hint_*`、`app._sidebar_*`)必须继续从实例直达——mixin 继承天然满足;组件对象须留同名委托/property。改测试只许改 import,不许改断言。
- `BINDINGS` 列表留在 `app.py` 不动;方法内懒 import screen 的写法保持;omp 研究注释随代码走;`zenagent/__init__.py` 导出不变。
- 内核五层(`zenagent/types|events|providers|agent|tools`)不动;不新增依赖。
- mixin 无同名方法,基类顺序按 Task 3 固定;每个 mixin 文件顶部带一行职责注释。

## Review Focus

- **新 screen 键漏注册**:未来往 `app/commands.py` 加 `screen="..."` 而 `SCREEN_OPENERS` 没有条目 → 命令静默失灵。测试:Task 2 的双向守卫(注册表 ⊇ 已知键集;`commands.py` 源码里的字面量集合 == 键集)。
- **附件↔队列图片回路**:dequeue 把图片塞回输入区、submit 后清空、images=None 语义——搬进对象时最容易错。测试:Task 16/17 的单元测试钉 `dequeue_latest` 回填与 `take(None)` 清空。
- **队列优先于压缩、仅 stop 自动续跑**:`_run` finally 块的顺序(steer 降级→非 aborted 且无 pending 才压缩→stop 才自动取下一队)。测试:Task 16 跑全量 `tests/test_app_pending.py tests/test_compact.py`;不改断言。
- **审批/提问管线超时语义**:`approval_timeout_s`/`_ask_lock` 留在 app.py 不搬;任何触碰其文件的任务跑 `tests/test_tui_approval.py tests/test_tui_ask.py`。
- **外观先于首帧解析**:`__init__` 里的主题/语言解析顺序不能变,否则浅色终端闪深色。测试:Task 4 跑 `tests/test_appearance.py tests/test_tui_app_theme.py tests/test_i18n_tui.py`。

---

## Phase 0:工作区卫生

### Task 1: 移走知乎草稿 + 提交 .gitignore

**Files:**
- Move: `zhihu-zenagent.md`、`zhihu-zenagent-v2.md` → `docs/za/`(该目录已被 ignore)
- Modify: `.gitignore`(补行尾换行,内容已含 `.zenagent/memory/`)

- [ ] **Step 1: 移动草稿并修换行**

```bash
mkdir -p docs/za && mv zhihu-zenagent.md zhihu-zenagent-v2.md docs/za/
printf '\n' >> .gitignore   # 仅当 tail -c1 .gitignore 不是换行时
tail -c 1 .gitignore | od -c | head -1   # 期望以 \n 结尾
```

- [ ] **Step 2: 验证工作区干净**

Run: `git status --short`
Expected: 只剩 ` M .gitignore`

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore agent memory dir, fix missing trailing newline"
```

---

## Phase 1:ZenAgentApp 十摊拆分

### Task 2: SCREEN_OPENERS 注册表替换 elif 链(含守卫测试)

**Files:**
- Modify: `zenagent/cli/tui/app.py:1561-1628`(`_dispatch_command`)
- Create: `tests/test_tui_screen_registry.py`

**Interfaces:**
- Produces: `ZenAgentApp.SCREEN_OPENERS: dict[str, str]`(类属性,screen 名 → 方法名)。15 个键:`model-picker→open_model_picker`、`session-picker→open_session_picker`、`session-picker-all→open_session_picker`(同方法,scope 由方法默认值处理)、`mode-picker→open_mode_picker`、`thinking-picker→open_thinking_picker`、`theme-picker→open_theme_picker`、`language-picker→open_language_picker`、`settings→open_settings`、`plan-review→open_plan_review`、`display-reset→action_display_reset`、`hotkeys→open_hotkeys`、`usage-dashboard→open_usage_dashboard`、`mcp-add→open_mcp_add`、`todo-edit→open_todo_editor`、`search-settings→open_search_provider`。注意 `session-picker` 与 `-all` 两个键都指向 `open_session_picker`,scope 差异由 `_dispatch_command` 在调用前按键名传参:取 opener 时若是 `-all` 键则 `getattr(self, opener)("all")` 否则 `getattr(self, opener)("project")`。

- [ ] **Step 1: 写失败的守卫测试**

```python
import re
from pathlib import Path

from zenagent.cli.tui.app import ZenAgentApp

COMMANDS_SRC = (Path(__file__).parents[1] / "zenagent" / "app" / "commands.py").read_text()

def test_every_screen_literal_in_commands_is_registered():
    assert set(re.findall(r'screen="([a-z-]+)"', COMMANDS_SRC)) == set(ZenAgentApp.SCREEN_OPENERS)

def test_known_screen_keys_are_pinned():
    assert {
        "model-picker", "session-picker", "session-picker-all", "mode-picker",
        "thinking-picker", "theme-picker", "language-picker", "settings",
        "plan-review", "display-reset", "hotkeys", "usage-dashboard",
        "mcp-add", "todo-edit", "search-settings",
    } <= set(ZenAgentApp.SCREEN_OPENERS)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_tui_screen_registry.py -v`
Expected: FAIL(`SCREEN_OPENERS` 不存在)

- [ ] **Step 3: 实现**——`SCREEN_OPENERS` 字典加在 `ZenAgentApp` 类属性区(BINDINGS 之后);`_dispatch_command` 里从 `if result.screen == "model-picker":` 起的整段 elif 链替换为:

```python
opener = self.SCREEN_OPENERS.get(result.screen)
if opener is not None:
    method = getattr(self, opener)
    if result.screen == "session-picker-all":
        method("all")
    else:
        method()
```

`display-reset` 键指向的 `action_display_reset()` 无参,适用 else 分支。`session-picker` 默认 scope 原为 `"project"`(`open_session_picker("project")`),保持:else 分支调用 `method()` 前置条件是 `open_session_picker` 签名带默认 `scope="project"`,若现状无默认值,则在此任务给该委托补默认参 `scope: str = "project"`。

- [ ] **Step 4: 跑测试与既有守卫**

Run: `uv run pytest tests/test_tui_screen_registry.py tests/test_tui_command_guard.py tests/test_tui_palette.py tests/test_app_commands.py -v && bash scripts/check.sh`
Expected: 全 PASS

- [ ] **Step 5: Commit** `refactor(tui): replace the screen elif chain with a guarded registry`

### Task 3: app_parts 包骨架 + UpdateNoticeMixin(最小摊,立范式)

**Files:**
- Create: `zenagent/cli/tui/app_parts/__init__.py`、`zenagent/cli/tui/app_parts/update_notice.py`
- Modify: `zenagent/cli/tui/app.py`(类基类、删 4 个方法)

**Interfaces:**
- Produces: `UpdateNoticeMixin`(无状态,方法:`_start_update_check`、`_maybe_show_cached_update`、`_show_update_notice`、`_refresh_update_check`,逐字搬移)。固定基类顺序,后续 mixin 按此追加:
  `class ZenAgentApp(UpdateNoticeMixin, App)` → 最终:`(UpdateNoticeMixin, AppearanceMixin, ModelsMixin, SessionsMixin, HintsMixin, CommandsMixin, QueueActionsMixin, DraftsMixin, DisplayMixin, ViewActionsMixin, App)`。

- [ ] **Step 1: 建包;`update_notice.py` 定义 `UpdateNoticeMixin`,四个方法体自 `app.py:394-460` 区域逐字搬移(懒 import 与注释保留)**
- [ ] **Step 2: `app.py` 基类改为 `(UpdateNoticeMixin, App)`,删除原方法**
- [ ] **Step 3: 验证** Run: `uv run pytest tests/test_tui_chat.py tests/test_cli.py -q && bash scripts/check.sh` Expected: PASS
- [ ] **Step 4: Commit** `refactor(tui): extract the update-check mixin (app_parts pattern)`

### Task 4: AppearanceMixin + screens/appearance.py

**Files:**
- Create: `zenagent/cli/tui/app_parts/appearance.py`、`zenagent/cli/tui/screens/appearance.py`
- Modify: `zenagent/cli/tui/app.py`

**Interfaces:**
- Produces: `AppearanceMixin`:`set_appearance`、`_apply_appearance`、`_repaint_rich`、`set_language`、`_announce_appearance_warnings`、`_announce_language_warnings`(逐字)。
- Produces: `screens/appearance.py` 模块函数 `open_theme_picker(app) -> None`、`open_language_picker(app) -> None`(体 = 原 App 方法,self 换 app 参数)。App 上留同名一行委托:`def open_theme_picker(self): from ...screens.appearance import open_theme_picker; open_theme_picker(self)`(懒 import 保持)。
- Consumes: `AppearanceMixin._rebuild_commands` 未搬(属 Task 8),`set_language` 里的 `self._rebuild_commands()` 调用不变(mixin 间经 self 调用,MRO 保证可达)。

- [ ] **Step 1: 建 `app_parts/appearance.py` 与 `screens/appearance.py`,方法搬移**
- [ ] **Step 2: `app.py` 基类追加 `AppearanceMixin`,删原方法,加两个一行委托**
- [ ] **Step 3: 验证** Run: `uv run pytest tests/test_appearance.py tests/test_tui_app_theme.py tests/test_i18n_tui.py tests/test_tui_chat.py -q && bash scripts/check.sh` Expected: PASS
- [ ] **Step 4: Commit** `refactor(tui): extract appearance/language into mixin and picker flows`

### Task 5: ModelsMixin + screens/providers.py

**Files:**
- Create: `zenagent/cli/tui/app_parts/models.py`、`zenagent/cli/tui/screens/providers.py`
- Modify: `zenagent/cli/tui/app.py`

**Interfaces:**
- Produces: `ModelsMixin`:`_model_providers`、`_search_providers`、`_apply_model`、`_cycle_model`、`_announce_missing_key`、`_prompt_secret`、`action_pick_model`、`action_cycle_model`、`action_cycle_model_backward`(逐字)。
- Produces: `screens/providers.py`:`open_model_picker(app)`、`open_model_keys(app)`、`open_search_keys(app)`、`open_search_provider(app)`(体=self→app)。App 留四个一行委托。
- Consumes: `_prompt_secret` 调 `self._ask_question`(留在 app.py,经 self 可达)。

- [ ] **Step 1: 建两个模块,方法搬移;App 基类追加 `ModelsMixin`,加委托**
- [ ] **Step 2: 验证** Run: `uv run pytest tests/test_tui_missing_key.py tests/test_tui_controls.py tests/test_tui_chat.py -q && bash scripts/check.sh` Expected: PASS
- [ ] **Step 3: Commit** `refactor(tui): extract model/provider/key management`

### Task 6: SessionsMixin + screens/pickers.py

**Files:**
- Create: `zenagent/cli/tui/app_parts/sessions.py`、`zenagent/cli/tui/screens/pickers.py`
- Modify: `zenagent/cli/tui/app.py`

**Interfaces:**
- Produces: `SessionsMixin`:`_apply_session_action`、`_reset_after_switch`(逐字)。
- Produces: `screens/pickers.py` 模块函数(体=self→app):`open_session_picker(app, scope="project")`、`open_mode_picker(app)`、`open_thinking_picker(app)`、`open_settings(app)`、`open_hotkeys(app)`、`open_usage_dashboard(app)`、`open_mcp_add(app)`、`open_todo_editor(app)`、`open_plan_review(app)`。App 上每个留一行同名委托(测试直接调 `app.open_session_picker()`,冻结要求;`scope` 默认参必须保留)。
- Consumes: Task 2 的注册表此时指向的仍是 App 同名委托,不受影响。

- [ ] **Step 1: 建两模块,搬移;基类追加 `SessionsMixin`;加九个委托**
- [ ] **Step 2: 验证** Run: `uv run pytest tests/test_tui_sessions.py tests/test_tui_pickers.py tests/test_tui_palette.py tests/test_tui_chat.py -q && bash scripts/check.sh` Expected: PASS
- [ ] **Step 3: Commit** `refactor(tui): extract picker/session flows behind one-line delegates`

### Task 7: hints 匹配逻辑并入 cli/completion.py + HintsMixin

**Files:**
- Modify: `zenagent/cli/completion.py`、`zenagent/cli/tui/app.py`
- Create: `zenagent/cli/tui/app_parts/hints.py`

**Interfaces:**
- Produces(`cli/completion.py` 模块级纯函数,加入 `__all__`):
  `command_matches(commands, token_text) -> list[tuple[str, str]]`(commands 为 `CommandRegistry`,i18n 前缀在函数内取)
  `skill_matches(skills, token_text) -> list[tuple[str, str]]`
  `argument_matches(models, commands, name, needle) -> list[tuple[str, str]]`(体 = 原 `_argument_values` + `_hint_context` 里的 needle 过滤段;models 为 `ModelManager`,仅 `/model` 分支用其 `entries()`)
  `hint_context(commands, skills, models, text, cursor) -> tuple[list[tuple[str, str]], str, int, int, str]`(体 = 原 `_hint_context`,`self.commands`/`self.runtime.skills`/`self.models` 换参数,内部改调上面三个函数)
- Produces: `HintsMixin`:`action_hint_next/prev/dismiss/accept` + 原 `_hint_start/_hint_end/_hint_prefix/_hint_kind` 状态的读写方法(状态本身留在 `App.__init__`,测试冻结)。`_hint_context`、`_command_matches`、`_skill_matches`、`_argument_values` 变一行委托到新函数(tests/test_i18n_app.py:93 直接调它们,冻结)。
- Consumes: `slash_token_at`(app/skill_commands)、`argument_values`(app/slash_args)——`argument_matches` 的取值体 = 原 `_argument_values`。

- [ ] **Step 1: completion.py 加四个纯函数;新建 HintsMixin;App 四个匹配方法改一行委托,`_hint_context` 体内联删去**
- [ ] **Step 2: 基类追加 `HintsMixin`**
- [ ] **Step 3: 验证** Run: `uv run pytest tests/test_i18n_app.py tests/test_completion.py tests/test_tui_chat.py -q && bash scripts/check.sh` Expected: PASS
- [ ] **Step 4: Commit** `refactor(tui): share hint matching via cli.completion, extract hint actions`

### Task 8: CommandsMixin(分发与注册)

**Files:**
- Create: `zenagent/cli/tui/app_parts/commands.py`
- Modify: `zenagent/cli/tui/app.py`

**Interfaces:**
- Produces: `CommandsMixin`:`get_system_commands`、`_run_command`、`_dispatch_command`、`_run_extension_command`、`_rebuild_commands`、`_show_help` + `SCREEN_OPENERS` 字典自 Task 2 的类属性区迁入本 mixin(仍在 `ZenAgentApp.SCREEN_OPENERS` 可达)。`_dispatch_command` 调 `self._apply_model`/`self.open_*`/`self.action_*` 全部经 self(mixin/self 委托可达)。

- [ ] **Step 1: 建模块搬移;`SCREEN_OPENERS` 移入;App 删原方法;基类追加 `CommandsMixin`(置于 `HintsMixin` 之后,注意 `SCREEN_OPENERS` 定义随迁)**
- [ ] **Step 2: 验证** Run: `uv run pytest tests/test_tui_command_guard.py tests/test_tui_screen_registry.py tests/test_app_commands.py tests/test_docs_commands.py tests/test_tui_approval.py tests/test_tui_ask.py -q && bash scripts/check.sh` Expected: PASS
- [ ] **Step 3: Commit** `refactor(tui): extract command dispatch into CommandsMixin`

### Task 9: QueueActionsMixin(队列/steering/undo/retry 的 TUI 动作)

**Files:**
- Create: `zenagent/cli/tui/app_parts/queue.py`
- Modify: `zenagent/cli/tui/app.py`

**Interfaces:**
- Produces: `QueueActionsMixin`:`action_interrupt`、`_undo_last_turn`、`action_dequeue`、`action_follow_up`、`_promote_pending`、`action_steer`、`action_clear_queue`、`action_retry`(逐字;全部经 `self.chat.*` 调用,无自有状态)。

- [ ] **Step 1: 建模块搬移;基类追加 `QueueActionsMixin`**
- [ ] **Step 2: 验证** Run: `uv run pytest tests/test_tui_pending_band.py tests/test_tui_controls.py tests/test_tui_chat.py -q && bash scripts/check.sh` Expected: PASS
- [ ] **Step 3: Commit** `refactor(tui): extract queue/steering key actions`

### Task 10: DraftsMixin(草稿/历史/外编/粘贴 + 提交路由)

**Files:**
- Create: `zenagent/cli/tui/app_parts/drafts.py`
- Modify: `zenagent/cli/tui/app.py`

**Interfaces:**
- Produces: `DraftsMixin`:`action_clear_draft`、`action_history_search`、`action_edit_external`、`edit_draft_externally`、`action_paste_clipboard`、`remove_attachment`(逐字)+ `on_prompt_input_submitted`(逐字,提交路由:skill/命令/模板/扩展/直发)。

- [ ] **Step 1: 建模块搬移;基类追加 `DraftsMixin`**
- [ ] **Step 2: 验证** Run: `uv run pytest tests/test_tui_attachments.py tests/test_tui_history.py tests/test_tui_chat.py tests/test_app_links.py -q && bash scripts/check.sh` Expected: PASS
- [ ] **Step 3: Commit** `refactor(tui): extract draft editing and submit routing`

### Task 11: DisplayMixin(思考/工具显示/plan/显示重置)

**Files:**
- Create: `zenagent/cli/tui/app_parts/display.py`
- Modify: `zenagent/cli/tui/app.py`

**Interfaces:**
- Produces: `DisplayMixin`:`action_cycle_thinking`、`action_toggle_thinking`、`action_toggle_plan`、`action_toggle_tools`、`action_toggle_tool_visibility`、`action_display_reset`、`action_cycle_model` 已在 ModelsMixin(勿重复)。逐字搬移。

- [ ] **Step 1: 建模块搬移;基类追加 `DisplayMixin`**
- [ ] **Step 2: 验证** Run: `uv run pytest tests/test_tui_controls.py tests/test_tui_layout.py -q && bash scripts/check.sh` Expected: PASS
- [ ] **Step 3: Commit** `refactor(tui): extract display toggle actions`

### Task 12: Sidebar 布局并入 widgets

**Files:**
- Modify: `zenagent/cli/tui/widgets/sidebar.py`、`zenagent/cli/tui/widgets/sidebar_resize.py`、`zenagent/cli/tui/app.py`

**Interfaces:**
- Produces: `Sidebar` 增方法 `begin_resize(app)`、`apply_width(app, width)`、`band_width(app) -> int`(宽度状态 `_sidebar_width/_sidebar_resizing` 移入 `Sidebar` 实例属性;拖拽几何常量 `DEFAULT/MIN/MAX_SIDEBAR_WIDTH`、`MAIN_COLUMN_MIN` 随迁)。App 上冻结委托:`sidebar_width` property、`begin_sidebar_resize`、`end_sidebar_resize`、`resize_sidebar`、`_band_width`、`_apply_sidebar_width`、`_open_sidebar_from_strip`、`action_toggle_sidebar` 全部一行转发到 widget(测试直接调它们)。
- Consumes: 既有 `SidebarResizeHandle` 事件路径不变。

- [ ] **Step 1: 状态与方法移入 `Sidebar`;App 方法改一行转发(懒 import)**
- [ ] **Step 2: 验证** Run: `uv run pytest tests/test_tui_layout.py tests/test_tui_mouse.py tests/test_tui_chat.py -q && bash scripts/check.sh` Expected: PASS
- [ ] **Step 3: Commit** `refactor(tui): move sidebar width state into the Sidebar widget`

### Task 13: ViewActionsMixin(滚动/子代理视图/任务日志)

**Files:**
- Create: `zenagent/cli/tui/app_parts/views.py`
- Modify: `zenagent/cli/tui/app.py`

**Interfaces:**
- Produces: `ViewActionsMixin`:`_scroll_chat`、`_on_mouse_scroll_up/down`、`action_scroll_up/down`、`action_jump_to_bottom`、`on_transcript_view_pending_changed`、`enter_subagent_view`、`_exit_subagent_view`、`show_job_log`、`_job_log_closed`(逐字)。状态 `_viewing`/`_job_log_screen` 留在 `App.__init__`(测试冻结),mixin 读写之。滚动键绑定(`on_...`)保留原名。

- [ ] **Step 1: 建模块搬移;基类追加 `ViewActionsMixin`(末位,App 之前)**
- [ ] **Step 2: 验证** Run: `uv run pytest tests/test_tui_job_log.py tests/test_tui_mouse.py tests/test_tui_chat.py -q && bash scripts/check.sh` Expected: PASS
- [ ] **Step 3: Commit** `refactor(tui): extract scroll/subagent-view/job-log actions`

### Task 14: 收尾审计 + CHANGELOG 报告

**Files:**
- Modify: `CHANGELOG.md`、`zenagent/cli/tui/app.py`

- [ ] **Step 1: 审计 app.py**:仅剩 core(`__init__`/`compose`/`on_mount`/`on_unmount`/`on_event`/`on_resize`/`on_text_area_changed`/`on_prompt_input_resized`/`_on_change`/`_run_refresh`/`_refresh_status`/`_sync_work_timer`/`_sync_chrome`/`dismiss_error`/`action_quit_app`)、审批/提问管线(`request_tool_approval`/`request_ask`/`_ask_question`)、弹窗原语(`_modal_result`/`_confirm`/`_prompt_rename`/`open_menu`)、滚动委托、一行委托与基类声明。发现遗漏方法:属哪摊归哪摊(新任务),不在本任务顺手搬。
- [ ] **Step 2: 记录 `wc -l zenagent/cli/tui/app.py`(前后对照),CHANGELOG 顶部加 `## Unreleased` 段落一行条目**
- [ ] **Step 3: 全量 gate** Run: `bash scripts/check.sh` Expected: PASS
- [ ] **Step 4: Commit** `refactor(tui): ZenAgentApp reduced to core wiring (10 concerns extracted)`

---

## Phase 2:ChatController 三块状态离场

### Task 15: SubagentTracker(子代理视图状态对象)

**Files:**
- Create: `zenagent/app/subagent_tracker.py`
- Modify: `zenagent/app/chat.py`
- Test: `tests/test_subagent_tracker.py`(新)

**Interfaces:**
- Produces: `class SubagentTracker` —— 构造 `SubagentTracker(notify: Callable[[], None])`;状态:`_builders: dict[str, TranscriptBuilder]`、`_meta: dict[str, dict]`、`_disk_metas: dict | None`、`cost: float`、`tokens: int`;方法:`on_event(call_id, kind, payload) -> None`(体=原 `on_subagent_event`)、`transcript(call_id, session_path) -> list | None`(体=`subagent_transcript`)、`views(cards, session_path) -> list[SubagentView]`(体=`_subagent_views`,`cards` 传 `builder.cards.values()` 列表)、`add_spend(cost, tokens) -> None`、`invalidate_disk() -> None`、`reset() -> None`(体=`_reset_subagent_state` + cost/tokens 清零)。模块级 `_subagent_view()` 与 `_TASK_SUBAGENT_STATUSES` 随迁。
- Consumes: `SubagentStore`(zenagent/agent/subagents.py)、`TranscriptBuilder`。`ChatController.__init__` 改持 `self._subagents = SubagentTracker(self._notify)`;`status`/`_on_event`/`reload_transcript`/`clear`/`retry` 改调 tracker 公有方法;`on_subagent_event`/`subagent_transcript`/`_subagent_views`/`_reset_subagent_state` 变一行委托(测试与 tools/task 观察者契约)。

- [ ] **Step 1: 写失败测试**:构造 tracker,`on_event(start)`→`views` 含 running 行;`on_event(end, status="ok", cost, tokens)`→status/cost 生效;`reset()`→views 空、cost=0。
- [ ] **Step 2: 跑** `uv run pytest tests/test_subagent_tracker.py -v` Expected: FAIL
- [ ] **Step 3: 实现模块并接线 ChatController**
- [ ] **Step 4: 跑** `uv run pytest tests/test_subagent_tracker.py tests/test_app_chat.py tests/test_tui_job_log.py -q && bash scripts/check.sh` Expected: PASS
- [ ] **Step 5: Commit** `refactor(app): extract SubagentTracker from ChatController`

### Task 16: PendingQueue(队列+steering 共享 _pending)

**Files:**
- Create: `zenagent/app/pending.py`
- Modify: `zenagent/app/chat.py`
- Test: `tests/test_app_pending.py`(追加单元测试,不改既有断言)

**Interfaces:**
- Produces: `_PendingItem` dataclass 随迁;`class PendingQueue` —— 状态:`_items: list[_PendingItem]`、`_next_id: int`;方法:`queue(text, images) -> PendingMessage`(`add` 的 queue-lane 薄壳,不含附件语义)、`add(text, images, lane) -> PendingMessage`(体=`_add_pending`)、`clear() -> int`、`items -> list[PendingMessage]`、`count(lane) -> int`、`has(lane) -> bool`、`take_next_queue_item() -> _PendingItem | None`、`dequeue_latest() -> tuple[str, list[ImageContent]] | None`(体=`dequeue_follow_up` 的弹出段,返回文本+图片;回填附件由控制器做)、`remove_lane(lane) -> None`(体=`_demote_steering`/`_on_event` 里的列表过滤)、`find(pending_id) -> _PendingItem | None`、`remove(item) -> None`。
- Consumes: `ChatController` 保留编排:`steer`/`promote`/`_demote_steering`/`drop_pending` 调 queue 原语 + `runtime.agent.*`;附件回填保持现状写法 `self._attachments[:0] = images`(Task 17 落地后换成 `prepend`);`_run` finally 的取队顺序不变;`status` 的 `pending=` 用 `self._pending_queue.items`、`follow_ups=` 用 `count("queue")`。controller 上 `self._pending` 名字改为 `self._pending_queue`(注意 grep 全部引用点;测试只经公有方法/`status`,无直接 `_pending` 访问——若有,视为冻结属性,留 property 转发)。

- [ ] **Step 1: 写失败单元测试**:`queue→items→clear`;`dequeue_latest` 返回最后入队的 queue 项文本与图片;`take_next_queue_item` 取最旧;`remove_lane("steer")` 只删 steer。
- [ ] **Step 2: 跑** `uv run pytest tests/test_app_pending.py -v` Expected: 新增 FAIL
- [ ] **Step 3: 实现 `PendingQueue`,ChatController 改为编排**
- [ ] **Step 4: 跑** `uv run pytest tests/test_app_pending.py tests/test_compact.py tests/test_app_chat.py tests/test_tui_pending_band.py -q && bash scripts/check.sh` Expected: PASS(顺序类断言不得改动)
- [ ] **Step 5: Commit** `refactor(app): extract PendingQueue (queue+steering lanes) from ChatController`

### Task 17: Attachments(附件状态对象)

**Files:**
- Create: `zenagent/app/attachments_state.py`
- Modify: `zenagent/app/chat.py`
- Test: `tests/test_app_chat_attachments.py`(新)

**Interfaces:**
- Produces: `class Attachments` —— 状态:`_blocks: list[ImageContent]`;方法:`add(block) -> None`、`remove(index) -> bool`、`clear() -> bool`、`all() -> list[ImageContent]`、`prepend(blocks) -> None`、`take(explicit: list | None) -> list[ImageContent]`(语义 = `submit`/`queue_follow_up` 现行为:explicit 非空原样返回;否则返回快照并清空)。
- Consumes: `ChatController.attachments` property 与 `attach_image`/`remove_attachment`/`clear_attachments` 一行委托(`attachments` 属性名冻结,测试与 TUI 均在用)。

- [ ] **Step 1: 写失败测试**:`take(None)` 清空并返回快照;`take([x])` 不清空;`prepend` 后 `all()` 顺序在前。
- [ ] **Step 2: 跑** `uv run pytest tests/test_app_chat_attachments.py -v` Expected: FAIL
- [ ] **Step 3: 实现并接线(注意与 Task 16 的 `dequeue_latest` 回填衔接:controller 调 `self._attachments.prepend(images)`)**
- [ ] **Step 4: 跑** `uv run pytest tests/test_app_chat_attachments.py tests/test_tui_attachments.py tests/test_app_chat.py -q && bash scripts/check.sh` Expected: PASS
- [ ] **Step 5: Commit** `refactor(app): extract Attachments state from ChatController`

---

## Phase 3:Runtime metrics 离场

### Task 18: MetricsSession

**Files:**
- Create: `zenagent/app/metrics.py`
- Modify: `zenagent/app/runtime.py`

**Interfaces:**
- Produces: `class MetricsSession` —— 构造 `MetricsSession(path: Path)`;方法:`start(agent: Agent) -> None`(体=原 `_start_metrics`)、`flush(agent: Agent) -> None`(体=原 `_flush_metrics` 的 append 与重挂段)。`Runtime` 上 `_metrics_recorder` 字段删除,改 `self._metrics: MetricsSession | None`;`bootstrap` 里 `runtime._start_metrics(agent)`→`runtime._metrics = MetricsSession(options.metrics); runtime._metrics.start(agent)`,`persist()` 里 `runtime._flush_metrics()`→`runtime._metrics.flush(runtime.agent)`。`_start_metrics`/`_flush_metrics` 方法删除(Runtime 的下划线方法非冻结面,全仓无其他调用点——实现时先 grep 确认)。

- [ ] **Step 1: 建模块搬移;Runtime 接线;grep 确认无残留调用**
- [ ] **Step 2: 验证** Run: `uv run pytest tests/test_cli.py tests/test_doctor.py -q && bash scripts/check.sh` Expected: PASS
- [ ] **Step 3: Commit** `refactor(app): extract MetricsSession from Runtime`
