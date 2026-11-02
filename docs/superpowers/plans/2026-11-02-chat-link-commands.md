# 会话链接命令（`/urls`、`/copy <n>`、`/open <n>`）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户从会话里取出链接：`/urls` 编号列出上一条回答中的 http(s) 链接，`/copy <n>` 逐字节原文复制，`/open <n>` 用默认浏览器打开——不经过会被裁切、解码改写的渲染层。

**Architecture:** 一个纯函数模块 `zenagent/app/links.py`（抽取 + 索引解析，无 IO），三个命令挂在现有的 `CommandRegistry`（`zenagent/app/commands.py`）上，i18n 与补全表各加条目。TUI 与 REPL 共用同一条路径，渲染层零改动。

**Tech Stack:** Python 3.11+、`re`、`webbrowser`（标准库，无新依赖）、pytest + pytest-asyncio、现有 `zenagent.i18n` / `CommandRegistry` / `write_clipboard_text`。

**Spec:** `docs/superpowers/specs/2026-11-02-chat-link-commands-design.md`

## Global Constraints

- 只加标准库依赖：`re`、`webbrowser`。不新增第三方依赖。
- `zenagent/app/**` 不得 import `textual` / `rich`。
- 每个新增面向用户的字符串都要在 `zenagent/i18n/en.py` 和 `zenagent/i18n/zh.py` 各有一条；测试里 locale 被 `conftest` 固定为 `en`，断言英文文案。
- 链接文本**逐字节保真**：不做 `urllib.parse` 的任何编码/解码（`%3A%2F%2F` 必须原样保留）。
- 命令层不允许异常冒泡（TUI 的 `_dispatch_command` 会把异常升成撕裂应用的错误）：`/open` 的失败路径必须 return `CommandResult`。
- 测试全部离线：不真的打开浏览器、不真的写系统剪贴板（都 monkeypatch）。
- 每个 Task 结尾提交一次，提交信息按仓库现有风格（`feat(...)` / `docs:` / `test:`）。

## Review Focus

1. 长 URL 落在代码块里（屏幕上被裁掉）——`/urls` 仍要列出完整长度、`/copy <n>` 仍要拿到全文。
2. URL 紧贴尾部标点或中文句号：`看 https://x.com/a)。` 复制到的不含 `)` 和 `。`。
3. 成对括号的 URL：`https://en.wikipedia.org/wiki/Foo_(bar)` 右括号必须保留。
4. `/open` 在无浏览器/被拒绝时 `webbrowser.open` 返回 False 或抛错——只出失败文案，应用不崩。
5. 空链接、越界索引、`/copy 0`——必须有明确文案，绝不静默复制空串或误复制字面数字。
6. 百分号编码保真：`redirect_uri=http%3A%2F%2F127.0.0.1%3A8971%2Fcallback` 复制后仍是 `%3A%2F%2F`。

---

### Task 1: `links.py` —— 抽取与索引解析

**Files:**
- Create: `zenagent/app/links.py`
- Test: `tests/test_app_links.py`（新建）

**Interfaces:**
- Produces:
  - `extract_links(text: str) -> list[str]` —— 按出现顺序返回去重后的 http/https 链接，原文保真。
  - `parse_index(argument: str) -> int | None` —— 纯数字或 `#数字` 返回该整数（含 `0`），其余返回 `None`。

- [ ] **Step 1: 写失败的测试**

```python
"""Link extraction: verbatim URLs from raw markdown, and `/urls` indexes."""

from zenagent.app.links import extract_links, parse_index

OAUTH = (
    "https://metabase.example.cn/oauth/authorize?response_type=code"
    "&redirect_uri=http%3A%2F%2F127.0.0.1%3A8971%2Fcallback&state=abc"
)


def test_extracts_a_url_from_a_fenced_code_block():
    text = f"打开这个链接：\n\n```\n{OAUTH}\n```\n"
    assert extract_links(text) == [OAUTH]


def test_extracts_markdown_link_targets_and_bare_urls():
    text = f"见 [文档](https://example.com/docs) 和 https://example.org/bare 。"
    assert extract_links(text) == ["https://example.com/docs", "https://example.org/bare"]


def test_strips_trailing_punctuation_and_chinese_period():
    text = "打开 (https://example.com/a)。另外 https://example.com/b, 也行"
    assert extract_links(text) == ["https://example.com/a", "https://example.com/b"]


def test_keeps_balanced_parentheses_in_a_url():
    text = "https://en.wikipedia.org/wiki/Foo_(bar)"
    assert extract_links(text) == ["https://en.wikipedia.org/wiki/Foo_(bar)"]


def test_keeps_percent_encoding_verbatim():
    assert extract_links(OAUTH)[0].endswith("state=abc")
    assert "%3A%2F%2F" in extract_links(OAUTH)[0]


def test_deduplicates_keeping_first_order():
    text = "https://a.example/1 https://b.example/2 https://a.example/1"
    assert extract_links(text) == ["https://a.example/1", "https://b.example/2"]


def test_ignores_non_http_schemes_and_relative_paths():
    assert extract_links("ftp://example.com/x 见 docs/guide.md") == []


def test_empty_text_yields_no_links():
    assert extract_links("") == []


def test_parse_index_accepts_digits_and_hash():
    assert parse_index("2") == 2
    assert parse_index(" #12 ") == 12
    assert parse_index("0") == 0


def test_parse_index_rejects_text_and_negative_numbers():
    assert parse_index("hello") is None
    assert parse_index("") is None
    assert parse_index("-1") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_app_links.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'zenagent.app.links'`

