# `read_image` 工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 agent 一个 `read_image` 工具，让它把磁盘上的图片作为图片块取回并看到；同时把目录里实测能识图的三个模型的 `supports_vision` 打开。

**Architecture:** 新工具是静态的内置工具，和 `read_file` 并列，核心复用现成的 `read_image_argument`。工具本身不看模型：纯文本模型由发送层已有的 `downgrade_unsupported_images` 换成占位文字。权限闸门、会话落盘、四个 provider 适配器、RPC 视图都不改逻辑，只把新名字登记进去。

**Tech Stack:** Python 3.11+，标准库 only（不引入图像库）；pytest（`asyncio_mode = "auto"`）；仓库自己的提示块注册表与 golden（`scripts/prompt_goldens.py`）。

**Spec:** `docs/superpowers/specs/2026-11-28-read-image-tool-design.md`

**执行前提**

- 在 `superpowers:using-git-worktrees` 建出的隔离 worktree / 分支里执行，不在 `main` 上提交。
- spec 和本计划在 `main` 的 checkout 里是未跟踪文件，新 worktree 看不到：把它们复制进去，作为分支的第一个提交（`docs: read_image spec and plan`）。

**与 spec 的出入（都是落点，不是行为）**

- 工具对读不出来的文件（`OSError`，例如无权限）也返回 `is_error=True`，和 spec 列的四种失败一样处理；spec 没列这一种。内核契约是「工具不能完成就返回错误，不抛异常」。
- spec 的「涉及文件」没列测试与文档：`tests/test_task.py`、`tests/test_tools_scoped.py`、`tests/prompt_fixtures.py`、`docs/guide/tools-permissions.md`、`docs/guide/tui.md`、`docs/internals/providers.md` 也要改，见 Task 1。
- `PROMPT_VERSION` 整个分支只升一次（`2026-11-28.1` → `2026-11-28.2`），三个任务共用一条 CHANGELOG 条目、后两个任务往里加要点。

## Global Constraints

- 内核只依赖 httpx：不新增依赖，不引入图像库。
- 单图上限 `MAX_IMAGE_BYTES = 5 * 1024 * 1024`；格式按 magic byte 判断，只认 png / jpeg / gif / webp，不看扩展名。
- 工具名 `read_image`，唯一参数 `path`，`approval="read"`；在 `ALL_TOOLS` 和 `base_tools()` 里紧跟 `read_file` 之后。
- 图片的 base64 不得进入 RPC 线上视图或日志。现有投影已经保证，不要新增序列化图片的路径。
- `read_file` 对非图片的二进制文件和非 UTF-8 文件的拒读文案不变。
- 每处提示内容变更都记进 `zenagent/prompts/CHANGELOG.md`。
- 新模块必须 mypy 干净（不在 ratchet 名单里），文件不超过 800 行。
- golden 只能用 `uv run python scripts/prompt_goldens.py` 重新生成，不手改。
- 每个任务结束时相关测试全绿；最后一个任务跑完整的 `bash scripts/check.sh`。

## Review Focus

1. 模型不支持图片时调用 `read_image`：请求必须照常成功，模型收到占位文字而不是字节。→ Task 1 `test_a_text_only_model_gets_a_placeholder_not_the_bytes`
2. 读取受保护路径下的图片（如 `~/.ssh/…`）：判定必须和 `read_file` 读同一路径完全一致。→ Task 1 `test_read_image_is_gated_exactly_like_read_file`
3. 扩展名会撒谎：`.png` 里是文本或 BMP 这类不支持的格式要报错，`.bin` 里是真 PNG 要能看。→ Task 1 `test_refuses_content_that_is_not_a_supported_image`、`test_returns_the_image_itself`
4. 路径指向目录或不存在的文件：返回错误，不抛异常。→ Task 1 `test_missing_file_and_directory_are_errors_not_exceptions`
5. JPEG 前 8KB 没有 NUL 字节：`read_file` 仍要指向 `read_image`，不能掉回「not valid UTF-8」。→ Task 2 `test_read_file_points_images_at_read_image` 的第三个参数

---

### Task 1: `read_image` 工具，接入工具名单与权限闸门

