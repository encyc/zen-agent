# README 落地页重写 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 377 行的参考手册式 `README.md` 改成 ≤130 行的四段落地页，参考内容搬进 `docs/`，并补上 `[task]` 配置段与 MIT license。

**Architecture:** 纯文档改动 + 一条元数据改动（`pyproject.toml` 的 license 字段）。搬走的三块内容（斜杠命令表、`[task]` 段、测试/发版流程）分别落进两个新 docs 页面和一个已有页面；README 只保留决策级信息 + 绝对 URL 链接。两道形状守卫（`tests/test_readme.py`、`tests/test_docs_commands.py`）把"README 不许再长回去"和"命令文档不许漏命令"钉成测试。

**Tech Stack:** Markdown；构建后端 `uv_build`（uv 0.11.24）；门禁 `ruff` + `pytest`（`bash scripts/check.sh`）+ `bash scripts/release-check.sh`。

**Spec:** `docs/superpowers/specs/2026-11-02-readme-landing-redesign-design.md`

## Global Constraints

- README 用**英文**；`docs/` 全部保持**中文**（不新增中文 README）。
- 新 README **≤130 行**，四个二级标题固定为 `## Install`、`## What this is, and why`、`## Features`、`## Contributing`（顺序即此顺序）。
- 所有跨文件链接用绝对地址：`https://github.com/encyc/zen-agent/blob/main/<path>`。不得出现 `](docs/…`、`](CHANGELOG.md)` 这类相对链接（PyPI 渲染会失效）。
- 用户侧 Python 要求写 **3.11+**（`requires-python = ">=3.11"`）；`.python-version` 的 3.15 只能作为"开发工具链 pin"提及。不得再出现 `Requires Python 3.15+`。
- 量化数字只允许三个，且必须可复现：wheel 体积（构建后实测）、**1 个**运行时依赖（`httpx`）、启动 ~0.1 s（`zen --version` 实测）。其余用文字描述。
- 特性表**不收**以下四条（用户判定为普遍能力）：契约优先 / 离线测试 / 护栏与权限 / 会话即文件；**不写** `deepswe` 或任何找不到出处的分数。
- license = **MIT**；`pyproject.toml` 用 PEP 639 字符串形式 `license = "MIT"`。
- 现有 docs 页面的 `## 源码对照` 尾节风格必须延续（只写符号名，不贴行号）。

## Review Focus

- **PyPI 渲染**：README 被 `pyproject.toml::readme` 原样发布到 PyPI，相对链接在那里是死链 —— 陌生人从 PyPI 点"文档"应该落到 GitHub 上的 docs 页面。
- **版本口径漂移**：README 曾写 `Requires Python 3.15+` 而包声明 `>=3.11`，这个错话已经传播到 PyPI —— 两个数字必须由测试绑在一起。
- **搬运丢失**：45 条命令表搬出 README 时漏掉几条，用户就再也查不到（docs 里原本完全没有这张表）。
- **`[task]` 只存在于 plan 文档**：`docs/guide/config.md` 从未记录这个段，用户读文档会以为它不存在（项目层写 `[task]` 会被拒绝，这条规则也必须写清）。
- **README 膨胀回潮**：没有守卫，半年后又会有人把参考内容塞回来 —— 行数上限与四段标题必须成为断言。

---

### Task 1: MIT license 与包元数据

**Files:**
- Create: `LICENSE`
- Modify: `pyproject.toml`（`[project]` 表，`readme = "README.md"` 之后）

**Interfaces:**
- Consumes: 无
- Produces: 仓库根 `LICENSE`（MIT 全文）；wheel/sdist 的 `METADATA` 中出现 `License-Expression: MIT`，两个产物都带 `LICENSE` 文件。Task 5 的 README 末尾 "License: MIT" 链接指向它。

- [ ] **Step 1: 写 `LICENSE`（MIT 全文，版权行 `Copyright (c) 2026 Justin Gao`）**

作者与年份取自 `pyproject.toml::authors` 与当前年份；正文用标准 MIT 模板，不改一字。

- [ ] **Step 2: 在 `pyproject.toml` 的 `[project]` 表加两行**

```toml
license = "MIT"
license-files = ["LICENSE"]
```

放在 `requires-python = ">=3.11"` 之前。`uv_build` 支持 PEP 639 字符串形式；若构建时报 `license` 字段非法，改用 `license = { text = "MIT" }` 并在提交信息里记一句原因。

