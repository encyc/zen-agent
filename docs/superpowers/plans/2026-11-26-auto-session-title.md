# 会话自动命名（auto session title）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新会话的第一条用户消息之后，后台调一次 LLM 生成短会话名，写进已有的 `.titles.json`，所有前端自动显示。

**Architecture:** `zenagent/app/title.py` 提供 `clean_title` / `title_source` / `AutoTitler`；`bootstrap.prepare`（所有前端的唯一 app 装配点）把 `AutoTitler` 挂到 agent 的 `message_end` 上；任务调 provider、清洗、**写前复核仍无标题**后落 sidecar；`Runtime.close()` 等在飞任务。提示词进两个新 block。

**Tech Stack:** Python 3.11+、asyncio、现有 provider 抽象（`safe_stream` / `StreamOptions` / `stream_fn_for`）、pytest（`asyncio_mode = "auto"`，测试全离线）。

**Spec:** [docs/superpowers/specs/2026-11-26-auto-session-title-design.md](../specs/2026-11-26-auto-session-title-design.md)

## Global Constraints

- Python 3.11+；不新增运行时依赖（kernel 只允许 `httpx`）。
- 唯一门禁：`bash scripts/check.sh`（sync + ruff + mypy ratchet + 架构预算 + wire 校验 + pytest）。
- 架构预算：`zenagent/agent/session.py` 在 allowlist 里上限 920 行（当前 912），**只许变短**——本计划对它只做函数改名和 `__all__` 加一行（最多净 +1）。
- `zenagent/app/title.py` 是新模块，**必须 mypy 干净**（不在 ratchet 名单里）；`runtime.py` / `bootstrap.py` 在名单里，但新代码仍按有类型写。
- 提示词正文只允许放在 `zenagent/prompts/blocks/`（`tests/test_prompts_sources.py` 扫描）；新 block 必须在 `prompts/spec.py` 的 `BLOCKS` 里声明，`version=PROMPT_VERSION`、`budget_chars > 0`、模板变量与声明完全一致。
- 自动命名**永不**作用于已恢复的会话、**永不**覆盖已有标题、**永不**向用户报错或重试。
- 标题长度：提示词要求「中文约 10 字 / 英文约 5–6 个单词」，代码只留 `AUTO_TITLE_LIMIT = 60` 的安全上限，不按 10 硬截。

## Review Focus

1. **模型答的是一句话**（"这个会话可以叫：登录重试修复。"）——侧栏必须拿到名字而不是句子。Task 2 的 `clean_title` 表 + Task 3 的端到端。
2. **名字被引号 / 代码围栏 / `会话名：` 前缀包着**——要剥掉，不能把符号显示给用户。Task 2。
3. **`/rename` 与后台调用赛跑**——用户在往返途中改名，存储标题必须赢，不能被模型结果覆盖。Task 3 的 race 测试。
4. **goal 轮的内容带 `<objective>` 包裹和续跑说明**——名字必须来自 objective 本体，不能把包裹或"继续"提示写进名字。Task 3。
5. **纯图片、无文字的首条消息**——不发标题请求、不崩。Task 3 的 `title_source` 测试。
6. **标题调用失败**（key 错 / 500 / 超时）——本轮照常结束、不给用户弹提示、不重试，会话回退到首行 `brief()`。Task 3 的失败测试 + Task 4 的 close 测试。

---

## Task 1: 标题提示词 block 与 `compose_title`

**Files:**

- Create: `zenagent/prompts/blocks/title_system.md`
- Create: `zenagent/prompts/blocks/title_user.md`
- Modify: `zenagent/prompts/spec.py`（`BLOCKS` 里加两条，放在 compact 条目附近）
- Modify: `zenagent/prompts/__init__.py`（`__all__` + 新函数）
- Test: `tests/test_prompts_title.py`（新建）

**Interfaces:**

- Produces: `zenagent.prompts.compose_title(prompt: str) -> tuple[str, str]`，返回 `(system, user)`。
- Produces: block id `title.system`（无变量）、`title.user`（变量 `prompt`）。

- [ ] **Step 1: 写失败测试**