**Files:**
- Create: `zenagent/tools/read_image.py`
- Create: `zenagent/prompts/blocks/tools/read_image.md`
- Create: `tests/test_read_image.py`
- Modify: `zenagent/tools/__init__.py:16-52`（import、`__all__`、`ALL_TOOLS`）、`:96-133`（`base_tools`）
- Modify: `zenagent/app/permissions.py:57-71`（`READ_ONLY_TOOLS`）、`:82`（`_PATH_READ_TOOLS`）、`:105-109`（`_PATH_TARGET_TOOLS`）
- Modify: `zenagent/prompts/spec.py:27`（`PROMPT_VERSION`）、`:211-233`（`TOOL_BLOCK_NAMES`）、`:236-247`（`_TOOL_VARS`）
- Modify: `zenagent/prompts/CHANGELOG.md`（顶部新增一条）
- Modify: `tests/test_permissions.py`（`:206-221` 加一行断言，文件末尾加两个测试）
- Modify: `tests/test_task.py:134`、`tests/test_tools_scoped.py`（加一个测试）、`tests/prompt_fixtures.py:27`（注释）
- Modify: `docs/guide/tools-permissions.md`、`docs/guide/tui.md`、`docs/internals/providers.md`
- Regenerate: `tests/golden/prompts/*`

**Interfaces:**
- Consumes: `read_image_argument(path: Path, max_bytes: int = MAX_IMAGE_BYTES) -> ImageContent`（`zenagent/attachments.py`，读不了或不是图片时抛 `ValueError`）；`AgentTool(name, description, parameters, execute, approval, …)`（`zenagent/agent/loop.py`）；`ToolResultAgent(content, details, is_error, …)`（`zenagent/agent/protocol.py`）；`text_result(text, *, is_error=False, **details)`（`zenagent/tools/_result.py`）；`tool_description(name, **values)`（`zenagent/prompts`）。
- Produces: `READ_IMAGE: AgentTool`（`zenagent/tools/read_image.py`）——`name == "read_image"`，`approval == "read"`，`parameters` 见 Step 1 的第一个测试；成功时 `content == [TextContent(text=f"{path} ({mime}, {size} bytes)"), ImageContent(...)]`，`details == {"path": str(path), "mime_type": mime, "bytes": size}`；失败时 `content` 只有一个 `TextContent`，`is_error=True`。提示块 id `tool.read_image`，变量 `max_mb`。

- [ ] **Step 1: 写失败的测试**

`tests/test_read_image.py`（新文件）：

```python
"""The read_image tool: an image file comes back as an image block."""

import base64

import pytest

from zenagent.tools.read_image import READ_IMAGE

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 8


async def call(**args):
    return await READ_IMAGE.execute("call_1", args, None)


def test_declares_the_read_tier_and_one_path_argument():
    assert READ_IMAGE.approval == "read"
    assert READ_IMAGE.parameters == {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path of the image to view."}
        },
        "required": ["path"],
    }


@pytest.mark.parametrize(
    "payload, mime",
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp")],
)
async def test_returns_the_image_itself(tmp_path, payload, mime):
    path = tmp_path / "shot.bin"  # the extension is irrelevant: magic bytes decide
    path.write_bytes(payload)

    result = await call(path=str(path))

    assert not result.is_error
    [text, image] = result.content
    assert base64.b64decode(image.data) == payload
    assert image.mime_type == mime
    assert text.text == f"{path} ({mime}, {len(payload)} bytes)"
    assert result.details == {"path": str(path), "mime_type": mime, "bytes": len(payload)}


@pytest.mark.parametrize(
    "payload",
    [b"definitely not an image", b"BM" + b"\x00" * 30],  # text; a BMP header
)
async def test_refuses_content_that_is_not_a_supported_image(tmp_path, payload):
    path = tmp_path / "fake.png"
    path.write_bytes(payload)

    result = await call(path=str(path))

    assert result.is_error
    assert "does not look like" in result.content[0].text
    assert len(result.content) == 1


async def test_refuses_an_image_over_the_limit(tmp_path):
    path = tmp_path / "big.png"
    path.write_bytes(PNG + b"\x00" * (5 * 1024 * 1024))

    result = await call(path=str(path))

    assert result.is_error
    assert "the limit is 5242880" in result.content[0].text


@pytest.mark.parametrize("kind", ["missing", "directory"])
async def test_missing_file_and_directory_are_errors_not_exceptions(tmp_path, kind):
    path = tmp_path / "nope.png" if kind == "missing" else tmp_path

    result = await call(path=str(path))

    assert result.is_error
    assert len(result.content) == 1


async def test_a_text_only_model_gets_a_placeholder_not_the_bytes(tmp_path):
    """The tool never looks at the model: the wire projection does the downgrade."""
    from zenagent.providers.compat import CompatFlags
    from zenagent.providers.openai_completions import to_request_messages
    from zenagent.types import ToolResultMessage

    path = tmp_path / "shot.png"
    path.write_bytes(PNG)
    result = await call(path=str(path))
    message = ToolResultMessage(
        tool_call_id="c1", tool_name="read_image", content=result.content,
        is_error=False, timestamp=0,
    )

    downgraded = to_request_messages([message], CompatFlags(), supports_vision=False)
    assert downgraded == [{
        "role": "tool",
        "tool_call_id": "c1",
        "content": (
            f"{path} (image/png, {len(PNG)} bytes)\n"
            "(tool image omitted: model does not support images)"
        ),
    }]

    kept = to_request_messages([message], CompatFlags(), supports_vision=True)
    assert [m["role"] for m in kept] == ["tool", "user"]
    url = kept[1]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG


async def test_the_transcript_projection_names_the_image_without_its_bytes(tmp_path):
    from zenagent.app.views import _text_of

    path = tmp_path / "shot.png"
    path.write_bytes(PNG)
    result = await call(path=str(path))

    text = _text_of(result.content)

    assert text == f"{path} (image/png, {len(PNG)} bytes)\n[image image/png]"
    assert result.content[1].data not in text
```

