# Project-Scoped Runtime Phase A — Root Threading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every `Runtime` an explicit working directory and project identity root so two projects can run in one process without touching the process cwd, while preserving today's single-project behavior.

**Architecture:** Add `RuntimeOptions.root` (requested working directory) and resolve two fields at bootstrap: `Runtime.cwd` (tools, status, relative file operations) and `Runtime.root` (nearest `.git`; sessions, config, MCP, skills, templates, memory, permission policy). Thread those fields through bootstrap resources, session storage, tool instances, commands, chat status, and RPC handlers. CLI keeps `os.chdir` only as a compatibility shim; the runtime no longer reads `Path.cwd()` as its identity.

**Tech Stack:** Python 3.11+, pytest (asyncio auto mode), dataclasses, existing `AgentTool` contract.

**Spec:** `docs/research/project-scoped-runtime-audit.md`

## Global Constraints

- Work only in `/Users/mac/zen-agent/.worktrees/project-scoped-runtime` on branch `refactor/project-scoped-runtime`.
- Phase A is behavior-preserving for every existing entry point: no RPC wire/frame changes, `PROTOCOL_VERSION` stays `2`, no UI copy changes.
- No new runtime or dev dependencies.
- Tests must never read or write the real `~/.zenagent`; the autouse isolation fixtures in `tests/conftest.py` stay in force.
- Do not use `monkeypatch.chdir` in new tests; pass `root=` explicitly.
- Session writes stay lazy: no test may leave an `(empty)` transcript behind.
- Commit after each task with the exact English conventional message given; never touch the uncommitted docs in the main worktree.
- Every task ends with its targeted tests green before its commit.

## Scope Decisions

1. **Two path concepts, not one.** `Runtime.cwd` is the directory the process was asked to work in (tools, status, relative RPC paths); `Runtime.root` is the resolved project identity (nearest `.git`). This keeps sub-directory launches working (`read_file("x")` still means `<launch-dir>/x`) while giving project state a stable key. The desktop passes the project folder as `root`, so there `cwd == root`.
2. **Tools bind to `cwd`, not `root`.** Relative tool paths follow the working directory, matching today's shell semantics. A slot in Phase B will pass the project folder as its `root`, which makes both values equal again.
3. **Deferred to Phase B on purpose:** permission-gate classification of relative paths, `ToolContext.cwd`, taiji ownership, and task-manager event tags. Phase A must not change their behavior; in the CLI they remain process-scoped and correct.
4. **`_recursive_rm_reason` keeps the process cwd check.** Switching it to `policy.workspace` would stop flagging `rm -rf .` when launched from a sub-directory — a safety regression. Phase B can replace it once the slot root is the tool working directory.
5. **`os.chdir` stays in `zenagent rpc`.** It is a compatibility shim for code that still assumes the process cwd (taiji, LSP, extensions). It is no longer the runtime's identity source.
6. **Fork/rewrite/shake.** Fork and rewrite accept an optional `root`; `zenagent/app/shake.py` is a UI-only action and stays process-scoped in Phase A.

## File Map

| File | Responsibility |
|---|---|
| `zenagent/app/runtime.py` | Own `cwd`/`root`; use them for config, policy, MCP, skills, templates, prompt, memory, session creation. |
| `zenagent/app/bootstrap.py` | Build the broker and user config for the same requested root. |
| `zenagent/app/records.py` | Carry a root into a newly created `Session` header. |
| `zenagent/agent/session.py` | Root-aware session paths; header `cwd`; fork/rewrite root. |
| `zenagent/app/sessions.py` | Project meta, fork, undo, adopt through `runtime.root`. |
| `zenagent/app/retry.py`, `zenagent/app/chat.py` | Retry rewrite root; status reports `runtime.cwd`. |
| `zenagent/tools/__init__.py` | `base_tools(..., cwd=)` builds bound filesystem/search instances. |
| `zenagent/tools/bash.py`, `zenagent/tools/shell.py` | Bash default cwd from the bound directory. |
| `zenagent/app/commands.py` | `/dirs` and `/handoff` use `runtime.cwd`. |
| `zenagent/cli/rpc/server.py` | `RpcServer.root`/`RpcServer.cwd` seams; worktrees use `cwd`. |
| `zenagent/cli/rpc/ops_files.py`, `ops_config.py` | File ops use `cwd`; project config/MCP/skills use `root`. |
| `zenagent/cli/rpc/cli.py` | Pass `root=Path.cwd()` explicitly to `RuntimeOptions`. |
| `tests/conftest.py` | Session-dir monkeypatches accept the new `root` keyword. |
| `tests/test_app_runtime.py`, `test_session.py`, `test_tools_scoped.py` (new), `test_app_commands.py`, `test_project_rpc.py`, `test_project_scoped_runtime.py` (new) | Regression + acceptance coverage. |

---

### Task 1: Runtime cwd/root and bootstrap resources

**Files:**
- Modify: `zenagent/app/runtime.py:114-148` (`RuntimeOptions`), `:154-225` (`Runtime.__init__`), `:317-405` (bootstrap), `:529-592` (`_start_mcp`, `_touch_project`, `_note_session_scope`), `:594-627` (`_build_prompt`), `:633-705` (`_recompose`), `:785-812` (`refresh_skills`), `:849-880` (`rediscover`)
- Modify: `zenagent/app/bootstrap.py:53-84`
- Test: `tests/test_app_runtime.py`, `tests/test_bootstrap.py`