```python
"""标题提示词：渲染与变量声明。"""

from __future__ import annotations

from zenagent.prompts import compose_title
from zenagent.prompts.registry import read_block
from zenagent.prompts.spec import BLOCKS


def test_compose_title_renders_the_prompt() -> None:
    system, user = compose_title("帮我修一下登录重试的 bug")
    assert system == read_block("title.system")
    assert "帮我修一下登录重试的 bug" in user
    assert "只输出会话名" in user


def test_title_blocks_declare_their_vars() -> None:
    assert BLOCKS["title.system"].required_vars == ()
    assert BLOCKS["title.user"].required_vars == ("prompt",)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_prompts_title.py -q`
Expected: `ImportError: cannot import name 'compose_title'`

- [ ] **Step 3: 建 block 文件、声明 spec、导出 compose_title**

`zenagent/prompts/blocks/title_system.md`（正文照抄；`budget_chars=400`）：

```markdown
你是会话命名器。读完用户的第一条消息，给它起一个会话名。

要求：
1. 用用户消息的语言；名词短语，不要句子。
2. 中文约 10 个字，英文约 5–6 个单词；宁短勿长。
3. 不加引号、不加结尾标点、不解释，也不要写「会话名：」这类前缀。

只输出会话名本身。
```

`zenagent/prompts/blocks/title_user.md`（`budget_chars=200`，`required_vars=("prompt",)`）：

```markdown
用户的第一条消息：

{{prompt}}

只输出会话名。
```

`zenagent/prompts/spec.py` 的 `BLOCKS` 里加：

```python
    "title.system": BlockSpec(
        id="title.system",
        path="title_system.md",
        version=PROMPT_VERSION,
        cache_class="stable",
        budget_chars=400,
    ),
    "title.user": BlockSpec(
        id="title.user",
        path="title_user.md",
        version=PROMPT_VERSION,
        cache_class="stable",
        budget_chars=200,
        required_vars=("prompt",),
    ),
```

`zenagent/prompts/__init__.py`：`__all__` 加 `"compose_title"`，函数放在 `compose_compact` 后面：

```python
def compose_title(prompt: str) -> tuple[str, str]:
    """Render the session-title request as ``(system, user)``."""
    return read_block("title.system"), render(
        read_block("title.user"), {"prompt": prompt}
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_prompts_title.py tests/test_prompts_registry.py tests/test_prompts_budget.py -q`
Expected: PASS（registry 校验会同时检查两个新 block 的变量与预算）

- [ ] **Step 5: Commit**

```bash
git add zenagent/prompts/blocks/title_system.md zenagent/prompts/blocks/title_user.md \
        zenagent/prompts/spec.py zenagent/prompts/__init__.py tests/test_prompts_title.py
git commit -m "feat(title): add the session-title prompt blocks"
```

---

## Task 2: `clean_title`

**Files:**

- Create: `zenagent/app/title.py`
- Test: `tests/test_title.py`（新建）

**Interfaces:**

- Produces: `zenagent.app.title.clean_title(raw: str) -> str`
- Produces: 常量 `AUTO_TITLE_LIMIT = 60`

- [ ] **Step 1: 写失败测试**

```python
"""Auto session title: normalization and the background titler."""

from __future__ import annotations

import pytest

from zenagent.app.title import AUTO_TITLE_LIMIT, clean_title


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("登录重试修复", "登录重试修复"),
        ("「登录重试修复」", "登录重试修复"),
        ("`登录重试修复`", "登录重试修复"),
        ("**登录重试修复**", "登录重试修复"),
        ("会话名：登录重试修复", "登录重试修复"),
        ("Title: login retry fix", "login retry fix"),
        ("```\n登录重试修复\n```", "登录重试修复"),
        ("登录重试修复。", "登录重试修复"),
        ("登录重试修复\n\n说明：这是会话名", "登录重试修复"),
        ("  登录   重试  ", "登录 重试"),
        ("", ""),
        ("   \n  ", ""),
        ("长" * (AUTO_TITLE_LIMIT + 20), "长" * AUTO_TITLE_LIMIT),
    ],
)
def test_clean_title(raw: str, expected: str) -> None:
    assert clean_title(raw) == expected
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_title.py -q`
Expected: `ModuleNotFoundError: No module named 'zenagent.app.title'`