`tests/test_permissions.py`：在 `test_every_builtin_tool_declares_an_approval_tier` 里 `assert tiers["read_file"] == "read"` 之后加一行，并在文件末尾追加两个测试：

```python
    assert tiers["read_image"] == "read"
```

```python
async def test_read_image_is_gated_exactly_like_read_file():
    """An image read is a file read: same verdicts, protected paths included."""
    gate = PermissionGate(interactive=False, mode="auto")
    for path in ("~/.zenagent/settings.json", "~/.ssh/id_ed25519", "/etc/hosts", "README.md"):
        assert await gate.check("read_image", {"path": path}) == await gate.check(
            "read_file", {"path": path}
        ), path


def test_read_image_is_in_every_read_tool_list():
    from zenagent.app import permissions

    for name in ("READ_ONLY_TOOLS", "_PATH_READ_TOOLS", "_PATH_TARGET_TOOLS"):
        assert "read_image" in getattr(permissions, name), name
```

`tests/test_tools_scoped.py` 末尾追加：

```python
async def test_read_image_resolves_under_the_bound_cwd(tmp_path):
    alpha = tmp_path / "alpha"
    alpha.mkdir()
    (alpha / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    result = await _tool(base_tools(_ask(), cwd=alpha), "read_image").execute(
        "c1", {"path": "shot.png"}, None, None
    )

    assert not result.is_error
    assert result.details["path"] == str(alpha / "shot.png")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest -q tests/test_read_image.py tests/test_tools_scoped.py tests/test_permissions.py`
Expected: `test_read_image.py` 在收集阶段报错（`ModuleNotFoundError: No module named 'zenagent.tools.read_image'`）；`test_read_image_resolves_under_the_bound_cwd`、`test_read_image_is_in_every_read_tool_list`、`test_read_image_is_gated_exactly_like_read_file` FAIL；其余 PASS。

- [ ] **Step 3: 新增提示块并登记**

新建 `zenagent/prompts/blocks/tools/read_image.md`，单行，内容和 `read_file.md` 一样不带结尾说明：

```
View an image file (png/jpeg/gif/webp, up to {{max_mb}} MB) with vision: screenshots you captured, images in the project. Returns the image itself, not text. A model without image input gets a placeholder instead.
```

改 `zenagent/prompts/spec.py`：`PROMPT_VERSION = "2026-11-28.2"`；`TOOL_BLOCK_NAMES` 在 `"read_file"` 后插入 `"read_image"`；`_TOOL_VARS` 加 `"read_image": ("max_mb",)`。

- [ ] **Step 4: 实现 `zenagent/tools/read_image.py`**

导出 `READ_IMAGE`（`__all__ = ["READ_IMAGE"]`）。执行函数签名：

```python
async def _read_image(tool_call_id: str, args: dict, abort, on_update=None) -> ToolResultAgent:
```