**Interfaces:**
- Consumes: `project.find_project_root(cwd)`, `load_user_config(cwd=)`, `load_mcp_config(path, cwd=)`, `default_skill_roots(cwd)`, `default_template_roots(cwd)`, `GuardPolicy.from_config(cwd=)`.
- Produces: `RuntimeOptions.root: Path | str | None`; `Runtime.cwd: Path`; `Runtime.root: Path`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app_runtime.py`:

```python
async def test_explicit_root_scopes_the_runtime(runtime_factory, tmp_path):
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    nested = root / "src"
    nested.mkdir()

    runtime = await runtime_factory(root=nested)

    assert runtime.cwd == nested.resolve()
    assert runtime.root == root.resolve()
    assert runtime.memory_store is not None
    assert runtime.memory_store.root == root.resolve()
    assert runtime.gate.policy.workspace == root.resolve()
    assert f"Working directory: {nested.resolve()}" in runtime.agent.system_prompt


async def test_bootstrap_rejects_a_missing_root(tmp_path):
    missing = tmp_path / "gone"
    with pytest.raises(RuntimeError, match="project root is not a directory"):
        await Runtime.bootstrap(
            RuntimeOptions(root=missing, mcp=False, skills=False, templates=False)
        )
```

Update the fake in `tests/test_bootstrap.py` so the new `cwd=` keyword is accepted:

```python
    monkeypatch.setattr(
        bootstrap_mod, "load_user_config", lambda *args, **kwargs: SimpleNamespace()
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_app_runtime.py::test_explicit_root_scopes_the_runtime tests/test_app_runtime.py::test_bootstrap_rejects_a_missing_root -q`

Expected: FAIL — `RuntimeOptions.__init__() got an unexpected keyword argument 'root'` (first test), and the bootstrap test fails the same way.

- [ ] **Step 3: Add the two runtime fields**

In `zenagent/app/runtime.py`, add the option field at the top of `RuntimeOptions`:

```python
@dataclass
class RuntimeOptions:
    """Everything bootstrap needs; plain values so any frontend can build it."""

    #: Working/project directory this runtime is scoped to. ``None`` keeps the
    #: historical behaviour: discover from the process cwd.
    root: Path | str | None = None
    model_id: str | None = None
```

In `Runtime.__init__`, immediately after `self.model = None`:

```python
        #: Directory tools resolve relative paths against and status reports.
        self.cwd: Path = Path.cwd()
        #: Project identity root (nearest ``.git``): sessions, config, MCP,
        #: skills, templates, memory and the permission policy hang off it.
        self.root: Path = self.cwd
```

- [ ] **Step 4: Resolve both fields at the top of bootstrap**

Replace the first line of `Runtime.bootstrap` (`runtime = cls()`) with:

```python
        runtime = cls()
        requested = (
            Path(options.root).expanduser()
            if options.root is not None
            else Path.cwd()
        )
        runtime.cwd = requested.resolve()
        if not runtime.cwd.is_dir():
            raise RuntimeError(f"project root is not a directory: {runtime.cwd}")
        runtime.root = project.find_project_root(runtime.cwd)
        runtime.user_config = load_user_config(cwd=runtime.root)
```

Then replace the remaining process-cwd reads in the same file:

```python
        policy = GuardPolicy.from_config(
            runtime.user_config,
            cwd=runtime.root,
            allow_outside=options.allow_outside,
            allow_destructive=options.allow_destructive,
            allow_protected=options.allow_protected,
        )
```

```python
            runtime.skills, warnings = discover_skills(
                default_skill_roots(runtime.root)
            )
```

```python
            runtime.templates, warnings = discover_templates(
                default_template_roots(runtime.root)
            )
```

```python
    async def _start_mcp(self, options: RuntimeOptions) -> McpManager | None:
        configs = load_mcp_config(options.mcp_file, cwd=self.root)
```

```python
    def _touch_project(self) -> None:
        """Record the project root/last_used; never fail a run over metadata."""
        with contextlib.suppress(OSError):
            project.write_project_meta(root=self.root)
```

```python
        owner = project.project_key_for_session(path)
        current = project.project_key(self.root)
        if owner is not None and owner != current:
            self.warnings.append(
                f"session belongs to project {owner}; tools still run in {self.cwd}"
            )
        elif owner is None:
            self.warnings.append(f"legacy (unscoped) session; tools run in {self.cwd}")
```

```python
            load_context_files(
                discover_context_files(self.cwd),
                max_bytes=self.limits.max_context_bytes,
            )
```

```python
        context = PromptContext(
            cwd=self.cwd,
```

```python
        self.memory_store = MemoryStore(self.root)
```

```python
        skills, warnings = discover_skills(default_skill_roots(self.root))
```

```python
            skills, warnings = discover_skills(default_skill_roots(self.root))
```

```python
            templates, warnings = discover_templates(
                default_template_roots(self.root)
            )
```

- [ ] **Step 5: Build the broker for the same requested root**

In `zenagent/app/bootstrap.py`, replace the broker construction:

```python
    project_cwd = Path(options.root) if options.root is not None else None
    broker = build_broker_for(
        user_config=load_user_config(cwd=project_cwd),
        mode=options.mode or "ask",
        read_only=options.read_only,
        assume_yes=assume_yes,
        allow_dangerous=options.allow_dangerous,
        allow_outside=options.allow_outside,
        allow_destructive=options.allow_destructive,
        allow_protected=options.allow_protected,
        cwd=project_cwd,
    )
```

Add `from pathlib import Path` to the imports.

- [ ] **Step 6: Run the targeted tests**

Run: `uv run pytest tests/test_app_runtime.py tests/test_bootstrap.py tests/test_project_scoping.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add zenagent/app/runtime.py zenagent/app/bootstrap.py tests/test_app_runtime.py tests/test_bootstrap.py
git commit -m "refactor(runtime): thread an explicit project root through bootstrap"
```

---

### Task 2: Root-aware session storage and headers

**Files:**
- Modify: `zenagent/agent/session.py:83-120` (path helpers), `:350-423` (fields/header), `:606-639` (fork/rewrite), `:741-765` (`latest_session`)
- Modify: `zenagent/app/records.py:153-165`
- Modify: `zenagent/app/runtime.py:543-569`, `:966-990`
- Modify: `zenagent/app/sessions.py:120-129`, `:225-267`
- Modify: `zenagent/app/retry.py:32-60`
- Modify: `zenagent/app/chat.py:493`
- Test: `tests/test_session.py`; update session-dir monkeypatches in `tests/conftest.py:20`, `tests/test_templates.py:82`, `tests/test_session.py:52`, `tests/test_session.py:472`, `tests/test_compact.py:94`, `tests/test_extensions.py:101`

**Interfaces:**
- Consumes: `Runtime.cwd` and `Runtime.root` from Task 1.
- Produces:
  - `sessions_dir(*, root: Path | str | None = None) -> Path`
  - `session_path(prefix: str = "", *, root=None) -> Path`
  - `unique_session_path(prefix: str = "", *, root=None) -> Path`
  - `latest_session(*, scope: str = "project", root=None) -> Path | None`
  - `records.open_session(path, *, lazy=False, cwd=None) -> Session`
  - `Session.cwd: Path | str | None`; `Session.fork_at(..., root=None)`; `Session.rewrite(..., root=None)`
  - `prepare_retry(agent, session, *, root=None)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session.py`:

```python
def test_session_helpers_accept_an_explicit_root(tmp_path):
    from zenagent import project

    root = tmp_path / "project"
    root.mkdir()

    path = unique_session_path(root=root)

    assert path.parent == project.project_sessions_dir(root=root)
    assert latest_session(root=root) is None


def test_a_new_session_header_records_the_explicit_root(tmp_path):
    import json

    root = tmp_path / "project"
    root.mkdir()
    session = Session(unique_session_path(root=root), cwd=root)

    session.materialize()

    header = json.loads(session.path.read_text(encoding="utf-8").splitlines()[0])
    assert header["cwd"] == str(root)
```

The helpers are imported at the top of `tests/test_session.py` already; if a name is missing, extend that import list. Update every session-dir monkeypatch to accept the keyword:

```python
lambda *, root=None: tmp_path / "sessions"
```

Apply to `tests/conftest.py:20`, `tests/test_templates.py:82`, `tests/test_session.py:52`, `tests/test_session.py:472`, `tests/test_compact.py:94`, and `tests/test_extensions.py:101`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_session.py::test_session_helpers_accept_an_explicit_root tests/test_session.py::test_a_new_session_header_records_the_explicit_root -q`

Expected: FAIL — `unique_session_path() got an unexpected keyword argument 'root'`.

- [ ] **Step 3: Make session path helpers root-aware**

Replace the helpers in `zenagent/agent/session.py`:

```python
def sessions_dir(*, root: Path | str | None = None) -> Path:
    """Session files for one project (never created here)."""
    return project.project_sessions_dir(root=root)


def all_session_dirs() -> list[Path]:
    """Legacy directory plus every known project's session directory."""
    return project.all_session_dirs()


def session_path(prefix: str = "", *, root: Path | str | None = None) -> Path:
    """A fresh session path: ``<date>-<time>[-prefix].jsonl``."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"-{prefix}" if prefix else ""
    return sessions_dir(root=root) / f"{stamp}{suffix}.jsonl"
```

```python
def unique_session_path(
    prefix: str = "", *, root: Path | str | None = None
) -> Path:
    """Like :func:`session_path`, but never returns an existing (or reserved) file.

    The timestamp has second resolution, so two sessions started in the same
    second would otherwise share a filename -- and a "new" session would load
    the existing transcript instead of starting empty. Lazy sessions hold their
    path before the file exists, so the reservation set tracks them too.
    """
    path = session_path(prefix, root=root)
    counter = 0
    while path.exists() or path in _RESERVED_PATHS:
        counter += 1
        path = path.with_name(f"{path.stem}-{counter}{path.suffix}")
    _RESERVED_PATHS.add(path)
    return path
```

- [ ] **Step 4: Record the root in a new session header**

Add the field to `Session` after `derived_from`:

```python
    #: Project root recorded in a new file's header; ``None`` records the
    #: process cwd (the pre-root behaviour).
    cwd: Path | str | None = None
```

In `_ensure_header`, replace `"cwd": os.getcwd(),` with:

```python
                "cwd": str(self.cwd) if self.cwd is not None else os.getcwd(),
```

- [ ] **Step 5: Thread root through fork/rewrite**

Replace `fork_at` and `rewrite` signatures and constructors:

```python
    def fork_at(
        self, index: int, *, keep: Callable[[dict], bool], root: Path | None = None
    ) -> Path:
```

```python
        forked = Session(
            unique_session_path(f"fork{index}", root=root),
            derived_from=self.path.stem,
            cwd=root,
        )
```

```python
    def rewrite(
        self,
        messages: list[Message],
        *,
        keep: Callable[[dict], bool],
        root: Path | None = None,
    ) -> Path:
        """Write ``messages`` to a *new* session file and return its path."""
        rewritten = Session(
            unique_session_path("rewrite", root=root),
            derived_from=self.path.stem,
            cwd=root,
        )
```

Replace the `scope="project"` branch of `latest_session`:

```python
def latest_session(
    *, scope: str = "project", root: Path | str | None = None
) -> Path | None:
```

```python
    if scope == "project":
        directory = sessions_dir(root=root)
```

- [ ] **Step 6: Carry the root through session creation in the app layer**

In `zenagent/app/records.py`:

```python
def open_session(
    path: Path,
    *,
    lazy: bool = False,
    cwd: Path | str | None = None,
) -> Session:
```

```python
    return Session(path, boundary=is_compaction, lazy=lazy, cwd=cwd)
```

In `zenagent/app/runtime.py`:

```python
            path = latest_session(root=self.root) if resume == "latest" else Path(resume)
```

```python
                return records.open_session(Path(path), cwd=self.root)
```

```python
        self.session = records.open_session(
            unique_session_path(root=self.root), lazy=True, cwd=self.root
        )
```

```python
            session = records.open_session(path, cwd=self.root)
```

In `zenagent/app/sessions.py`:

```python
    def _touch_project_meta(self) -> None:
        """Record the project root/last_used; never fail a session over it."""
        try:
            project.write_project_meta(root=self.runtime.root)
        except OSError:
            pass
```

```python
        target = Session(
            unique_session_path("fork", root=self.runtime.root),
            derived_from=source.stem,
            cwd=self.runtime.root,
        )
```

```python
        new_path = Session(source).rewrite(
            truncated,
            keep=records.keep_for_rewrite(),
            root=self.runtime.root,
        )
```

```python
        self.runtime.session = records.open_session(new_path, cwd=self.runtime.root)
```

```python
    def adopt(self, path: Path) -> Path:
        """Move a legacy session into this project; see :func:`adopt_legacy_session`."""
        return adopt_legacy_session(path, cwd=self.runtime.root)
```

In `zenagent/app/retry.py`:

```python
def prepare_retry(
    agent: Agent, session: Session, *, root: Path | None = None
) -> tuple[str, Session] | None:
```

```python
    new_path = Session(session.path).rewrite(
        truncated, keep=records.keep_for_rewrite(), root=root
    )
```

In `zenagent/app/chat.py`:

```python
        prepared = prepare_retry(
            self.runtime.agent, self.runtime.session, root=self.runtime.root
        )
```

- [ ] **Step 7: Run the session and app-session suites**

Run: `uv run pytest tests/test_session.py tests/test_app_sessions.py tests/test_app_undo_retry.py tests/test_app_runtime.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add zenagent/agent/session.py zenagent/app/records.py zenagent/app/runtime.py zenagent/app/sessions.py zenagent/app/retry.py zenagent/app/chat.py tests/conftest.py tests/test_session.py tests/test_templates.py tests/test_compact.py tests/test_extensions.py
git commit -m "refactor(sessions): scope session storage and headers to a project root"
```

---

### Task 3: Bind filesystem, search, and bash tools to a runtime cwd

**Files:**
- Modify: `zenagent/tools/__init__.py`
- Modify: `zenagent/tools/bash.py:85-105`, `:207-224`
- Modify: `zenagent/tools/shell.py:142-154`
- Modify: `zenagent/app/runtime.py:434-437`, `:654-657`
- Test: `tests/test_tools_scoped.py` (new)

**Interfaces:**
- Consumes: `Runtime.cwd` from Task 1.
- Produces:
  - `base_tools(ask_tool, *, shell_max_output_bytes=None, cwd: Path | None = None)`
  - `build_bash_tool(max_output_bytes: int | None = None, *, cwd: Path | None = None)`
  - `resolve_cwd(raw, base: Path | None = None)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tools_scoped.py`:

```python
"""Bound tool instances resolve relative paths under their own cwd."""

import sys

from zenagent.tools import base_tools
from zenagent.tools.ask import build_ask_tool


def _tool(tools, name):
    return next(tool for tool in tools if tool.name == name)


def _ask():
    return build_ask_tool(lambda: None)


async def test_fs_tools_resolve_under_the_bound_cwd(tmp_path):
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    (alpha / "note.txt").write_text("alpha note\n", encoding="utf-8")
    (beta / "note.txt").write_text("beta note\n", encoding="utf-8")

    alpha_read = await _tool(base_tools(_ask(), cwd=alpha), "read_file").execute(
        "c1", {"path": "note.txt"}, None, None
    )
    beta_read = await _tool(base_tools(_ask(), cwd=beta), "read_file").execute(
        "c1", {"path": "note.txt"}, None, None
    )

    assert alpha_read.content[0].text == "alpha note\n"
    assert beta_read.content[0].text == "beta note\n"


async def test_search_tools_default_to_the_bound_cwd(tmp_path):
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    (alpha / "one.py").write_text("alpha marker\n", encoding="utf-8")
    (beta / "two.py").write_text("beta marker\n", encoding="utf-8")

    alpha_tools = base_tools(_ask(), cwd=alpha)
    globbed = await _tool(alpha_tools, "glob").execute(
        "c1", {"pattern": "*.py"}, None, None
    )
    grepped = await _tool(alpha_tools, "grep").execute(
        "c1", {"pattern": "marker"}, None, None
    )

    assert "one.py" in globbed.content[0].text
    assert "two.py" not in globbed.content[0].text
    assert "alpha marker" in grepped.content[0].text
    assert "beta marker" not in grepped.content[0].text


async def test_bash_defaults_to_the_bound_cwd(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    command = (
        f'{sys.executable} -c "'
        "import pathlib; print(pathlib.Path.cwd().resolve())"
        '"'
    )

    result = await _tool(base_tools(_ask(), cwd=root), "bash").execute(
        "c1", {"command": command}, None, None
    )

    assert result.content[0].text.strip().endswith(str(root.resolve()))


async def test_bash_relative_cwd_arguments_rebase_on_the_bound_cwd(tmp_path):
    root = tmp_path / "project"
    sub = root / "sub"
    sub.mkdir(parents=True)
    command = (
        f'{sys.executable} -c "'
        "import pathlib; print(pathlib.Path.cwd().resolve())"
        '"'
    )

    result = await _tool(base_tools(_ask(), cwd=root), "bash").execute(
        "c1", {"command": command, "cwd": "sub"}, None, None
    )

    assert result.content[0].text.strip().endswith(str(sub.resolve()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tools_scoped.py -q`

Expected: FAIL — `base_tools() got an unexpected keyword argument 'cwd'`.

- [ ] **Step 3: Add the binding helper and cwd-aware composition**

In `zenagent/tools/__init__.py`, add imports:

```python
from dataclasses import replace
from pathlib import Path
```

Add the helper before `base_tools`:

```python
def _bound(tool: AgentTool, cwd: Path, *, default_path: bool = False) -> AgentTool:
    """A copy of ``tool`` that resolves relative ``path`` arguments under ``cwd``."""

    async def execute(tool_call_id, args, abort, on_update=None):
        if "path" not in args and not default_path:
            return await tool.execute(tool_call_id, args, abort, on_update)
        raw = args.get("path")
        if raw is None or raw == "":
            target = cwd
        else:
            candidate = Path(str(raw)).expanduser()
            target = candidate if candidate.is_absolute() else cwd / candidate
        return await tool.execute(
            tool_call_id, {**args, "path": str(target)}, abort, on_update
        )

    return replace(tool, execute=execute)


def base_tools(
    ask_tool: AgentTool,
    *,
    shell_max_output_bytes: int | None = None,
    cwd: Path | None = None,
) -> list[AgentTool]:
    """The full prompt-ordered set, with the per-Runtime ask instance slotted
    in where ``ASK`` used to sit (between WAIT and GLOB).

    ``shell_max_output_bytes`` rebuilds ``bash`` with the configured budget
    (``[limits.tools].shell_max_output_bytes``); ``cwd`` additionally binds
    relative paths (and bash's default directory) to one working directory.
    ``None`` keeps the module default instances and process-cwd semantics.
    """
    if cwd is None:
        bash = (
            BASH
            if shell_max_output_bytes is None
            else build_bash_tool(shell_max_output_bytes)
        )
        read_file = READ_FILE
        write_file = WRITE_FILE
        edit_file = EDIT_FILE
        list_dir = LIST_DIR
        glob = GLOB
        grep = GREP
    else:
        bash = build_bash_tool(shell_max_output_bytes, cwd=cwd)
        read_file = _bound(READ_FILE, cwd)
        write_file = _bound(WRITE_FILE, cwd)
        edit_file = _bound(EDIT_FILE, cwd)
        list_dir = _bound(LIST_DIR, cwd)
        glob = _bound(GLOB, cwd, default_path=True)
        grep = _bound(GREP, cwd, default_path=True)
    return [
        read_file,
        write_file,
        edit_file,
        list_dir,
        bash,
        WAIT,
        ask_tool,
        glob,
        grep,
        WEB_SEARCH,
        FETCH,
    ]
```

- [ ] **Step 4: Give bash a bound default directory**

In `zenagent/tools/shell.py`:

```python
def resolve_cwd(raw: object, base: Path | None = None) -> Path | str:
    """Resolve the optional ``cwd`` argument; returns an error string if bad."""
    root = Path(os.getcwd()) if base is None else base
    if raw is None or raw == "":
        return root
    try:
        path = Path(str(raw)).expanduser()
    except (TypeError, ValueError):
        return f"invalid cwd: {raw!r}"
    if not path.is_absolute():
        path = root / path
    if not path.is_dir():
        return f"cwd does not exist or is not a directory: {path}"
    return path
```

In `zenagent/tools/bash.py`:

```python
async def _bash(
    tool_call_id: str,
    args: dict,
    abort: AbortToken | None,
    on_update=None,
    *,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
    base_cwd: Path | None = None,
) -> ToolResultAgent:
```

```python
    cwd = resolve_cwd(args.get("cwd"), base_cwd)
```

```python
def build_bash_tool(
    max_output_bytes: int | None = None, *, cwd: Path | None = None
) -> AgentTool:
    """The ``bash`` tool with its own output budget and default directory.

    The budget comes from ``[limits.tools].shell_max_output_bytes``, so the
    tool is built per Runtime; ``BASH`` stays the default instance every
    existing caller (and test) uses.
    """
    budget = MAX_OUTPUT_BYTES if max_output_bytes is None else max_output_bytes

    async def execute(
        tool_call_id: str, args: dict, abort: AbortToken | None, on_update=None
    ) -> ToolResultAgent:
        return await _bash(
            tool_call_id,
            args,
            abort,
            on_update,
            max_output_bytes=budget,
            base_cwd=cwd,
        )
```

Add `from pathlib import Path` to `zenagent/tools/bash.py` if it is not already imported.

- [ ] **Step 5: Bind the runtime's tool instances**

In `zenagent/app/runtime.py`, change the task tool assembly:

```python
        runtime.task_tool = build_task_tool(
            model=lambda: runtime.agent.model,
            base_tools=base_tools(runtime.ask_tool, cwd=runtime.cwd),
```

Change `_recompose`:

```python
        tools = base_tools(
            self.ask_tool,
            shell_max_output_bytes=self.limits.shell_max_output_bytes,
            cwd=self.cwd,
        )
```

- [ ] **Step 6: Run the tool suites**

Run: `uv run pytest tests/test_tools_scoped.py tests/test_tools_fs.py tests/test_tools_search.py tests/test_tools_bash.py tests/test_ask.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add zenagent/tools/__init__.py zenagent/tools/bash.py zenagent/tools/shell.py zenagent/app/runtime.py tests/test_tools_scoped.py
git commit -m "refactor(tools): bind filesystem and shell tools to a runtime cwd"
```

---

### Task 4: Status and command paths follow the runtime cwd

**Files:**
- Modify: `zenagent/app/chat.py:143`, `:163`
- Modify: `zenagent/app/commands.py:764`, `:792`
- Test: `tests/test_app_runtime.py`, `tests/test_app_commands.py`

**Interfaces:**
- Consumes: `Runtime.cwd` from Task 1.
- Produces: `RunStatus.cwd` and `/dirs` reflect the runtime working directory; `/handoff` writes there.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app_runtime.py`:

```python
async def test_status_reports_the_runtime_cwd(runtime_factory, tmp_path):
    from zenagent.app.chat import ChatController

    runtime = await runtime_factory(root=tmp_path)

    assert ChatController(runtime).status.cwd == str(tmp_path.resolve())
```

Append to `tests/test_app_commands.py`:

```python
async def test_dirs_lists_the_runtime_cwd(runtime_factory, tmp_path):
    from zenagent.app.chat import ChatController
    from zenagent.app.commands import AppServices, build_default_commands
    from zenagent.app.models import ModelManager
    from zenagent.app.sessions import SessionManager

    runtime = await runtime_factory(root=tmp_path)
    services = AppServices(
        runtime=runtime,
        chat=ChatController(runtime),
        models=ModelManager(runtime),
        sessions=SessionManager(runtime),
    )

    result = await build_default_commands(services).dispatch("dirs", "")

    assert str(tmp_path.resolve()) in result.message
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_app_runtime.py::test_status_reports_the_runtime_cwd tests/test_app_commands.py::test_dirs_lists_the_runtime_cwd -q`

Expected: FAIL — both strings show the process cwd instead of `tmp_path`.

- [ ] **Step 3: Replace the reads**

In `zenagent/app/chat.py`:

```python
        git = git_status(self.runtime.cwd)
```

```python
            cwd=str(self.runtime.cwd),
```

In `zenagent/app/commands.py`:

```python
    dirs = [str(services.runtime.cwd), *(str(path) for path in services.runtime.extra_dirs)]
```

```python
    document = services.runtime.cwd / f"handoff-{time.strftime('%Y%m%d-%H%M%S')}.md"
```

- [ ] **Step 4: Run the suites**

Run: `uv run pytest tests/test_app_runtime.py tests/test_app_commands.py tests/test_app_pending.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/chat.py zenagent/app/commands.py tests/test_app_runtime.py tests/test_app_commands.py
git commit -m "refactor(app): report status and command paths from runtime cwd"
```

---

### Task 5: RPC handlers use the runtime root and cwd

**Files:**
- Modify: `zenagent/cli/rpc/server.py:219-267`, `:599`
- Modify: `zenagent/cli/rpc/ops_files.py:47-49`, `:59`, `:71`
- Modify: `zenagent/cli/rpc/ops_config.py:238`, `:369`, `:432`, `:485`, `:620`, `:638`
- Modify: `zenagent/cli/rpc/cli.py:40-69`
- Test: `tests/test_project_rpc.py`

**Interfaces:**
- Consumes: `Runtime.root`, `Runtime.cwd` from Task 1.
- Produces: `RpcServer.root: Path`, `RpcServer.cwd: Path` (both fall back to `Path.cwd()` only in agent-only test mode).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_project_rpc.py`:

```python
async def test_file_ops_are_relative_to_the_runtime_cwd(runtime_factory, tmp_path):
    runtime = await runtime_factory(root=tmp_path)
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    writes: list[str] = []
    server = build_server(runtime, writes)

    assert server.cwd == tmp_path.resolve()
    assert server.root == tmp_path.resolve()

    result = await call(server, writes, {"type": "fs_read", "path": "note.txt"})

    assert result["ok"] is True
    assert result["file"]["text"] == "hello\n"


async def test_skill_discovery_uses_the_runtime_root(runtime_factory, tmp_path):
    skill = tmp_path / ".zenagent" / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: demo\ndescription: demo skill\n---\n\nbody\n",
        encoding="utf-8",
    )
    runtime = await runtime_factory(root=tmp_path)
    server = build_server(runtime, [])

    roots, skills, warnings = server._discover()

    assert roots[0] == tmp_path / ".zenagent" / "skills"
    assert [item.name for item in skills] == ["demo"]
    assert warnings == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_project_rpc.py::test_file_ops_are_relative_to_the_runtime_cwd tests/test_project_rpc.py::test_skill_discovery_uses_the_runtime_root -q`

Expected: FAIL — `RpcServer` has no `cwd`/`root` attributes.

- [ ] **Step 3: Add the server seams**

In `zenagent/cli/rpc/server.py`, add properties to `RpcServer` after the `agent` property:

```python
    @property
    def root(self) -> Path:
        """Project identity root for this server's runtime."""
        return self.runtime.root if self.runtime is not None else Path.cwd()

    @property
    def cwd(self) -> Path:
        """Working directory for relative file operations."""
        return self.runtime.cwd if self.runtime is not None else Path.cwd()
```

Replace `worktree_overview(Path.cwd())` with:

```python
                    "worktree": worktree_overview(self.cwd),
```

- [ ] **Step 4: Route the mixins**

`zenagent/cli/rpc/ops_files.py`:

```python
                payload = list_directory(self.cwd, path)
```

```python
                payload = {"file": read_file(self.cwd, path)}
```

```python
            payload = await asyncio.to_thread(workspace_files, self.cwd)
```

```python
            else MemoryStore(self.root)
```

`zenagent/cli/rpc/ops_config.py`:

```python
        roots = default_skill_roots(self.root)
```

```python
        global_layer, project_layer = load_mcp_layers(cwd=self.root)
```

```python
        global_layer, project_layer = load_mcp_layers(cwd=self.root)
```

```python
            "project_path": str(project_config_path(self.root)),
```

```python
                    cwd=self.root,
```

```python
                remove_mcp_server(name, layer=self._mcp_layer(request), cwd=self.root)
```

- [ ] **Step 5: Pass the root explicitly from the RPC entrypoint**

At the top of the `RuntimeOptions` construction in `zenagent/cli/rpc/cli.py`:

```python
    options = RuntimeOptions(
        root=Path.cwd(),
        model_id=args.model,
```

Keep the existing `os.chdir` shim in `main`; it remains for taiji/LSP/extensions.

- [ ] **Step 6: Run the RPC suites**

Run: `uv run pytest tests/test_project_rpc.py tests/test_rpc filesystem 2>/dev/null || uv run pytest tests/test_project_rpc.py tests/test_rpc_server.py tests/test_rpc_ops.py -q`

If the extra filenames do not exist, run only:

```bash
uv run pytest tests/test_project_rpc.py -q
```

and then the full RPC selection:

```bash
uv run pytest tests -k "rpc" -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add zenagent/cli/rpc/server.py zenagent/cli/rpc/ops_files.py zenagent/cli/rpc/ops_config.py zenagent/cli/rpc/cli.py tests/test_project_rpc.py
git commit -m "refactor(rpc): route file and config operations through the runtime root"
```

---

### Task 6: Prove two runtimes are isolated in one process

**Files:**
- Test: `tests/test_project_scoped_runtime.py` (new)

**Interfaces:**
- Consumes: `RuntimeOptions.root`, `Runtime.cwd`, `Runtime.root`, bound tools (Tasks 1-3), session helpers (Task 2).
- Produces: the Phase A acceptance gate.

- [ ] **Step 1: Write the failing test**

Create `tests/test_project_scoped_runtime.py`:

```python
"""Phase A acceptance: two projects, one process, no cwd races."""

import json

import pytest

from zenagent import project
from zenagent.app.runtime import Runtime, RuntimeOptions
from zenagent.config import UserConfig


def _repo(tmp_path, name, note):
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    (root / "note.txt").write_text(note, encoding="utf-8")
    return root


async def _bootstrap(monkeypatch, root):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(
        "zenagent.app.runtime.load_user_config",
        lambda *args, **kwargs: UserConfig(default_model="deepseek-flash"),
    )
    return await Runtime.bootstrap(
        RuntimeOptions(
            root=root,
            mcp=False,
            skills=False,
            templates=False,
            context_files=False,
        )
    )


def _tool(runtime, name):
    return next(tool for tool in runtime._base_tools if tool.name == name)


@pytest.fixture
async def two_runtimes(monkeypatch, tmp_path):
    alpha = _repo(tmp_path, "alpha", "alpha note\n")
    beta = _repo(tmp_path, "beta", "beta note\n")
    a = await _bootstrap(monkeypatch, alpha)
    b = await _bootstrap(monkeypatch, beta)
    try:
        yield alpha, beta, a, b
    finally:
        await a.close()
        await b.close()


async def test_cwd_and_project_identity_are_per_runtime(two_runtimes):
    alpha, beta, a, b = two_runtimes

    assert a.cwd == alpha.resolve()
    assert b.cwd == beta.resolve()
    assert a.root == alpha.resolve()
    assert b.root == beta.resolve()
    assert a.gate.policy.workspace == alpha.resolve()
    assert b.gate.policy.workspace == beta.resolve()


async def test_sessions_do_not_cross_project_directories(two_runtimes):
    alpha, beta, a, b = two_runtimes

    assert a.session.path.parent == project.project_sessions_dir(root=alpha)
    assert b.session.path.parent == project.project_sessions_dir(root=beta)
    assert a.session.path != b.session.path

    a.session.materialize()
    assert b.session.path.exists() is False

    header = json.loads(a.session.path.read_text(encoding="utf-8").splitlines()[0])
    assert header["cwd"] == str(alpha.resolve())


async def test_memory_and_prompt_follow_the_runtime_root(two_runtimes):
    alpha, beta, a, b = two_runtimes

    assert a.memory_store.root == alpha.resolve()
    assert b.memory_store.root == beta.resolve()
    assert f"Working directory: {alpha.resolve()}" in a.agent.system_prompt
    assert f"Working directory: {beta.resolve()}" in b.agent.system_prompt


async def test_bound_tools_read_from_their_own_project(two_runtimes):
    alpha, beta, a, b = two_runtimes

    alpha_read = await _tool(a, "read_file").execute(
        "c1", {"path": "note.txt"}, None, None
    )
    beta_read = await _tool(b, "read_file").execute(
        "c1", {"path": "note.txt"}, None, None
    )

    assert alpha_read.content[0].text == "alpha note\n"
    assert beta_read.content[0].text == "beta note\n"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_project_scoped_runtime.py -q`

Expected: FAIL while Tasks 1-3 are incomplete. If run after them, it must fail only if a path is still process-scoped.

- [ ] **Step 3: Fix any remaining process-scoped read the test exposes**

The allowed fixes are only the ones already specified in Tasks 1-3. Do not weaken the assertions; find the read that still uses `Path.cwd()` and route it through `runtime.cwd`/`runtime.root`.

- [ ] **Step 4: Run the test until green**

Run: `uv run pytest tests/test_project_scoped_runtime.py -q`

Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add tests/test_project_scoped_runtime.py
git commit -m "test(runtime): prove two project runtimes are isolated in one process"
```

---

### Task 7: Full verification and architecture budget

**Files:**
- Modify (only if the gate demands it): the LOC allowlist read by `scripts/check_architecture.py`
- No production changes otherwise.

- [ ] **Step 1: Run the full suite**

```bash
uv run pytest -q
```

Expected: PASS; the baseline before this plan was `2578 passed, 7 warnings`.

- [ ] **Step 2: Run lint and the architecture budget**

```bash
uv run ruff check .
python3 scripts/check_architecture.py
```

Expected: both clean. If the architecture gate reports that a file exceeds its LOC budget, follow the repo convention: register the new line count in the gate's allowlist (do not restructure unrelated code), then re-run.

- [ ] **Step 3: Verify no real-home pollution**

```bash
git status --short
ls ~/.zenagent/projects 2>/dev/null | wc -l
```

The first command must show only files this plan touched. The second number must not have grown because of the test run; the autouse fixtures isolate `zenagent_home`.

- [ ] **Step 4: Commit the gate update (only when Step 2 required one)**

```bash
git add scripts/check_architecture.py
git commit -m "chore(arch): re-register the runtime-aware LOC budgets"
```

---

## Self-Review

**Spec coverage (audit Phase A items):**

| Audit item | Task |
|---|---|
| 1. Root on Runtime, replace 11 reads | Task 1 |
| 2. Thread root into session helpers and header | Task 2 |
| 3. `load_mcp_config` / `build_broker_for` / prompt cwd | Task 1 |
| 4. Per-runtime tools (`bash`, fs, glob, grep, list_dir) | Task 3 |
| 5. `_recursive_rm_reason` | Deliberately deferred — see Scope Decision 4; keeping the process cwd preserves sub-directory safety. |
| 6. Commands and chat status | Task 4 |
| 7. RPC fs/memory/worktrees/config ops | Task 5 |
| 8. `os.chdir` only as a shim | Kept in `zenagent/cli/rpc/cli.py`; Task 5 passes `root=` explicitly. |
| Acceptance: two runtimes isolated in one process | Task 6 |

**Placeholder scan:** every code step contains the literal edit or test; no TBD/TODO markers.

**Type consistency:** `RuntimeOptions.root: Path | str | None` is normalized to `Runtime.cwd: Path` and `Runtime.root: Path` once in bootstrap; every later consumer uses the `Path` fields. `base_tools(..., cwd: Path | None)`, `build_bash_tool(..., cwd: Path | None)`, `resolve_cwd(raw, base: Path | None)`, `Session.cwd: Path | str | None`, `records.open_session(..., cwd: Path | str | None)`, and `prepare_retry(..., root: Path | None)` agree with their call sites.