- [ ] **Step 3: 实现 `clean_title`**

新建 `zenagent/app/title.py`，模块 docstring 说明"一次后台 provider 调用给会话起展示名，失败静默"。顺序是固定的（测试按这个顺序钉住）：去掉整段代码围栏 → 取第一条非空行 → 剥已知前缀 → 剥两端引号/反引号/星号 → 压空白 → 去尾随标点 → 截 `AUTO_TITLE_LIMIT`。

```python
AUTO_TITLE_LIMIT = 60

_PREFIXES = ("会话名：", "会话名:", "会话名称：", "标题：", "名字：", "Title:", "title:")
_WRAPPERS = "\"'“”‘’「」『』《》`*"
_TRAILING = "。．.!！?？,，;；:：、…"


def clean_title(raw: str) -> str:
    """One provider reply -> a display name; ``""`` means give up."""
```

围栏处理：`text.startswith("```")` 时去掉前三反引号与第一行，再 `rstrip()` 去掉结尾的 ```` ``` ````（`str.removesuffix`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_title.py -q && uv run ruff check zenagent/app/title.py tests/test_title.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/title.py tests/test_title.py
git commit -m "feat(title): normalize a model reply into a session name"
```

---

## Task 3: `title_source` 与 `AutoTitler`

**Files:**

- Modify: `zenagent/app/title.py`（新增 `title_source`、`AutoTitler`、两个常量）
- Modify: `zenagent/agent/session.py:312`（`_goal_objective` → 公开的 `goal_objective`；`__all__` 加一行；`:366` 的调用点同步改名）
- Test: `tests/test_title.py`（追加）

**Interfaces:**

- Consumes: `zenagent.prompts.compose_title`（Task 1）、`clean_title`（Task 2）
- Produces: `zenagent.agent.session.goal_objective(text: str) -> str`
- Produces: `zenagent.app.title.title_source(message: UserMessage) -> str`
- Produces: `TITLE_MAX_TOKENS = 128`、`TITLE_TIMEOUT_S = 15.0`、`TITLE_PROMPT_LIMIT = 2000`
- Produces: `AutoTitler(runtime, *, timeout_s: float = TITLE_TIMEOUT_S)`；`__call__(event: AgentEvent) -> None`；`async finish(timeout_s: float = 2.0) -> None`

- [ ] **Step 1: 写失败测试（追加到 `tests/test_title.py`）**

```python
import asyncio

from conftest import text_stream

from zenagent.agent.session import (
    goal_objective,
    read_session_titles,
    session_title,
    set_session_title,
)
from zenagent.app.title import TITLE_MAX_TOKENS, AutoTitler, title_source
from zenagent.types import ImageContent, TextContent, UserMessage


def _recording_stream(text: str, calls: list):
    """A fake provider that records ``(options, messages)`` per call."""
    inner = text_stream(text)

    async def stream(model, messages, tools, options):
        calls.append((options, messages))
        async for event in inner(model, messages, tools, options):
            yield event

    return stream


def test_goal_objective_is_public() -> None:
    wrapped = "<objective>\n修好登录\n</objective>\n\n继续"
    assert goal_objective(wrapped) == "修好登录"
    assert goal_objective("没有包裹") == ""


def test_title_source_reads_text_blocks_and_goals() -> None:
    message = UserMessage(
        content=[TextContent(text="看看这张图"), ImageContent(data="", mime_type="image/png")],
        timestamp=0,
    )
    assert title_source(message) == "看看这张图"
    goal = UserMessage(content="<objective>\n修好登录\n</objective>\n\n继续", timestamp=0, goal=True)
    assert title_source(goal) == "修好登录"


def test_title_source_skips_image_only_messages() -> None:
    message = UserMessage(
        content=[ImageContent(data="", mime_type="image/png")], timestamp=0
    )
    assert title_source(message) == ""


async def test_first_user_message_writes_the_title(runtime_factory) -> None:
    runtime = await runtime_factory(stream_fn=text_stream("登录重试修复"))
    titler = AutoTitler(runtime)
    runtime.agent.subscribe(titler)
    await runtime.agent.prompt("帮我修一下登录重试的 bug")
    await titler.finish()
    assert session_title(runtime.session.path) == "登录重试修复"