`READ_IMAGE = AgentTool(name="read_image", description=tool_description("read_image", max_mb=str(MAX_IMAGE_BYTES // (1024 * 1024))), parameters={…}, execute=_read_image, approval="read")`，`parameters` 与 Step 1 第一个测试里的字面量一致。函数体只有三步：`target = Path(args["path"]).expanduser()`；调用 `read_image_argument(target)`，捕获 `(ValueError, OSError)` 后 `return text_result(str(exc), is_error=True)`；成功时 `size = len(base64.b64decode(block.data))`，按 Interfaces 里的形状返回。

- [ ] **Step 5: 登记进工具名单**

`zenagent/tools/__init__.py`：`from zenagent.tools.read_image import READ_IMAGE`；`__all__` 加 `"READ_IMAGE"`；`ALL_TOOLS` 在 `READ_FILE` 后插入 `READ_IMAGE`；`base_tools()` 里 `cwd is None` 分支 `read_image = READ_IMAGE`，否则 `_bound(READ_IMAGE, cwd)`，返回列表里放在 `read_file` 之后。import 和 `__all__` 按字母序放置（ruff 会检查）。

- [ ] **Step 6: 登记进权限闸门**

`zenagent/app/permissions.py` 的三个集合各加 `"read_image"`：`READ_ONLY_TOOLS`、`_PATH_READ_TOOLS`、`_PATH_TARGET_TOOLS`。

- [ ] **Step 7: 更新钉住名单的既有测试**

- `tests/test_task.py:134`：期望集合加 `"read_image"`（非 auto 模式的子代理拿到的是父代理的只读工具，`read_image` 属于其中）。
- `tests/prompt_fixtures.py:27`：注释改成 `#: The 12 built-in tools, in base_tools order.`

`tests/prompt_fixtures.py` 的 `TOOL_GOLDEN_NAMES` 由 `ALL_TOOL_NAMES` 推导，Step 5 登记后自动含 `read_image`，不用手改。

- [ ] **Step 8: 重新生成 golden 并记 CHANGELOG**

Run: `uv run python scripts/prompt_goldens.py && uv run python scripts/prompt_goldens.py --check`
Expected: 第二条命令输出 `golden check ok: all files match`；`git status --short tests/golden/prompts` 只列出该目录下的 `tools.json` 和 `*.txt`。

取数字（下面命令在本次改动前的输出是 `8020`，即现有 20 个工具的描述总字符数；这就是条目里的基线）：

Run: `uv run python -c "import json; from zenagent.tools.read_image import READ_IMAGE; d=json.load(open('tests/golden/prompts/tools.json')); print(d['read_image']['chars'], sum(v['chars'] for v in d.values()), len(json.dumps(READ_IMAGE.parameters)))"`
Expected: 三个整数，依次是 N、M、K。

在 `zenagent/prompts/CHANGELOG.md` 顶部（`## 2026-11-28.1` 之前）新增，N / M / K 换成上面的数：

```markdown
## 2026-11-28.2 — `read_image` joins the roster

- One block is new: `tool.read_image` (N chars, budget 900). It documents the
  read-only tool that returns an image file as an image block, so the model can
  look at a screenshot it captured instead of measuring pixels with a script.
- Why a tool instead of widening `read_file`: `read_file` is text-only by
  contract (paging, the read-before-write gate). Images have none of that and
  ride the kernel's existing image path (budget pass-through, blob spill,
  per-adapter hoisting) unchanged.
- Tool budget impact: total description chars 8020 → M of 20000; the new tool's
  schema is K bytes.
```

- [ ] **Step 9: 更新文档**

- `docs/guide/tools-permissions.md`：内置工具表里 `read_file` 一行之后加
  `| \`read_image\` | 看图：png/jpeg/gif/webp，按 magic byte 判断（不看扩展名），单张 ≤5MB；把图片本身作为图片块返回给模型，不支持图片的模型收到占位文字 | 只读，免确认 |`；
  「权限判定顺序」第 2 点的 `read_file/grep/glob/list_dir/web_search/fetch/read_skill` 改成 `read_file/read_image/grep/glob/list_dir/web_search/fetch/read_skill`；
  「工具结果的形状」列表末尾加一条：``- `read_image` 的 content 是 `[TextContent, ImageContent]`：图片块不参与文本预算，`apply_result_budget` 原样保留。``
- `docs/guide/tui.md`：`/images clear` 那条（第 310 行）之后加
  `- 模型自己看图走 \`read_image\` 工具（例如它用 Playwright 截的图），见[工具与权限](tools-permissions.md)。`