- [ ] **Step 3: 构建并核对元数据**

Run:
```bash
uv build --clear
python3 -c "
import zipfile,glob
w=sorted(glob.glob('dist/*.whl'))[-1]; z=zipfile.ZipFile(w)
meta=[n for n in z.namelist() if n.endswith('METADATA')][0]
text=z.read(meta).decode()
print([l for l in text.splitlines() if l.startswith(('License','Version'))])
print('LICENSE in wheel:', any(n.endswith('LICENSE') for n in z.namelist()))
"
```
Expected: 打印出 `License-Expression: MIT`（或 `License: MIT`）与 `Version: 0.7.2`，且 `LICENSE in wheel: True`。

- [ ] **Step 4: 跑打包门禁**

Run: `bash scripts/release-check.sh --skip-build`
Expected: `==> PASS  zenagent-py 0.7.2 is ready to publish`（`artifact contents` 一节仍 ok，说明 LICENSE 没有触发 dev/junk 判定）。

- [ ] **Step 5: Commit**

```bash
git add LICENSE pyproject.toml
git commit -m "chore: license the project under MIT"
```

---

### Task 2: 斜杠命令全表进 `docs/guide/commands.md`

**Files:**
- Create: `docs/guide/commands.md`
- Create: `tests/test_docs_commands.py`
- Modify: `docs/README.md`（第 2 组 "使用指南" 的链接列表里加一行）

**Interfaces:**
- Consumes: 现有 README 138–182 行的命令表（本任务搬走它，Task 5 才会从 README 删除）
- Produces: `docs/guide/commands.md`，一级标题 `# 斜杠命令`，每个命令以反引号形式 `/name` 出现；`tests/test_docs_commands.py::test_every_registered_command_is_documented`

- [ ] **Step 1: 写失败测试**

`tests/test_docs_commands.py`：

```python
"""The slash-command reference must list every command the app registers."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = (
    ROOT / "zenagent/app/commands.py",
    ROOT / "zenagent/cli/tui/app.py",
    ROOT / "zenagent/extensions.py",
)
DOC = ROOT / "docs/guide/commands.md"


def registered_commands() -> set[str]:
    names: set[str] = set()
    for path in SOURCES:
        names |= set(re.findall(r'_command\("([a-z][a-z-]*)"\)', path.read_text(encoding="utf-8")))
    return names


def test_every_registered_command_is_documented():
    names = registered_commands()
    assert len(names) > 40, names  # the scrape itself must not silently break
    text = DOC.read_text(encoding="utf-8")
    missing = sorted(name for name in names if f"`/{name}" not in text)
    assert not missing, f"docs/guide/commands.md is missing {missing}"


def test_doc_never_repeats_a_command_row():
    rows = re.findall(r"^\| `/([a-z][a-z-]*)", DOC.read_text(encoding="utf-8"), re.M)
    duplicates = sorted({name for name in rows if rows.count(name) > 1})
    assert not duplicates, f"duplicate command rows: {duplicates}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_docs_commands.py -q`
Expected: FAIL —— `FileNotFoundError`（`docs/guide/commands.md` 还不存在）。

- [ ] **Step 3: 写 `docs/guide/commands.md`**

结构（中文，延续 docs 风格）：

```
# 斜杠命令
两行说明：TUI 与 REPL 共用 app/commands.py 的同一个 CommandRegistry；`/help` 列出全部；不认识的命令会作为普通提问发送。
## 通用          | 命令 | 作用 |
## 会话与历史     （/new /clear /sessions /session /rename /pin /fork /undo /retry /compact /import /export /handoff /shake /fresh）
## 模型与模式     （/model /thinking /mode /plan-review）
## 输入与附加     （/image /images /add-dir /remove-dir /dirs /copy /urls /open /btw）
## 诊断与工具     （/doctor /tools /skills /reload /mcp /usage /context /todo /jobs /hotkeys /settings /trace /theme /lang）
## 参数补全      （Tab：/mode /thinking /model；TUI 内联候选）
## 执行中插话     （steering 语义：工作中的输入排队，不并发起第二轮）
## 源码对照      （app/commands.py::CommandRegistry / _command、app/links.py、对应测试）
```