async def test_a_stored_title_is_never_overwritten(runtime_factory) -> None:
    calls: list = []
    runtime = await runtime_factory(stream_fn=_recording_stream("模型给的名字", calls))
    set_session_title(runtime.session.path, "手动名字")
    titler = AutoTitler(runtime)
    runtime.agent.subscribe(titler)
    await runtime.agent.prompt("随便说点什么")
    await titler.finish()
    assert session_title(runtime.session.path) == "手动名字"
    assert all(options.max_tokens != TITLE_MAX_TOKENS for options, _ in calls)


async def test_a_rename_during_the_call_wins(runtime_factory) -> None:
    gate = asyncio.Event()
    inner = text_stream("模型给的名字")

    async def stream(model, messages, tools, options):
        if options.max_tokens == TITLE_MAX_TOKENS:
            await gate.wait()
        async for event in inner(model, messages, tools, options):
            yield event

    runtime = await runtime_factory(stream_fn=stream)
    titler = AutoTitler(runtime)
    runtime.agent.subscribe(titler)
    await runtime.agent.prompt("帮我修登录")
    set_session_title(runtime.session.path, "手动名字")
    gate.set()
    await titler.finish()
    assert session_title(runtime.session.path) == "手动名字"


async def test_a_failing_title_call_is_silent(runtime_factory) -> None:
    inner = text_stream("ok")

    async def stream(model, messages, tools, options):
        if options.max_tokens == TITLE_MAX_TOKENS:
            raise RuntimeError("boom")
        async for event in inner(model, messages, tools, options):
            yield event

    runtime = await runtime_factory(stream_fn=stream)
    titler = AutoTitler(runtime)
    runtime.agent.subscribe(titler)
    await runtime.agent.prompt("帮我改一下")  # must not raise
    await titler.finish()
    assert read_session_titles(runtime.session.path.parent) == {}


async def test_only_the_first_message_titles(runtime_factory) -> None:
    calls: list = []
    runtime = await runtime_factory(stream_fn=_recording_stream("名字", calls))
    titler = AutoTitler(runtime)
    runtime.agent.subscribe(titler)
    await runtime.agent.prompt("第一句")
    await runtime.agent.prompt("第二句")
    await titler.finish()
    assert sum(1 for options, _ in calls if options.max_tokens == TITLE_MAX_TOKENS) == 1


async def test_a_resumed_session_is_not_retitled(runtime_factory) -> None:
    first = await runtime_factory(stream_fn=text_stream("ok"))
    await first.agent.prompt("第一句")
    path = first.session.path
    await first.close()  # one writer per session: release the lease before resuming
    calls: list = []
    resumed = await runtime_factory(resume=path, stream_fn=_recording_stream("不该出现", calls))
    titler = AutoTitler(resumed)
    resumed.agent.subscribe(titler)
    await resumed.agent.prompt("第二句")
    await titler.finish()
    assert all(options.max_tokens != TITLE_MAX_TOKENS for options, _ in calls)


async def test_a_goal_turn_titles_from_its_objective(runtime_factory) -> None:
    calls: list = []
    runtime = await runtime_factory(stream_fn=_recording_stream("登录目标", calls))
    titler = AutoTitler(runtime)
    runtime.agent.subscribe(titler)
    await runtime.agent.prompt("<objective>\n修好登录\n</objective>\n\n继续", goal=True)
    await titler.finish()
    title_calls = [messages for options, messages in calls if options.max_tokens == TITLE_MAX_TOKENS]
    assert title_calls
    prompt = title_calls[0][-1].content
    assert "修好登录" in prompt
    assert "<objective>" not in prompt