- `docs/internals/providers.md`：「图片：tool result 里的图怎么到达模型」一节第一句之后加
  `内置工具里，\`read_image\` 和经 \`desktop\` 代理的 MCP 结果会带回图片块。`

- [ ] **Step 10: 跑测试与静态检查确认通过**

Run: `uv run pytest -q tests/test_read_image.py tests/test_tools_scoped.py tests/test_permissions.py tests/test_task.py tests/test_prompts_tools.py tests/test_prompts_budget.py tests/test_prompts_golden.py tests/test_layering.py && uv run ruff check zenagent tests && uv run mypy zenagent/tools/read_image.py`
Expected: 测试 PASS，ruff 无输出，mypy 报 `Success: no issues found in 1 source file`

- [ ] **Step 11: 提交**

```bash
git add zenagent/tools/read_image.py zenagent/tools/__init__.py zenagent/app/permissions.py zenagent/prompts tests/test_read_image.py tests/test_permissions.py tests/test_task.py tests/test_tools_scoped.py tests/prompt_fixtures.py tests/golden/prompts docs/guide docs/internals
git commit -m "feat(tools): read_image views an image file with vision"
```

---

### Task 2: 让模型找得到它——`read_file` 指路，提示里点名

**Files:**
- Modify: `zenagent/attachments.py:14-36`（`__all__`、`_sniff_mime` → `sniff_image_mime`）
- Modify: `zenagent/tools/fs.py:24-26`（import）、`:181`（`_read_file` 的拒读分支之前）
- Modify: `zenagent/prompts/blocks/tools/read_file.md`、`zenagent/prompts/blocks/plan_mode.md`、`zenagent/prompts/blocks/system/tool_policy.md`
- Modify: `zenagent/prompts/CHANGELOG.md`（`2026-11-28.2` 条目追加要点）
- Modify: `tests/test_images.py`、`tests/test_tools_fs.py:109`（在该测试之前插入）
- Regenerate: `tests/golden/prompts/*`

**Interfaces:**
- Consumes: `READ_IMAGE`（Task 1）；`read_image` 这个工具名。
- Produces: `sniff_image_mime(data: bytes) -> str | None`（`zenagent/attachments.py`，公开并进 `__all__`；`_sniff_mime` 这个名字不再保留）。

- [ ] **Step 1: 写失败的测试**

`tests/test_images.py` 末尾追加：

```python
def test_sniff_image_mime_is_public_and_ignores_the_extension():
    from zenagent.attachments import sniff_image_mime

    assert sniff_image_mime(PNG) == "image/png"
    assert sniff_image_mime(b"RIFF\x00\x00\x00\x00WAVEjunk") is None  # RIFF, but not WEBP
    assert sniff_image_mime(b"definitely not an image") is None
```

`tests/test_tools_fs.py` 在 `test_read_binary_file_is_refused` 之前插入：

```python
@pytest.mark.parametrize(
    "payload, mime",
    [
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "image/png"),
        # a JPEG header with and without NUL bytes: both old refusal paths must redirect
        (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 8, "image/jpeg"),
        (b"\xff\xd8\xff" + b"\xff" * 16, "image/jpeg"),
    ],
)
async def test_read_file_points_images_at_read_image(payload, mime):
    from pathlib import Path

    Path("shot.dat").write_bytes(payload)
    result = await call(READ_FILE, path="shot.dat")
    assert result.is_error
    assert result.content[0].text == (
        f"refusing to read shot.dat: it is an image ({mime}); call read_image to view it"
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest -q tests/test_images.py tests/test_tools_fs.py`
Expected: 新增的两个测试 FAIL（`ImportError`；`assert "it is an image" ...`），其余 PASS。

- [ ] **Step 3: 公开嗅探函数**

`zenagent/attachments.py`：`_sniff_mime` 改名为 `sniff_image_mime`，加进 `__all__`，`read_image_argument` 改用新名字。

- [ ] **Step 4: `read_file` 遇到图片时指路**

`zenagent/tools/fs.py`：`from zenagent.attachments import sniff_image_mime`。`_read_file` 里读完字节、在 `if _looks_binary(data):` 之前插入：

```python
    mime = sniff_image_mime(data)
    if mime is not None:
        return text_result(
            f"refusing to read {path}: it is an image ({mime}); "
            "call read_image to view it",
            is_error=True,
        )
```

- [ ] **Step 5: 提示里点名**