内容以 `zenagent/app/commands.py` 的实际实现为准（用 `/help` 的描述文案），一条命令一行，作用列不超过 15 字。表格行格式必须是 ``| `/name args` | 作用 |``，与守卫测试的正则一致。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_docs_commands.py -q`
Expected: PASS（2 passed）。若 `missing` 非空，说明第 3 步漏了命令 —— 补进表格再跑。

- [ ] **Step 5: 在 `docs/README.md` 的"使用指南"列表里加链接**

在 `[CLI](guide/cli.md)` 之后插入 ` · [斜杠命令](guide/commands.md)`，保持该行原有顺序与分隔符风格。

- [ ] **Step 6: Commit**

```bash
git add docs/guide/commands.md docs/README.md tests/test_docs_commands.py
git commit -m "docs: move the slash-command reference out of the README"
```

---

### Task 3: 补两个 docs 空洞（`[task]` 段与发版流程）

**Files:**
- Modify: `docs/guide/config.md`（在第 133 行 `### [safety] 与 [backup]（仅全局层）` 之前插入新小节）
- Create: `docs/development.md`
- Modify: `docs/README.md`（第 5 组末尾加 ` · [开发与发版](development.md)`）

**Interfaces:**
- Consumes: `zenagent/config.py::DEFAULT_TASK_MAX_TURNS`（300）、`DEFAULT_TASK_KEEP_TRANSCRIPTS`（64）、`DEFAULT_TASK_MAX_TRANSCRIPT_BYTES`（32 MiB）、`DEFAULT_TASK_MAX_TRANSCRIPT_FILE_BYTES`（8 MiB）；`zenagent/tools/task.py` 的截断语义
- Produces: `docs/guide/config.md` 的 `### [task]（仅全局层）` 小节；`docs/development.md` 的 `# 开发与发版`

- [ ] **Step 1: 在 `docs/guide/config.md` 加 `[task]` 小节**

必须说清的四件事：① 四个键与默认值（`max_turns = 300`、`keep_transcripts = 64`、`max_transcript_bytes = 33554432`、`max_transcript_file_bytes = 8388608`）；② **仅全局层**，项目层写 `[task]` 会被拒绝（与 `[safety]`/`[backup]` 同规则）；③ `max_turns` 是子代理的轮次上限，超限时父模型看到 `status="truncated" reason="max_turns" turns="N"` 而不是错误；④ 四者都必须是正整数，0 或布尔值会被配置加载拒绝。表格形式与相邻 `[safety]` 小节一致。

- [ ] **Step 2: 写 `docs/development.md`**

结构：`# 开发与发版` → `## 环境`（`uv sync --extra cli`；`.python-version` 是 3.15 的开发 pin，包本身支持 3.11+）→ `## 三个闸门`（`bash scripts/check.sh` 是唯一必须过的门禁；`uv run pytest -q` 全离线；`./scripts/eval.sh` 花钱、可选）→ `## 打包门禁`（`bash scripts/release-check.sh` 检查什么：版本一致性、产物内容、kernel-only 安装、`[cli]` 安装；`--python 3.13` / `--skip-build`）→ `## 发版`（`check.sh` → `release-check.sh` → `git tag vX.Y.Z` → push tag 触发 publish workflow；手动 `gh workflow run publish.yml` 永远是 dry run）→ `## 源码对照`（`.github/workflows/{ci,publish}.yml`、`scripts/{check,release-check}.sh`、`tests/`）。内容改写自当前 README 310–357 行，但按 docs 风格（中文、符号名、不贴行号）。

- [ ] **Step 3: 更新 `docs/README.md` 索引**

第 5 组（`功能地图` / `测试策略` 那一行）末尾追加 ` · [开发与发版](development.md)`。

- [ ] **Step 4: 验证链接与命令可跑**

Run: `bash scripts/check.sh`（ruff 只扫 Python，此步主要确认没把测试弄坏）
然后人肉核对：`docs/development.md` 里出现的每条命令都在本仓库存在（`ls scripts/`、`ls .github/workflows/`）。

Expected: `1985 passed`（新增的 `test_docs_commands.py` 的 2 条使其变成 1987）。

- [ ] **Step 5: Commit**

```bash
git add docs/guide/config.md docs/development.md docs/README.md
git commit -m "docs: document the [task] budget and the release flow"
```

---

### Task 4: README 重写为四段落地页（含形状守卫）

**Files:**
- Create: `tests/test_readme.py`
- Rewrite: `README.md`（377 行 → ≤130 行）

**Interfaces:**
- Consumes: `pyproject.toml::requires-python`；Task 1 的 `LICENSE`；Task 2/3 产出的 `docs/guide/commands.md`、`docs/development.md`
- Produces: `tests/test_readme.py`（3 个测试：行数与四段标题、无相对链接且绝对链接指向真实文件、Python 口径一致）