async def test_finish_gives_up_on_a_hung_call(runtime_factory) -> None:
    inner = text_stream("ok")

    async def stream(model, messages, tools, options):
        if options.max_tokens == TITLE_MAX_TOKENS:
            await asyncio.sleep(30)
        async for event in inner(model, messages, tools, options):
            yield event

    runtime = await runtime_factory(stream_fn=stream)
    titler = AutoTitler(runtime)
    runtime.agent.subscribe(titler)
    await runtime.agent.prompt("帮我改一下")
    await titler.finish(timeout_s=0.05)  # returns; the hung call is cancelled
    assert read_session_titles(runtime.session.path.parent) == {}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_title.py -q`
Expected: `ImportError: cannot import name 'goal_objective'`（以及 `title_source` / `AutoTitler`）

- [ ] **Step 3: 公开 `goal_objective`，实现 `title_source` 与 `AutoTitler`**

`zenagent/agent/session.py`：`_goal_objective` 改名 `goal_objective`（`__all__` 加 `"goal_objective"`，`:366` 附近 `_title_from_messages` 的调用同步改）。函数体不变。

`zenagent/app/title.py` 追加（`Runtime` 只在 `TYPE_CHECKING` 下导入并写成字符串注解，避免运行期循环导入）：

```python
TITLE_MAX_TOKENS = 128
TITLE_TIMEOUT_S = 15.0
TITLE_PROMPT_LIMIT = 2000


def title_source(message: UserMessage) -> str:
    """The text the name is generated from; ``""`` when there is none."""


class AutoTitler:
    """One background provider call per new session, fired by its first message."""

    def __init__(self, runtime: Runtime, *, timeout_s: float = TITLE_TIMEOUT_S) -> None:
        ...

    def __call__(self, event: AgentEvent) -> None:
        ...

    async def finish(self, timeout_s: float = 2.0) -> None:
        ...
```

实现要点（测试已钉住行为，这里只钉住容易写错的地方）：

- `title_source`：`content` 是 `str` 就用它；是 list 就拼 `TextContent.text`。空文本返回 `""`。`message.goal` 时先 `goal_objective(text) or text`。
- `__call__` 只在 `event.type == "message_end"` 且 `isinstance(event.message, UserMessage)` 时继续；然后依次判断（任何一条不满足就返回）：该 `runtime.session.path` 已在 `self._started` 里；`sum(isinstance(m, UserMessage) for m in runtime.agent.messages) != 1`；`read_session_titles(path.parent).get(path.name)` 已有值；`title_source` 为空。通过后 `self._started.add(path)` 并 `asyncio.create_task(self._run(path, text))`，任务放进 `self._tasks` 集合、`add_done_callback(self._tasks.discard)`。
- `_run(path, text)`：整体 `try/except Exception: return`（不要吞 `BaseException`）；`await asyncio.wait_for(self._request(text), self._timeout_s)` 拿名字；`clean_title` 为空就返回；**写前复核**（中间不能有 `await`）：

  ```python
  if read_session_titles(path.parent).get(path.name):
      return
  set_session_title(path, name)
  ```

  写入抛 `OSError` / `ValueError` 同样静默。
- `_request(text)`：`system, user = compose_title(text[:TITLE_PROMPT_LIMIT])`；请求是 `[SystemMessage(content=system, timestamp=now), UserMessage(content=user, timestamp=now)]`；`options = StreamOptions(api_key=agent.api_key, thinking=None, max_tokens=TITLE_MAX_TOKENS, session_id=agent.session_id)`（session id 不能省，网关按它路由）；`fn = agent.stream_fn or stream_fn_for(agent.model)`；用 `safe_stream(fn, agent.model, request, [], options)` 收流，**只在 `event.type == "done"` 时**把 `event.partial.content` 里的 `TextContent` 拼起来返回，`"error"` 返回 `""`。
- `finish`：`pending = list(self._tasks)`；空就返回；`await asyncio.wait(pending, timeout=timeout_s)`；把没完成的 `cancel()`，再 `await asyncio.gather(*pending, return_exceptions=True)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_title.py -q && uv run ruff check zenagent/app/title.py tests/test_title.py`
Expected: PASS（`test_finish_gives_up_on_a_hung_call` 约 0.05s）

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/title.py zenagent/agent/session.py tests/test_title.py
git commit -m "feat(title): name a new session from its first message"
```

---

## Task 4: 接线（`prepare` + `close`）与文档

**Files:**

- Modify: `zenagent/app/runtime.py`（`__init__` 加 `auto_titler` 属性；`close()` 收尾）
- Modify: `zenagent/app/bootstrap.py`（`prepare` 里挂 `AutoTitler`）
- Modify: `docs/guide/sessions.md`（标题小节补一段）
- Test: `tests/test_title.py`（追加）