- `read_file.md`：`Binary files are refused.` 改成 `Binary files are refused. Images are refused as well; view them with read_image.`
- `plan_mode.md`：`(read_file, list_dir,` 改成 `(read_file, read_image, list_dir,`
- `system/tool_policy.md`：`must use read_file, list_dir,` 改成 `must use read_file, read_image, list_dir,`

- [ ] **Step 6: 重新生成 golden 并补 CHANGELOG**

Run: `uv run python scripts/prompt_goldens.py && uv run python scripts/prompt_goldens.py --check`
Expected: `golden check ok: all files match`

在 `zenagent/prompts/CHANGELOG.md` 的 `2026-11-28.2` 条目末尾追加：

```markdown
- `tool.read_file` gains one sentence (images are refused; view them with
  `read_image`); `plan_mode` and `system.tool_policy` name `read_image` among the
  read-only / specialized tools. Why: on 2026-11-18 and 2026-11-28 the model's own
  habit was to `read_file` a screenshot it had just captured and then fall back to
  measuring pixels; naming the tool where that habit lands is what makes it found.
```

- [ ] **Step 7: 跑测试确认通过**

Run: `uv run pytest -q tests/test_images.py tests/test_tools_fs.py tests/test_prompts_tools.py tests/test_prompts_budget.py tests/test_prompts_golden.py`
Expected: PASS（`test_read_binary_file_is_refused`、`test_read_non_utf8_is_refused` 不改一行，仍通过）

- [ ] **Step 8: 提交**

```bash
git add zenagent/attachments.py zenagent/tools/fs.py zenagent/prompts tests/test_images.py tests/test_tools_fs.py tests/golden/prompts
git commit -m "feat(tools): read_file points images at read_image"
```

---

### Task 3: 目录开关与全量验证

**Files:**
- Modify: `zenagent/catalog.yaml:39`（`zai-coding-cn/glm-5.3-flash`）、`:95`（`opencode-go/claude-haiku-5-5`）、`:117`（`opencode-go/deepseek-v4.1-flash`）
- Modify: `tests/test_catalog.py`（`test_supports_vision_is_parsed_and_defaults_to_false` 之后加一个测试）

**Interfaces:**
- Consumes: 无（不依赖前两个任务）。
- Produces: 三个模型条目的 `supports_vision` 变为 `true`；`/image`、ctrl+v、`--image`、RPC `prompt.images` 和发送层都读这个值。

- [ ] **Step 1: 写失败的测试**

```python
def test_vision_flags_follow_the_2026_11_28_live_probe():
    from zenagent.providers.catalog import resolve_model_ref

    catalog = load_catalog()
    for ref in (
        "opencode-go/deepseek-v4.1-flash",
        "opencode-go/claude-haiku-5-5",
        "zai-coding-cn/glm-5.3-flash",
    ):
        assert resolve_model_ref(catalog, ref).supports_vision is True, ref
    # Both answered HTTP 400 to an image part: text-only, whatever the family.
    for ref in ("zai-coding-cn/glm-5.3", "opencode-go/glm-5.3"):
        assert resolve_model_ref(catalog, ref).supports_vision is False, ref
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest -q tests/test_catalog.py`
Expected: 新测试 FAIL（前三个 `ref` 断言不成立），其余 PASS。

- [ ] **Step 3: 翻开三个开关**

三个条目各在 `reasoning: true` 的下一行加：

```yaml
        supports_vision: true   # live image request answered (2026-11-28)
```

缩进与同条目的其它键一致。其余条目不动：`zai-coding-cn/glm-5.3`、`opencode-go/glm-5.3` 实测拒收；`opencode-go/deepseek-v4-pro`、`deepseek/*` 没测出。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest -q tests/test_catalog.py`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add zenagent/catalog.yaml tests/test_catalog.py
git commit -m "fix(catalog): flag the models whose endpoints accept image input"
```

- [ ] **Step 6: 全量闸门**

Run: `bash scripts/check.sh`
Expected: lint、types、architecture budget、wire freshness、worktree hygiene、tests 全部通过，退出码 0。

- [ ] **Step 7: 手工验收（需要真模型和桌面端，交给用户，执行者不代做）**

桌面端用 `opencode-go/deepseek-v4.1-flash`：

1. 让它用 Playwright 截一张图并描述画面：应调用 `read_image`，描述与画面相符。
2. 输入框贴一张图发送：不再报「… does not accept images」，模型能说出图里有什么。
3. 切到 `zai-coding-cn/glm-5.3` 再贴图：应被拒，并说明该模型不支持图片。