- [ ] **Step 1: 写失败测试 `tests/test_readme.py`**

```python
"""The README is the landing page: short, four parts, no dead links on PyPI."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
TEXT = README.read_text(encoding="utf-8")
SECTIONS = ["## Install", "## What this is, and why", "## Features", "## Contributing"]
BLOB = "https://github.com/encyc/zen-agent/blob/main/"


def test_readme_is_a_landing_page_with_four_parts():
    assert len(TEXT.splitlines()) <= 130
    headings = re.findall(r"^## .+$", TEXT, re.M)
    assert headings == SECTIONS


def test_readme_links_are_absolute_and_resolve():
    relative = [t for t in re.findall(r"\]\((?!https?://)([^)#]+)", TEXT)]
    assert not relative, f"relative links break on PyPI: {relative}"
    for target in re.findall(re.escape(BLOB) + r"([^)\s]+)", TEXT):
        path = target.split("#")[0]
        assert (ROOT / path).exists(), f"README links a missing file: {path}"


def test_readme_python_floor_matches_the_package():
    requires = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["requires-python"]
    floor = requires.removeprefix(">=")
    assert floor == "3.11"
    assert "3.11" in TEXT
    assert "3.15+" not in TEXT
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_readme.py -q`
Expected: 3 failed —— 行数 377 > 130、`headings` 不等、README 有 3 处相对链接。

- [ ] **Step 3: 重写 `README.md`（严格照下面骨架，英文，≤130 行）**

```
# ZenAgent
3 个 badge：PyPI（zenagent-py）、CI（ci.yml）、Python 3.11+
一段定位（≤4 行）：a coding agent you can read, embed and change — 纯 Python，
  kernel 只依赖 httpx。读写文件、跑命令、搜索、派子代理；四种用法面。
Repositories 行：GitHub / PyPI 绝对链接

## Install
uv tool install "zenagent-py[cli]"  →  zen --version   （Python 3.11+）
API key：3 行表（zai-coding-cn / deepseek / opencode-go）+ 一句"也可以指向自己的 openai 兼容 endpoint"
一句升级说明：TUI 每小时至多查一次 PyPI，ZENAGENT_NO_UPDATE_CHECK=1 关掉
## Quick start 内的两个代码块（不新增二级标题）：
  zen                                  # TUI
  zen -p "explain this repo" --json    # headless，适合脚本/CI

## What this is, and why
6–8 条 bullet，F2 主线，只含已确认的卖点：
- 1 个运行时依赖（httpx）、wheel ~450 KB、启动 ~0.1 s（实测数字，构建后按实测改写）
- 纯 Python：pip 装、3.11–3.14、无 node runtime、无编译步骤
- 四个接入面共用一套内核：TUI / REPL / `zen -p` headless / `zen rpc`（JSONL）
- GUI 是客户端不是重写：RPC 面已存在，Tauri 应用在路线图上
- 嵌入 30 行（Agent + subscribe）+ 指向 docs/guide/rpc-sdk.md
- 扩展而不重启：skills / 模板 / extensions / MCP / 自定义 provider（/reload 生效）
- 无厂商锁定：openai-completions（含 zai/deepseek 方言）+ anthropic-messages，可指本地 endpoint
- 成本可见：catalog 单价、`zen usage`、`--max-cost`

## Features
6 条，每条 1–3 行 + 一条绝对链接：
1. 小到能读完的 kernel → architecture.md
2. 四个接入面（含 RPC → Tauri 路线）→ guide/tui.md、guide/cli.md、guide/rpc-sdk.md
3. 嵌入 SDK → guide/rpc-sdk.md
4. 扩展而不重启 → guide/{skills-templates,extensions,mcp}.md
5. 模型与成本 → guide/config.md、catalog.yaml
6. 路线图 → CHANGELOG.md

## Contributing
uv sync --extra cli ；bash scripts/check.sh 是唯一门禁
Docs 导航表 6 行（绝对链接）：architecture / guide/cli / guide/commands / guide/config /
  reference/contracts / development
一句实话：Not done: OS-level sandboxing —— fence 与快照防的是 agent 犯错，不是恶意仓库或提示注入。
License: MIT（链接到 LICENSE）
```