**Interfaces:**

- Consumes: `AutoTitler`（Task 3）
- Produces: `Runtime.auto_titler: AutoTitler | None`（默认 `None`；`close()` 后置 `None`）

- [ ] **Step 1: 写失败测试（追加到 `tests/test_title.py`）**

```python
from zenagent.app.agent_profile import AgentProfile
from zenagent.app.bootstrap import prepare
from zenagent.app.runtime import RuntimeOptions


def _offline_options(root) -> RuntimeOptions:
    return RuntimeOptions(
        root=root,
        profile=AgentProfile(mcp=False, skills=False, templates=False),
    )


async def test_prepare_attaches_the_titler(app_env, tmp_path) -> None:
    prepared = await prepare(
        _offline_options(app_env), stream_fn=text_stream("登录重试修复"), views=False
    )
    runtime = prepared.runtime
    titler = runtime.auto_titler
    assert titler is not None
    try:
        await runtime.agent.prompt("帮我修一下登录重试的 bug")
        await titler.finish()
        assert session_title(runtime.session.path) == "登录重试修复"
    finally:
        await runtime.close()


async def test_close_lands_an_inflight_title(app_env, tmp_path) -> None:
    prepared = await prepare(
        _offline_options(app_env), stream_fn=text_stream("登录重试修复"), views=False
    )
    runtime = prepared.runtime
    await runtime.agent.prompt("帮我修一下")
    await runtime.close()
    assert session_title(runtime.session.path) == "登录重试修复"
    assert runtime.auto_titler is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_title.py -q -k "prepare or close"`
Expected: `AttributeError: 'Runtime' object has no attribute 'auto_titler'`

- [ ] **Step 3: 接线**

`zenagent/app/runtime.py`：

- `TYPE_CHECKING` 下 `from zenagent.app.title import AutoTitler`；`__init__` 里 `self.auto_titler: AutoTitler | None = None`（放在 `self.goal_resume` 附近）。
- `close()` 末尾（`_drop_unused_session()` 之前）加：

  ```python
  if self.auto_titler is not None:
      titler, self.auto_titler = self.auto_titler, None
      await titler.finish()
  ```

`zenagent/app/bootstrap.py`：`prepare` 里 `attach_run_recorder(runtime)` 之后、`if not views:` 之前：

```python
    titler = AutoTitler(runtime)
    runtime.auto_titler = titler
    runtime.agent.subscribe(titler)
```

顶部加 `from zenagent.app.title import AutoTitler`（与既有 `from zenagent.app...` 同组、字母序）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_title.py -q`
Expected: PASS

- [ ] **Step 5: 补文档**

`docs/guide/sessions.md` 的标题小节（`/rename` 那条附近）加一条：

```markdown
- 自动命名：新会话的第一条用户消息之后，后台调一次 LLM 把这条消息归纳成短名（约 10 个
  汉字 / 5–6 个英文词），写进同一个 sidecar。失败静默，回退到首行 `brief()`；`/rename`
  永远优先——写库前会复核，手改的名字不会被覆盖。
```

- [ ] **Step 6: 全量门禁**

Run: `bash scripts/check.sh`
Expected: 全部通过（ruff / mypy ratchet / 架构预算 / wire 校验 / pytest）

- [ ] **Step 7: Commit**

```bash
git add zenagent/app/runtime.py zenagent/app/bootstrap.py docs/guide/sessions.md tests/test_title.py
git commit -m "feat(title): name every new session from its first prompt"
```

---

## Self-Review

- Spec 覆盖：block + 提示词（Task 1）、清洗（Task 2）、触发/优先级/失败静默/只跑一次/goal/超时（Task 3）、所有前端装配点 + close + 文档（Task 4）。Spec 的「已知取舍」不需要代码（不加 wire 帧、不新增配置）。
- 类型一致：`compose_title(prompt) -> tuple[str, str]`、`clean_title(raw) -> str`、`title_source(message) -> str`、`AutoTitler.finish(timeout_s)` 在任务间一致。
- Review Focus 六条各自落在 Task 2/3 的测试里。