- [ ] **Step 3: 实现 `zenagent/app/links.py`**

模块 docstring 说明"渲染层按宽度裁切、按解析器解码，所以链接从原始 markdown 文本取"。实现要点：

```python
_LINK_RE = re.compile(r"""https?://[^\s<>"'`]+""")
#: 句末标点不算链接的一部分（中英混排都要覆盖）。
_TRAILING = ".,;:!?、。，）》】\"']}"
```

- `extract_links`：`findall`，逐条从尾部剥字符：循环 `while url and (url[-1] in _TRAILING or (url[-1] == ")" and url.count("(") < url.count(")")))` → `url = url[:-1]`；跳过剥空的结果；用 `dict.fromkeys` 或显式 seen 集合去重并保序。
- `parse_index`：`argument.strip().lstrip("#")`，`str.isdigit()` 为真才 `int(...)`，否则 `None`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_app_links.py -v`
Expected: PASS（11 passed）

- [ ] **Step 5: 提交**

```bash
git add zenagent/app/links.py tests/test_app_links.py
git commit -m "feat(app): extract session links from raw markdown"
```

---

### Task 2: `/urls` 命令

**Files:**
- Modify: `zenagent/app/commands.py`（在 `_copy` 之后新增 `_urls`；在 `build_default_commands` 的工厂元组里、`_copy` 旁边加入 `_urls`）
- Modify: `zenagent/i18n/en.py`、`zenagent/i18n/zh.py`（`cmd.urls.description`、`cmdmsg.urls_empty`、`cmdmsg.urls_row`）
- Modify: `zenagent/cli/completion.py`（`"/urls": "cmd.urls.description"`，放在 `"/copy"` 那一行附近）
- Test: `tests/test_app_commands.py`

**Interfaces:**
- Consumes: `extract_links(text: str) -> list[str]`（Task 1）、`AppServices.chat.last_assistant_text() -> str`（现有）、`t(key, **kwargs) -> str`（现有）。
- Produces: 命令名 `"urls"`；`CommandResult(message=...)` 的多行文本（无 screen/quit）。i18n 键 `cmd.urls.description`、`cmdmsg.urls_empty`、`cmdmsg.urls_row`，其中 `urls_row` 形如 `"{index}. {preview}  ({length} chars)"` / `"{index}. {preview}  （{length} 字符）"`。

- [ ] **Step 1: 写失败的测试**（追加到 `tests/test_app_commands.py`）

```python
async def services_with_answer(runtime_factory, answer):
    """AppServices whose chat has already answered once with `answer`."""
    from conftest import sequenced_stream
    from zenagent.app.chat import ChatController
    from zenagent.app.commands import AppServices
    from zenagent.app.models import ModelManager
    from zenagent.app.sessions import SessionManager

    runtime = await runtime_factory(stream_fn=sequenced_stream([answer]))
    chat = ChatController(runtime)
    await chat.submit("hi")
    await chat.wait_for_idle()
    return AppServices(runtime=runtime, chat=chat,
                       models=ModelManager(runtime), sessions=SessionManager(runtime))


async def test_urls_command_numbers_links_from_the_last_answer(runtime_factory):
    answer = (
        "先起回调：\n\n```\nhttps://metabase.example.cn/oauth/authorize?"
        "response_type=code&redirect_uri=http%3A%2F%2F127.0.0.1%3A8971%2Fcallback\n```\n"
        "文档见 [docs](https://example.com/docs)。\n"
    )
    services = await services_with_answer(runtime_factory, answer)
    result = await build_default_commands(services).dispatch("urls", "")
    assert result.message is not None
    assert "1. https://metabase.example.cn/oauth/authorize" in result.message
    assert "2. https://example.com/docs" in result.message


async def test_urls_command_reports_when_the_answer_has_no_links(services):
    result = await build_default_commands(services).dispatch("urls", "")
    assert result.message == "no links in the last answer"
```

（第二条断言值取自加入的英文文案；实现时以 `zenagent/i18n/en.py` 里写下的那句为准，两边保持一致。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_app_commands.py -k urls -v`
Expected: FAIL — `unknown command: urls`

- [ ] **Step 3: 加 i18n 键、命令与补全条目**

`zenagent/app/commands.py`（`_copy` 之后）：

```python
#: `/urls` 预览截断宽度；复制走 `/copy <n>`，不受此限制。
URL_PREVIEW_CHARS = 80


@_command("urls")
async def _urls(services: AppServices, argument: str) -> CommandResult:
    """List the links of the last answer, numbered for `/copy` and `/open`."""
    links = extract_links(services.chat.last_assistant_text())
    if not links:
        return CommandResult(message=t("cmdmsg.urls_empty"))
    rows = []
    for index, url in enumerate(links, start=1):
        preview = url if len(url) <= URL_PREVIEW_CHARS else url[: URL_PREVIEW_CHARS - 1] + "…"
        rows.append(t("cmdmsg.urls_row", index=index, preview=preview, length=len(url)))
    return CommandResult(message="\n".join(rows))
```

顶部 import：`from zenagent.app.links import extract_links`；`build_default_commands` 的元组里 `_copy` 后加 `_urls`。

i18n（en）：`"cmd.urls.description": "list the links of the last answer"`、
`"cmdmsg.urls_empty": "no links in the last answer"`、
`"cmdmsg.urls_row": "{index}. {preview}  ({length} chars)"`。
i18n（zh）：`"列上一条回答里的链接"`、`"上一条回答里没有链接"`、`"{index}. {preview}  （{length} 字符）"`。

`zenagent/cli/completion.py`：`"/urls": "cmd.urls.description",`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_app_commands.py -k urls -v` 再 `uv run pytest tests/test_i18n.py tests/test_tui_i18n.py -q`
Expected: 两个 urls 测试 PASS；i18n 测试 PASS（中英键位对齐）。

- [ ] **Step 5: 提交**

```bash
git add zenagent/app/commands.py zenagent/i18n/en.py zenagent/i18n/zh.py zenagent/cli/completion.py tests/test_app_commands.py
git commit -m "feat(app): /urls lists the links of the last answer"
```

---

### Task 3: `/copy <n>` 按索引复制原文

**Files:**
- Modify: `zenagent/app/commands.py`（`_copy` 增加索引分支；`cmd.copy.description` 文案更新）
- Modify: `zenagent/i18n/en.py`、`zenagent/i18n/zh.py`（`cmdmsg.url_copied`、`cmdmsg.url_index_invalid`、`cmdmsg.urls_empty` 复用 Task 2、`cmd.copy.description` 改写）
- Test: `tests/test_app_commands.py`

**Interfaces:**
- Consumes: `extract_links`、`parse_index`（Task 1）；`write_clipboard_text(text: str) -> bool`（现有，`zenagent/cli/tui/clipboard.py`，handler 内部 import）；测试助手 `services_with_answer(runtime_factory, answer)`（Task 2 Step 1 已加进 `tests/test_app_commands.py`，直接复用，不要重写）。
- Produces: `/copy <n>` 语义；i18n 键 `cmdmsg.url_copied`（`"{url}"` 由 handler 传入的完整原文）、`cmdmsg.url_index_invalid`（`"{count}"`）。

- [ ] **Step 1: 写失败的测试**

```python
async def test_copy_with_an_index_copies_the_url_verbatim(runtime_factory, monkeypatch):
    copied: list[str] = []
    monkeypatch.setattr(
        "zenagent.cli.tui.clipboard.write_clipboard_text",
        lambda text: copied.append(text) or True,
    )
    answer = (
        "```\nhttps://metabase.example.cn/oauth/authorize?response_type=code"
        "&redirect_uri=http%3A%2F%2F127.0.0.1%3A8971%2Fcallback\n```\n"
    )
    services = await services_with_answer(runtime_factory, answer)
    result = await build_default_commands(services).dispatch("copy", "1")
    assert copied and "%3A%2F%2F" in copied[0]
    assert result.message is not None


async def test_copy_keeps_literal_text_when_the_argument_is_not_a_number(services, monkeypatch):
    copied: list[str] = []
    monkeypatch.setattr(
        "zenagent.cli.tui.clipboard.write_clipboard_text",
        lambda text: copied.append(text) or True,
    )
    await build_default_commands(services).dispatch("copy", "hello world")
    assert copied == ["hello world"]


async def test_copy_with_an_out_of_range_index_reports_the_range(services):
    result = await build_default_commands(services).dispatch("copy", "9")
    assert result.message is not None and "9" not in result.message
    assert "0" in result.message or "no links" in result.message


async def test_copy_index_zero_never_copies_the_digit(services, monkeypatch):
    """`/copy 0` is an index (out of range), never the literal text "0"."""
    copied: list[str] = []
    monkeypatch.setattr(
        "zenagent.cli.tui.clipboard.write_clipboard_text",
        lambda text: copied.append(text) or True,
    )
    result = await build_default_commands(services).dispatch("copy", "0")
    assert copied == []
    assert result.message is not None
```

（第三条断言写成"不静默成功"即可：实现后按实际文案收紧成 `== t("cmdmsg.urls_empty")`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_app_commands.py -k copy -v`
Expected: FAIL —— 索引分支不存在，`/copy 1` 复制的是字面 `"1"`。

- [ ] **Step 3: 实现索引分支**

`_copy` 开头（`text = argument.strip() or ...` 之前）：

```python
    index = parse_index(argument)
    if index is not None:
        links = extract_links(services.chat.last_assistant_text())
        if not links:
            return CommandResult(message=t("cmdmsg.urls_empty"))
        if not 1 <= index <= len(links):
            return CommandResult(message=t("cmdmsg.url_index_invalid", count=len(links)))
        link = links[index - 1]
        if write_clipboard_text(link):
            return CommandResult(message=t("cmdmsg.url_copied", url=link))
        return CommandResult(message=t("cmdmsg.copy_failed"))
```

i18n（en）：`"cmdmsg.url_copied": "{url}"`、`"cmdmsg.url_index_invalid": "link index out of range (1-{count}); try /urls"`；
`"cmd.copy.description"` → `"copy text, or the last answer, or link <n> of it to the clipboard"`。
i18n（zh）：`"{url}"`、`"链接编号越界（1-{count}）；先看 /urls"`、`"把文本（或上一条回答、或其中第 <n> 条链接）复制到剪贴板"`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_app_commands.py -k copy -v` 再 `uv run pytest tests/test_repl_commands.py -q`
Expected: PASS。REPL 的 `/copy` 旧测试必须保持绿（字面参数路径没变）。

- [ ] **Step 5: 提交**

```bash
git add zenagent/app/commands.py zenagent/i18n/en.py zenagent/i18n/zh.py tests/test_app_commands.py
git commit -m "feat(app): /copy <n> copies link n of the last answer verbatim"
```

---

### Task 4: `/open <n>` 命令

**Files:**
- Modify: `zenagent/app/commands.py`（新增 `_open`；`build_default_commands` 元组加 `_open`）
- Modify: `zenagent/i18n/en.py`、`zenagent/i18n/zh.py`（`cmd.open.description`、`cmdmsg.url_opened`、`cmdmsg.url_open_failed`、`cmdmsg.url_usage`）
- Modify: `zenagent/cli/completion.py`（`"/open": "cmd.open.description"`）
- Test: `tests/test_app_commands.py`

**Interfaces:**
- Consumes: `extract_links`、`parse_index`；`webbrowser.open(url) -> bool`（handler 内部 import）；测试助手 `services_with_answer(runtime_factory, answer)`（Task 2 定义）。
- Produces: 命令名 `"open"`；i18n 键 `cmd.open.description`、`cmdmsg.url_opened`（`"{url}"`）、`cmdmsg.url_open_failed`（`"{url}"`）、`cmdmsg.url_usage`。

- [ ] **Step 1: 写失败的测试**

```python
async def test_open_command_opens_the_indexed_link(runtime_factory, monkeypatch):
    import webbrowser

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **kw: opened.append(url) or True)
    services = await services_with_answer(runtime_factory, "见 https://example.com/docs 。")
    result = await build_default_commands(services).dispatch("open", "1")
    assert opened == ["https://example.com/docs"]
    assert result.message is not None


async def test_open_command_survives_a_refusing_browser(runtime_factory, monkeypatch):
    """`webbrowser.open` false or raising must not tear the app down."""
    import webbrowser

    def boom(url, *args, **kwargs):
        raise OSError("no browser")

    monkeypatch.setattr(webbrowser, "open", boom)
    services = await services_with_answer(runtime_factory, "见 https://example.com/x 。")
    result = await build_default_commands(services).dispatch("open", "1")
    assert result.message is not None


async def test_open_command_without_an_index_prints_usage(services):
    result = await build_default_commands(services).dispatch("open", "")
    assert result.message is not None and "/urls" in result.message
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_app_commands.py -k open -v`
Expected: FAIL — `unknown command: open`

- [ ] **Step 3: 实现 `_open`**

```python
@_command("open")
async def _open(services: AppServices, argument: str) -> CommandResult:
    """Open link <n> of the last answer in the default browser."""
    import webbrowser

    index = parse_index(argument)
    if index is None or index < 1:
        return CommandResult(message=t("cmdmsg.url_usage"))
    links = extract_links(services.chat.last_assistant_text())
    if not links:
        return CommandResult(message=t("cmdmsg.urls_empty"))
    if index > len(links):
        return CommandResult(message=t("cmdmsg.url_index_invalid", count=len(links)))
    url = links[index - 1]
    try:
        opened = webbrowser.open(url)
    except Exception:  # noqa: BLE001 - a missing browser must not kill the TUI
        opened = False
    if not opened:
        return CommandResult(message=t("cmdmsg.url_open_failed", url=url))
    return CommandResult(message=t("cmdmsg.url_opened", url=url))
```

i18n（en）：`"cmd.open.description": "open link <n> of the last answer in the browser"`、
`"cmdmsg.url_opened": "opened {url}"`、
`"cmdmsg.url_open_failed": "could not open {url}"`、
`"cmdmsg.url_usage": "usage: /open <n> (see /urls for the numbers)"`。
i18n（zh）：`"在浏览器里打开上一条回答的第 <n> 条链接"`、`"已打开 {url}"`、
`"打不开 {url}"`、`"用法：/open <编号>（编号见 /urls）"`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_app_commands.py -k open -v` 再 `uv run pytest tests/test_i18n.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add zenagent/app/commands.py zenagent/i18n/en.py zenagent/i18n/zh.py zenagent/cli/completion.py tests/test_app_commands.py
git commit -m "feat(app): /open <n> opens a link from the last answer"
```

---

### Task 5: TUI 端到端 + 文档

**Files:**
- Test: `tests/test_tui_links.py`（新建）
- Modify: `docs/feature-map.md`（命令清单加三条）、`CHANGELOG.md`（Unreleased 段）

**Interfaces:**
- Consumes: 命令名 `"urls"` / `"copy"` / `"open"`（Task 2-4）、`ZenAgentApp.commands.dispatch(name, argument)`（现有）。

- [ ] **Step 1: 写失败的端到端测试**

```python
"""End to end: the link commands survive the TUI and reach the clipboard."""

from zenagent.cli.tui.app import ZenAgentApp
from conftest import sequenced_stream

ANSWER = (
    "回调监听已起在 127.0.0.1:8971：\n\n```\n"
    "https://metabase.example.cn/oauth/authorize?response_type=code"
    "&redirect_uri=http%3A%2F%2F127.0.0.1%3A8971%2Fcallback\n```\n"
)


async def test_urls_then_copy_round_trips_the_clipped_url(runtime_factory, monkeypatch):
    copied: list[str] = []
    monkeypatch.setattr(
        "zenagent.cli.tui.clipboard.write_clipboard_text",
        lambda text: copied.append(text) or True,
    )
    runtime = await runtime_factory(stream_fn=sequenced_stream([ANSWER]))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()

        listing = await app.commands.dispatch("urls", "")
        assert "https://metabase.example.cn/oauth/authorize" in listing.message

        result = await app.commands.dispatch("copy", "1")
        assert result.message is not None
        assert copied and "%3A%2F%2F" in copied[0]
        assert app.is_running
```

- [ ] **Step 2: 跑测试确认失败（在补文档前先确认它真能挂）**

Run: `uv run pytest tests/test_tui_links.py -v`
Expected: PASS（Task 2-4 已实现）；若 FAIL 说明命令没进 TUI 的 registry，回 Task 2/4 修。

- [ ] **Step 3: 文档**

`docs/feature-map.md`：在命令清单里 `/copy` 附近加 `/urls`（列链接）、`/copy <n>`（原文复制第 n 条）、`/open <n>`（浏览器打开）。
`CHANGELOG.md` 的 `Unreleased` 段加一条：`feat(tui): /urls, /copy <n>, /open <n> — read links out of the transcript verbatim`。

- [ ] **Step 4: 全量验证**

Run: `uv run pytest -q`
Expected: 全绿（无回归）。

- [ ] **Step 5: 提交**

```bash
git add tests/test_tui_links.py docs/feature-map.md CHANGELOG.md
git commit -m "docs: document the link commands; cover them end to end"
```