硬约束：四个 `##` 标题**只能有这四个**（测试断言 `headings == SECTIONS`），`## Docs`、`## Tests`、`## Architecture`、`## Tools`、`## Status` 一律不出现；`###` 子标题可以用。写完数一遍 `wc -l`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_readme.py -q`
Expected: 3 passed。链接报 `missing` 时，修链接路径而不是改测试。

- [ ] **Step 5: 复核被删内容确实存在于 docs**

逐条对照 spec 的"内容搬迁清单"：命令表→`guide/commands.md`（Task 2）、测试/发版→`development.md`（Task 3）、`[task]`→`guide/config.md`（Task 3）、工具清单→`guide/tools-permissions.md`、6 条契约→`reference/contracts.md`、config 全字段→`guide/config.md`、SDK→`guide/rpc-sdk.md`、Extending→`guide/{skills-templates,extensions,mcp}.md`、架构→`architecture.md`、`## Status`→`CHANGELOG.md`。缺哪个就补到对应 docs 页面，不要把内容塞回 README。

- [ ] **Step 6: Commit**

```bash
git add README.md tests/test_readme.py
git commit -m "docs: make the README a landing page"
```

---

### Task 5: 全量门禁与 PyPI 描述复核

**Files:**
- Modify: 无（只读验证；发现问题回到对应任务修）

**Interfaces:**
- Consumes: Task 1–4 的全部产物
- Produces: 一份"可以发布"的确认

- [ ] **Step 1: 跑唯一门禁**

Run: `bash scripts/check.sh`
Expected: ruff 无输出，pytest `1989 passed`（1985 + 2 命令守卫 + 3 README 守卫，允许上下 1 条误差但必须 0 failed）。

- [ ] **Step 2: 跑打包门禁并检查 README 进入产物**

Run:
```bash
uv build --clear && bash scripts/release-check.sh --skip-build
python3 -c "
import tarfile,glob
s=sorted(glob.glob('dist/*.tar.gz'))[-1]
t=tarfile.open(s); m=[x for x in t.getnames() if x.endswith('README.md')]
print('README in sdist:', m)
print(t.extractfile(m[0]).read().decode().count(chr(10)), 'lines')
"
```
Expected: `==> PASS`；sdist 含 `README.md` 且行数 ≤130（这就是 PyPI 将渲染的正文）。

- [ ] **Step 3: 核对 PyPI 关键渲染点**

人肉检查 `README.md`：无 `](docs/`、无 `](CHANGELOG.md)`、无相对图片；badge 的 URL 全是 http(s)；四段标题与 spec 一致。

- [ ] **Step 4: 汇报（不提交空 commit）**

把两个门禁的实际输出贴给用户；提示：**PyPI 上的项目页要到下一个 release 才会更新**，本次改动只在 GitHub 生效。

---

## Self-Review

- **Spec coverage**：搬迁清单 11 行 → Task 2（命令表）、Task 3（`[task]`、测试/发版）、Task 4 Step 5（其余 docs 已验证覆盖，README 删除）；四段结构 → Task 4；绝对链接/3.11 口径/数字限额 → Global Constraints + `tests/test_readme.py`；MIT → Task 1；放弃 deepswe 与 4–7 四条卖点 → Global Constraints（并在 Review Focus 之外显式禁止）。`docs/README.md` 索引两处 → Task 2 Step 5、Task 3 Step 3。
- **Step scan**：每个 Step 都有可执行动作或可判定输出；没有"适当处理"这类空话；README 骨架给的是内容约束而非逐字文案（文案属于实现者的判断，spec 已限定边界）。
- **Type consistency**：测试常量 `SECTIONS`、`BLOB`、`DOC`、`register('_command("…")')` 正则与 Task 2/4 的产出文件路径一致；`requires-python` 的解析方式在三处（Global Constraints、test_readme、Task 1 Step 3）口径一致（3.11）。
- **Review Focus**：5 条各自挂到测试 —— PyPI 相对链接 → `test_readme_links_are_absolute_and_resolve`；版本口径 → `test_readme_python_floor_matches_the_package`；搬运丢失 → `test_every_registered_command_is_documented`；`[task]` 缺失 → Task 3 Step 1（无测试，属文档内容，由 Step 4 的人工核对兜底）；膨胀回潮 → `test_readme_is_a_landing_page_with_four_parts`。
- **Proportion**：计划比 spec 长约 1.4 倍，主要为两个守卫测试的完整代码；README 正文没有逐字写死，避免计划替实现者写文案。
