# Project-Scoped Runtime Phase B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `zen rpc` host process serves several projects: per-root taiji supervisors, per-project runtimes in an LRU slot registry, project-tagged frames, and `project open|switch|close|relocate` ops — with no frontend changes and no wire version bump.

**Architecture:** `RpcHost` owns the stdio loop and an `OrderedDict` of `RuntimeSlot`s keyed by `project_key(root)`. Each slot wraps one `RpcServer` (one runtime, chat, in-flight task, approvals/asks). Requests may name a `project` root; missing means the active slot, so v2 clients keep working. Every frame a slot emits carries its `project`, and approval/ask ids are prefixed with the slot key. Taiji becomes a registry keyed by project root; a slot claims/releases its project's host lock with its lifecycle. Slots are evicted LRU beyond 4, never while busy or while a taiji worker is running/queued.

**Tech Stack:** Python 3.12+, asyncio, existing `zenagent` app layers (Runtime, ChatController, RpcServer mixins, TaijiSupervisor), pytest + pytest-asyncio, ruff.

**Spec:** `docs/research/project-scoped-runtime-audit.md` (Phase B section)

## Global Constraints

- Work only in `/Users/mac/zen-agent/.worktrees/project-scoped-runtime` on branch `refactor/project-scoped-runtime`. Never touch the main worktree's uncommitted docs.
- Phase B is Python-only: no desktop (`desktop/`), Rust, or UI copy changes.
- `PROTOCOL_VERSION` stays `2`; wire changes are additive (`project` request field, `project` frame field, `project` ops).
- No new Python, npm, or cargo dependencies.
- Tests must never read/write the real `~/.zenagent`; the autouse fixtures in `tests/conftest.py` already isolate `project.zenagent_home` and settings. Host/slot tests must pass explicit `root=` values and must not depend on `monkeypatch.chdir`.
- A resident slot is created with `dataclasses.replace(options, root=<project>, resume=<request resume or None>)`; a project switch never reuses another project's `--resume`.
- Busy is per slot. Eviction and close refuse with code `project_busy`; a request that names no resident project gets code `no_project`.
- Implementers and reviewers do not spawn subagents.
- Per-task commits are authorized by this plan; commit each task separately with the message given in the task.
- Final gate for every task that touches shipped code: `uv run ruff check .`, `python3 scripts/check_architecture.py`, `uv run pytest -q`.

---

## Wire Contract (reference for Tasks 3-5)

Request fields (all optional, additive):

```json
{"type": "prompt", "id": "42", "project": "/abs/project/root", "text": "..."}
{"type": "approval", "id": "43", "request_id": "<key>:a0", "decision": "allow"}
{"type": "project", "id": "44", "op": "open|switch|close|relocate", "root": "...", "from": "...", "to": "..."}
```

Frame fields:

```json
{"type": "event", "project": "/abs/project/root", "event": {"type": "approval_request", "request_id": "<key>:a0"}}
{"type": "result", "project": "/abs/project/root", "id": "42", "ok": true}
```

`project open|switch` result:

```json
{"type": "result", "id": "44", "ok": true, "root": "/abs/root", "key": "<project_key>",
 "session": "/abs/sessions/x.jsonl", "active": "/abs/root",
 "resident": [{"key": "...", "root": "...", "session": "..."}]}
```

Errors use the existing sanitized frames: `{"type": "error", "id": ..., "message": ..., "code": "project_busy" | "no_project" | "project_invalid" | "project_failed"}`.

---

### Task 1: Per-root taiji supervisors and the host claim helper

**Files:**
- Modify: `zenagent/app/taiji/supervisor.py:538-563`
- Modify: `zenagent/app/taiji/session.py:27-46`
- Modify: `zenagent/app/taiji/tools.py:217-238`
- Modify: `zenagent/app/taiji/__init__.py`
- Modify: `zenagent/cli/rpc/ops_taiji.py:26-28`
- Create: `zenagent/app/taiji/claim.py`
- Test: `tests/test_taiji_supervisor.py` (registry tests), `tests/test_taiji_rpc.py` (fixture), `tests/test_rpc_v2.py` (lambda arity), new `tests/test_taiji_claim.py`

**Interfaces:**
- Consumes: `project.find_project_root`, `project.project_sessions_dir`, `project.project_dir`, `should_init_taiji`, `own_taiji_lock`, `release_taiji_lock`, `taiji_supervisor_for`.
- Produces:
  - `get_taiji(*, repo_root: Path | str, **kwargs) -> TaijiSupervisor`
  - `existing_taiji(root: Path | str | None = None) -> TaijiSupervisor | None`
  - `async close_taiji(root: Path | str | None = None) -> None`
  - `TaijiClaim(root: Path, owned: bool)`
  - `claim_taiji_host(runtime) -> TaijiClaim`
  - `async release_taiji_host(claim: TaijiClaim) -> None`
  - `ensure_taiji_session(sessions_dir, *, cwd: Path | None = None) -> Path`

- [ ] **Step 1: Write the failing tests**

In `tests/test_taiji_supervisor.py`, replace `test_get_taiji_is_a_per_loop_singleton` (line 192) with the registry tests:

```python
async def test_get_taiji_keeps_one_supervisor_per_root(
    repo: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    runner = StubRunner()

    def make(root: Path):
        return get_taiji(
            repo_root=root,
            sessions_dir=tmp_path / "s",
            registry_path=tmp_path / "s" / "taiji.json",
            runner=runner,
            chat=None,
            mode_of=lambda: "ask",
        )

    one = make(repo)
    again = make(repo)
    two = make(other)
    try:
        assert one is again
        assert two is not one
        assert existing_taiji(repo) is one
        assert existing_taiji(other) is two
        await close_taiji(repo)
        assert existing_taiji(repo) is None
        assert existing_taiji(other) is two
    finally:
        await close_taiji()
    assert existing_taiji() is None


async def test_existing_taiji_without_a_root_only_answers_when_unambiguous(
    repo: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()

    def make(root: Path):
        return get_taiji(
            repo_root=root,
            sessions_dir=tmp_path / "s",
            registry_path=tmp_path / "s" / "taiji.json",
            runner=StubRunner(),
            chat=None,
            mode_of=lambda: "ask",
        )

    one = make(repo)
    try:
        assert existing_taiji() is one
        make(other)
        assert existing_taiji() is None
    finally:
        await close_taiji()
```

Update the import block at the top of that file to include `existing_taiji`.

Update `test_existing_taiji_returns_none_before_any_construction` (line ~1058) so it clears the registry:

```python
async def test_existing_taiji_returns_none_before_any_construction() -> None:
    from zenagent.app.taiji import supervisor as supervisor_module

    supervisor_module._SUPERVISORS.clear()
    try:
        assert supervisor_module.existing_taiji() is None
    finally:
        await supervisor_module.close_taiji()
```

In `tests/test_taiji_rpc.py`, change the fixture (line 82) and autouse cleaner (lines 86-92):

```python
    supervisor_module._SUPERVISORS[repo.resolve()] = supervisor
```

```python
@pytest.fixture(autouse=True)
def clean_singleton():
    from zenagent.app.taiji import supervisor as supervisor_module

    supervisor_module._SUPERVISORS.clear()
    yield
    supervisor_module._SUPERVISORS.clear()
```

In `tests/test_rpc_v2.py` (line 1731), keep the fake compatible with the rooted lookup:

```python
    monkeypatch.setattr(
        "zenagent.app.taiji.supervisor.existing_taiji",
        lambda root=None: FakeSupervisor(),
    )
```

Create `tests/test_taiji_claim.py`:

```python
"""The per-project taiji host claim: session, lock, fleet — and release."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from zenagent import project
from zenagent.app.taiji import (
    TaijiClaim,
    claim_taiji_host,
    ensure_taiji_session,
    release_taiji_host,
    taiji_session_path,
)
from zenagent.app.taiji.supervisor import existing_taiji, _git


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "taiji@example.com")
    _git(repo, "config", "user.name", "taiji")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    return repo


class FakeRuntime:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.cwd = self.root
        self.mode = "ask"
        self.taiji_chat = None
        self.notes: list[str] = []

    def _note(self, text: str) -> None:
        self.notes.append(text)


def test_ensure_taiji_session_records_the_project_cwd(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    path = ensure_taiji_session(sessions, cwd=tmp_path / "proj")
    header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert header["cwd"] == str((tmp_path / "proj").resolve())


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_claim_owns_the_host_and_release_clears_it(repo: Path) -> None:
    claim = claim_taiji_host(FakeRuntime(repo))
    try:
        assert claim == TaijiClaim(root=repo.resolve(), owned=True)
        assert existing_taiji(repo) is not None
        session = taiji_session_path(project.project_sessions_dir(root=repo))
        header = json.loads(session.read_text(encoding="utf-8").splitlines()[0])
        assert header["cwd"] == str(repo.resolve())
        assert (project.project_dir(root=repo) / "taiji.lock").exists()
    finally:
        await release_taiji_host(claim)
    assert existing_taiji(repo) is None
    assert not (project.project_dir(root=repo) / "taiji.lock").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_a_second_claim_on_the_same_project_is_not_owned(repo: Path) -> None:
    runtime = FakeRuntime(repo)
    first = claim_taiji_host(runtime)
    try:
        second = claim_taiji_host(runtime)
        assert second.owned is False
        assert runtime.notes  # the host told the person who owns the lock
    finally:
        await release_taiji_host(first)


async def test_claim_is_inert_outside_a_main_checkout(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "zenagent.app.taiji.claim.should_init_taiji", lambda root: False
    )
    runtime = FakeRuntime(repo)
    claim = claim_taiji_host(runtime)
    assert claim == TaijiClaim(root=repo.resolve(), owned=False)
    assert not taiji_session_path(
        project.project_sessions_dir(root=repo)
    ).exists()
    await release_taiji_host(claim)
```

- [ ] **Step 2: Run the new tests and watch them fail**

Run: `uv run pytest tests/test_taiji_supervisor.py::test_get_taiji_keeps_one_supervisor_per_root tests/test_taiji_claim.py -q`
Expected: FAIL — `_SUPERVISORS` / `claim` module / `cwd` parameter missing.

- [ ] **Step 3: Implement the registry in `supervisor.py`**

Replace the tail of `zenagent/app/taiji/supervisor.py` (from `_SINGLETON` to EOF):

```python
#: One supervisor per project root: a host with several resident projects
#: runs one fleet per root, never a process-wide fleet.
_SUPERVISORS: dict[Path, TaijiSupervisor] = {}


def _root_key(root: Path | str) -> Path:
    return Path(root).expanduser().resolve()


def get_taiji(*, repo_root: Path | str, **kwargs: Any) -> TaijiSupervisor:
    """The supervisor for ``repo_root``; the first construction wins."""
    key = _root_key(repo_root)
    supervisor = _SUPERVISORS.get(key)
    if supervisor is None:
        supervisor = TaijiSupervisor(repo_root=key, **kwargs)
        _SUPERVISORS[key] = supervisor
    return supervisor


def existing_taiji(root: Path | str | None = None) -> TaijiSupervisor | None:
    """The live supervisor for ``root``; ``None`` when this host never started it.

    Without a root, answer only when the registry is unambiguous (one live
    project): the taiji panel and worker client must never guess between
    fleets, and a worker subprocess must read ``active: false``.
    """
    if root is not None:
        return _SUPERVISORS.get(_root_key(root))
    if len(_SUPERVISORS) == 1:
        return next(iter(_SUPERVISORS.values()))
    return None


async def close_taiji(root: Path | str | None = None) -> None:
    """Close one project's fleet, or every fleet when no root is given."""
    if root is not None:
        supervisor = _SUPERVISORS.pop(_root_key(root), None)
        if supervisor is not None:
            await supervisor.close()
        return
    supervisors = list(_SUPERVISORS.values())
    _SUPERVISORS.clear()
    for supervisor in supervisors:
        await supervisor.close()
```

- [ ] **Step 4: Add the session cwd override**

In `zenagent/app/taiji/session.py`, change the signature and header:

```python
def ensure_taiji_session(
    sessions_dir: Path, *, cwd: Path | None = None
) -> Path:
    """The taiji session file, created with its header and title on first use."""
    path = taiji_session_path(sessions_dir)
    if not path.exists() or path.stat().st_size == 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "type": HEADER_TYPE,
            "version": RECORD_VERSION,
            "id": path.stem,
            "createdAt": int(time.time()),
            "cwd": str(Path(cwd).resolve()) if cwd is not None else os.getcwd(),
            "app": "zenagent",
            "appVersion": __version__,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    set_session_title(path, TAIJI_SESSION_TITLE)
    return path
```

- [ ] **Step 5: Point the supervisor seam at the runtime root**

Replace `taiji_supervisor_for` in `zenagent/app/taiji/tools.py`:

```python
def taiji_supervisor_for(runtime: Any) -> TaijiSupervisor:
    """This host's supervisor for ``runtime``'s project, mode and chat.

    The public seam for both the Router's tool provider and the RPC panel
    ops; construction is per project root, so a multi-project host keeps one
    fleet per root.
    """
    import os

    from zenagent import project
    from zenagent.app.taiji.supervisor import get_taiji

    root = project.find_project_root(runtime.root)
    worker_model = os.environ.get("ZENAGENT_WORKER_MODEL") or None
    return get_taiji(
        repo_root=root,
        sessions_dir=project.project_sessions_dir(root=root),
        registry_path=project.project_dir(root=root) / "taiji.json",
        runner=RpcRunner(worker_model=worker_model),
        chat=getattr(runtime, "taiji_chat", None),
        mode_of=lambda: runtime.mode,
    )
```

- [ ] **Step 6: Add the claim helper**

Create `zenagent/app/taiji/claim.py`:

```python
"""One project's taiji host claim: session, lock and fleet, in that order.

Claiming is what makes a slot the project's Router host. Releasing is the
slot's teardown path: close this root's fleet, then drop its lock. A slot
that did not own the lock releases nothing, so a live foreign host survives.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zenagent import project
from zenagent.app.taiji.session import (
    ensure_taiji_session,
    own_taiji_lock,
    release_taiji_lock,
    should_init_taiji,
)


@dataclass(frozen=True)
class TaijiClaim:
    """What one slot claimed: the project root, and whether it owns the host."""

    root: Path
    owned: bool


def claim_taiji_host(runtime: Any) -> TaijiClaim:
    """Materialize this project's taiji session, lock and fleet (best effort).

    Only the main checkout hosts taiji. A foreign live host keeps the lock:
    this runtime reports ``owned=False`` and still runs as an ordinary
    project, exactly like the single-project CLI did.
    """
    root = project.find_project_root(runtime.root)
    if not should_init_taiji(root):
        return TaijiClaim(root=root, owned=False)
    ensure_taiji_session(project.project_sessions_dir(root=root), cwd=root)
    if not own_taiji_lock(project.project_dir(root=root)):
        runtime._note("taiji: 另一个宿主已持有本项目的太极锁")
        return TaijiClaim(root=root, owned=False)
    from zenagent.app.taiji.tools import taiji_supervisor_for

    taiji_supervisor_for(runtime)
    return TaijiClaim(root=root, owned=True)


async def release_taiji_host(claim: TaijiClaim) -> None:
    """Close the fleet this claim started and drop its lock."""
    if not claim.owned:
        return
    from zenagent.app.taiji.supervisor import close_taiji

    await close_taiji(claim.root)
    release_taiji_lock(project.project_dir(root=claim.root))
```

- [ ] **Step 7: Export the new surface**

Replace `zenagent/app/taiji/__init__.py`:

```python
"""Taiji: the orchestration layer over the Runtime — Router, workers, panel snapshots."""

from zenagent.app.taiji.claim import (
    TaijiClaim,
    claim_taiji_host,
    release_taiji_host,
)
from zenagent.app.taiji.session import (
    TAIJI_SESSION_STEM,
    TaijiHostLocked,
    acquire_taiji_lock,
    ensure_taiji_session,
    is_taiji_session,
    own_taiji_lock,
    release_taiji_lock,
    should_init_taiji,
    taiji_session_path,
)
from zenagent.app.taiji.supervisor import close_taiji, existing_taiji, get_taiji

__all__ = [
    "TAIJI_SESSION_STEM",
    "TaijiClaim",
    "TaijiHostLocked",
    "acquire_taiji_lock",
    "claim_taiji_host",
    "close_taiji",
    "ensure_taiji_session",
    "existing_taiji",
    "get_taiji",
    "is_taiji_session",
    "own_taiji_lock",
    "release_taiji_host",
    "release_taiji_lock",
    "should_init_taiji",
    "taiji_session_path",
]
```

- [ ] **Step 8: Route the taiji panel by the server's project**

In `zenagent/cli/rpc/ops_taiji.py`, change the lookup (line 28):

```python
        supervisor = existing_taiji(self.root)
```

- [ ] **Step 9: Run the affected tests**

Run:

```bash
uv run pytest tests/test_taiji_supervisor.py tests/test_taiji_claim.py tests/test_taiji_rpc.py tests/test_taiji_tools.py tests/test_taiji_loop.py tests/test_rpc_v2.py -q
```

Expected: PASS. If `test_taiji_rpc.py` still mutates `_SINGLETON`, the fixture edit was missed.

- [ ] **Step 10: Commit**

```bash
git add zenagent/app/taiji zenagent/cli/rpc/ops_taiji.py tests/test_taiji_supervisor.py tests/test_taiji_rpc.py tests/test_rpc_v2.py tests/test_taiji_claim.py
git commit -m "feat(taiji): keep one supervisor and host lock per project root"
```

---

### Task 2: Scope background job views to their workspace

**Files:**
- Modify: `zenagent/tools/jobs.py:87-108` (Job fields), `zenagent/tools/jobs.py:163-168` (`JobManager.start`)
- Modify: `zenagent/app/chat.py:189-195` (`_job_views`)
- Test: `tests/test_app_chat.py`

**Interfaces:**
- Consumes: `JobManager.list()`, `ChatController.runtime.root` (Phase A).
- Produces: `Job.cwd: str` (spawn directory, `""` when unknown), `ChatController._job_views()` filtered to jobs whose cwd is inside `runtime.root`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app_chat.py`:

```python
async def test_job_views_stay_inside_their_workspace(runtime_factory, tmp_path):
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    (alpha / "sub").mkdir(parents=True)
    beta.mkdir()
    a = await runtime_factory(root=alpha)
    b = await runtime_factory(root=beta)
    from zenagent.app.chat import ChatController
    from zenagent.tools.jobs import get_job_manager

    manager = get_job_manager()
    one = await manager.start("sleep 30", cwd=alpha)
    two = await manager.start("sleep 30", cwd=alpha / "sub")
    try:
        assert [view.id for view in ChatController(a)._job_views()] == [
            one.id,
            two.id,
        ]
        assert ChatController(b)._job_views() == []
    finally:
        await manager.kill(one.id)
        await manager.kill(two.id)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_app_chat.py::test_job_views_stay_inside_their_workspace -q`
Expected: FAIL — neither `Job` nor `JobManager.start` records a cwd; both chats list both jobs.

- [ ] **Step 3: Record the spawn directory on the job**

In `zenagent/tools/jobs.py`, add the field after `error: str = ""`:

```python
    #: Spawn directory; scopes the sidebar's job view to one project.
    cwd: str = ""
```

In `JobManager.start`, pass it when constructing the job:

```python
        job = Job(
            id=f"bg_{next(self._counter)}",
            command=command,
            timeout_s=timeout_s,
            cwd=str(target),
        )
```

- [ ] **Step 4: Filter the chat's job views**

Replace `_job_views` in `zenagent/app/chat.py`:

```python
    def _job_views(self) -> list[JobView]:
        """Background jobs started inside this runtime's workspace."""
        try:
            manager = get_job_manager()
        except RuntimeError:  # no running loop (headless, sync tests)
            return []
        workspace = Path(self.runtime.root).resolve()
        visible = [
            job
            for job in manager.list()
            if not getattr(job, "cwd", "")
            or Path(job.cwd).resolve().is_relative_to(workspace)
        ]
        return sort_jobs(job_views(visible))
```

- [ ] **Step 5: Run the job suites**

Run:

```bash
uv run pytest tests/test_app_chat.py tests/test_jobs.py tests/test_bash_tool.py -q
```

Expected: PASS. If `tests/test_app_chat.py` has no `runtime_factory` fixture yet, it is defined in `tests/conftest.py` and available automatically.

- [ ] **Step 6: Commit**

```bash
git add zenagent/tools/jobs.py zenagent/app/chat.py tests/test_app_chat.py
git commit -m "fix(jobs): scope background job views to the runtime workspace"
```

---

### Task 3: Project-tagged frames, scoped approval ids, and `RuntimeSlot`

**Files:**
- Modify: `zenagent/cli/rpc/server.py:219-260` (constructor), `:325-331` (`_send`), `:333-360` (`confirm`), `:405-432` (`ask`)
- Create: `zenagent/cli/rpc/slot.py`
- Test: new `tests/test_rpc_slot.py`
- Modify: `docs/architecture/allowlist.json` (register `zenagent/cli/rpc/server.py`'s measured size)

**Interfaces:**
- Consumes: `RpcServer`, `PreparedFrontend`, `project.project_key`, `TaijiClaim`, `claim_taiji_host`, `release_taiji_host`.
- Produces:
  - `RpcServer(..., project: str | None = None)`, attribute `server.project: str` (resolved root or `""`), `server.project_key: str`.
  - Every outgoing server frame carries `"project": <root>` when `project` is set.
  - `RuntimeSlot(root, key, server, claim, opened_at)` with `runtime`, `chat`, `busy`, `pinned`, `close()`, `adopt(prepared, *, write_line, approval_timeout_s=120.0)`.

- [ ] **Step 1: Write the failing slot/server tests**

Create `tests/test_rpc_slot.py`:

```python
"""One resident project: wire tags, scoped ids, and lifecycle wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path

from zenagent.app.bootstrap import PreparedFrontend
from zenagent.app.taiji import TaijiClaim
from zenagent.cli.rpc.slot import RuntimeSlot
from zenagent.project import project_key


class FakeAgent:
    def __init__(self) -> None:
        self.messages: list = []
        self.is_streaming = False
        self.tools: list = []
        self.termination = "stop"

    def subscribe(self, listener) -> None:
        self._listener = listener

    def abort(self) -> None:
        pass


class FakeChat:
    busy = False

    def reload_transcript(self) -> None:
        pass

    async def wait_for_idle(self) -> None:
        pass


class FakeRuntime:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.cwd = self.root
        self.agent = FakeAgent()
        self.ask_handler = None
        self.taiji_chat = None
        self.closed = False

    def _note(self, text: str) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class FakeBroker:
    def __init__(self) -> None:
        self.confirm = None

    def set_confirm(self, fn) -> None:
        self.confirm = fn


class FakeServices:
    models = None
    sessions = None


def _prepared(root: Path) -> tuple[PreparedFrontend, FakeRuntime, FakeBroker]:
    runtime = FakeRuntime(root)
    broker = FakeBroker()
    return (
        PreparedFrontend(
            runtime=runtime,
            broker=broker,
            chat=FakeChat(),
            services=FakeServices(),
        ),
        runtime,
        broker,
    )


def _adopt(root: Path, writes: list[dict], monkeypatch) -> RuntimeSlot:
    prepared, runtime, broker = _prepared(root)
    claim = TaijiClaim(root=root.resolve(), owned=False)
    monkeypatch.setattr(
        "zenagent.cli.rpc.slot.claim_taiji_host", lambda _runtime: claim
    )
    slot = RuntimeSlot.adopt(prepared, write_line=writes.append)
    assert broker.confirm == slot.server.confirm
    assert runtime.ask_handler == slot.server.ask
    return slot


async def test_slot_tags_frames_and_scopes_approval_ids(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    writes: list[dict] = []
    slot = _adopt(root, writes, monkeypatch)

    pending = asyncio.create_task(slot.server.confirm("bash", {"command": "ls"}))
    await asyncio.sleep(0)
    frame = writes[0]
    assert frame["project"] == str(root.resolve())
    assert frame["event"]["type"] == "approval_request"
    assert frame["event"]["request_id"] == f"{slot.key}:a0"

    slot.server._resolve_approval(
        {"request_id": f"{slot.key}:a0", "decision": "deny", "id": "r1"}
    )
    assert await pending == "deny"
    assert writes[-1]["project"] == str(root.resolve())
    assert writes[-1]["ok"] is True


async def test_slot_scopes_ask_ids_too(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    writes: list[dict] = []
    slot = _adopt(root, writes, monkeypatch)
    questions = [
        {"id": "q1", "question": "Continue?", "options": [{"label": "Yes"}]}
    ]

    pending = asyncio.create_task(slot.server.ask(questions))
    await asyncio.sleep(0)
    request_id = writes[0]["event"]["request_id"]
    assert request_id == f"{slot.key}:q0"

    slot.server._resolve_ask(
        {"request_id": request_id, "answers": [["Yes"]], "id": "r2"}
    )
    assert await pending == [{"id": "q1", "selected": ["Yes"]}]


async def test_slot_id_uses_the_project_key(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    slot = _adopt(root, [], monkeypatch)
    assert slot.key == project_key(root.resolve())
    assert slot.server.project == str(root.resolve())


async def test_slot_close_releases_the_claim_and_the_runtime(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    released: list[TaijiClaim] = []

    async def fake_release(claim: TaijiClaim) -> None:
        released.append(claim)

    prepared, runtime, _broker = _prepared(root)
    claim = TaijiClaim(root=root.resolve(), owned=True)
    monkeypatch.setattr(
        "zenagent.cli.rpc.slot.claim_taiji_host", lambda _runtime: claim
    )
    monkeypatch.setattr(
        "zenagent.cli.rpc.slot.release_taiji_host", fake_release
    )
    slot = RuntimeSlot.adopt(prepared, write_line=[].append)

    await slot.close()

    assert released == [claim]
    assert runtime.ask_handler is None
    assert runtime.closed is True
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_rpc_slot.py -q`
Expected: FAIL — `zenagent.cli.rpc.slot` does not exist; `RpcServer` has no `project` parameter.

- [ ] **Step 3: Tag and scope the server**

In `zenagent/cli/rpc/server.py`, add the import at the top:

```python
from zenagent.project import project_key
```

Change the constructor signature (after `approval_timeout_s`) to add:

```python
        project: str | None = None,
```

and set the attributes beside the other assignments:

```python
        self.project = str(Path(project).resolve()) if project else ""
        self.project_key = project_key(self.project) if self.project else ""
```

Replace `_send`:

```python
    def _send(self, payload: dict) -> None:
        if self._write_line is None:
            return
        if self.project:
            payload = {**payload, "project": self.project}
        self._write_line(json.dumps(payload, ensure_ascii=False, default=str))
```

In `confirm`, replace the id construction:

```python
        prefix = f"{self.project_key}:" if self.project_key else ""
        request_id = f"{prefix}a{self._approval_seq}"
```

In `ask`, do the same:

```python
        prefix = f"{self.project_key}:" if self.project_key else ""
        request_id = f"{prefix}q{self._ask_seq}"
```

No other change: `_resolve_approval` / `_resolve_ask` already match ids verbatim.

- [ ] **Step 4: Add `RuntimeSlot`**

Create `zenagent/cli/rpc/slot.py`:

```python
"""One resident project: its runtime, RPC server and taiji claim.

A slot is the unit the host opens and evicts. Everything that used to be
process-scoped in ``zen rpc`` — the in-flight run, text buffer, approval and
ask futures, taiji host lock — belongs to a slot now.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from zenagent.app.taiji import (
    TaijiClaim,
    claim_taiji_host,
    release_taiji_host,
)
from zenagent.cli.rpc.server import RpcServer
from zenagent.project import project_key

if TYPE_CHECKING:
    from zenagent.app.bootstrap import PreparedFrontend
    from zenagent.app.runtime import Runtime

#: Matches ``RpcServer``'s default approval timeout.
DEFAULT_APPROVAL_TIMEOUT_S = 120.0


class RuntimeSlot:
    """A resident project and everything its server owns."""

    def __init__(
        self,
        *,
        root: Path,
        server: RpcServer,
        claim: TaijiClaim,
        opened_at: float | None = None,
    ) -> None:
        self.root = Path(root)
        self.key = project_key(self.root)
        self.server = server
        self.claim = claim
        self.opened_at = time.monotonic() if opened_at is None else opened_at

    @classmethod
    def adopt(
        cls,
        prepared: "PreparedFrontend",
        *,
        write_line,
        approval_timeout_s: float = DEFAULT_APPROVAL_TIMEOUT_S,
    ) -> "RuntimeSlot":
        """Wire one prepared frontend into a slot and claim its taiji host."""
        runtime = prepared.runtime
        chat = prepared.chat
        services = prepared.services
        server = RpcServer(
            runtime=runtime,
            read_line=None,
            write_line=write_line,
            chat=chat,
            models=services.models if services is not None else None,
            sessions=services.sessions if services is not None else None,
            commands=prepared.commands,
            approval_timeout_s=approval_timeout_s,
            project=str(runtime.root),
        )
        prepared.broker.set_confirm(server.confirm)
        runtime.ask_handler = server.ask
        runtime.taiji_chat = chat
        claim = claim_taiji_host(runtime)
        return cls(root=Path(runtime.root).resolve(), server=server, claim=claim)

    @property
    def runtime(self) -> "Runtime":
        return self.server.runtime

    @property
    def chat(self):
        return self.server.chat

    @property
    def busy(self) -> bool:
        """A run, a queued follow-up or a compaction owns this slot."""
        return self.server.busy or bool(self.chat is not None and self.chat.busy)

    @property
    def pinned(self) -> bool:
        """True while the slot must not be evicted."""
        if self.busy:
            return True
        from zenagent.app.taiji.supervisor import existing_taiji

        supervisor = existing_taiji(self.root)
        if supervisor is None:
            return False
        return any(
            record.status in ("running", "queued")
            for record in supervisor.registry.records.values()
        )

    async def close(self) -> None:
        """Deny pending prompts, release the taiji claim and close the runtime."""
        await self.server._shutdown()
        await release_taiji_host(self.claim)
        self.runtime.ask_handler = None
        await self.runtime.close()
```

- [ ] **Step 5: Run the slot tests**

Run: `uv run pytest tests/test_rpc_slot.py tests/test_rpc.py tests/test_rpc_v2.py -q`
Expected: PASS.

- [ ] **Step 6: Register the measured server size**

```bash
wc -l < zenagent/cli/rpc/server.py
```

Set `zenagent/cli/rpc/server.py` in `docs/architecture/allowlist.json` to that exact integer (repo convention: register the measured size so the gate stays green), then run `python3 scripts/check_architecture.py`. Expected: `architecture budget ok`.

- [ ] **Step 7: Commit**

```bash
git add zenagent/cli/rpc/server.py zenagent/cli/rpc/slot.py tests/test_rpc_slot.py docs/architecture/allowlist.json
git commit -m "feat(rpc): tag frames and approval ids with their project"
```

---

### Task 4: `RpcHost` and CLI wiring

**Files:**
- Create: `zenagent/cli/rpc/host.py`
- Modify: `zenagent/cli/rpc/cli.py:38-118`
- Test: new `tests/test_rpc_host.py`

**Interfaces:**
- Consumes: `RuntimeSlot`, `prepare`, `RuntimeOptions`, `find_project_root`, `project_key`, `RpcServer.PROTOCOL_VERSION`, `Refusal`, `error_frame`, `failure_frame`, `relocate_project` (via the active slot's `ConfigOps._project`).
- Produces: `RpcHost(initial, options, read_line, write_line, *, assume_yes=False, stream_fn=None, max_slots=4, approval_timeout_s=120.0, warn=None)` with `run()`, `close()`, `active`, `resident`.

- [ ] **Step 1: Write the failing host tests**

Create `tests/test_rpc_host.py`:

```python
"""The host: one stdio loop, several resident projects, exact routing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zenagent.app.bootstrap import PreparedFrontend
from zenagent.app.runtime import RuntimeOptions
from zenagent.app.taiji import TaijiClaim
from zenagent.cli.rpc.host import RpcHost
from zenagent.cli.rpc.slot import RuntimeSlot
from zenagent.project import project_key


class FakeAgent:
    def __init__(self, name: str) -> None:
        self.messages: list = []
        self.is_streaming = False
        self.termination = "stop"
        self.tools = [
            SimpleNamespace(name=f"{name}-tool", description="", approval="read")
        ]

    def subscribe(self, listener) -> None:
        self._listener = listener

    def abort(self) -> None:
        pass


class FakeChat:
    def __init__(self) -> None:
        self.busy = False

    def reload_transcript(self) -> None:
        pass

    async def wait_for_idle(self) -> None:
        pass


class FakeRuntime:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.cwd = self.root
        self.agent = FakeAgent(self.root.name)
        self.session = SimpleNamespace(path=self.root / "sessions" / "s.jsonl")
        self.ask_handler = None
        self.taiji_chat = None
        self.mode = "ask"
        self.warnings: list[str] = []
        self.closed = False

    def _note(self, text: str) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class FakeBroker:
    def set_confirm(self, fn) -> None:
        self.confirm = fn


class FakeServices:
    models = None
    sessions = None


def _repo(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    return root


@pytest.fixture
async def host_env(tmp_path, monkeypatch):
    opened: dict[str, FakeRuntime] = {}

    async def fake_prepare(options, **kwargs):
        root = Path(options.root).resolve()
        runtime = FakeRuntime(root)
        opened[str(root)] = runtime
        return PreparedFrontend(
            runtime=runtime,
            broker=FakeBroker(),
            chat=FakeChat(),
            services=FakeServices(),
        )

    monkeypatch.setattr("zenagent.cli.rpc.host.prepare", fake_prepare)
    monkeypatch.setattr(
        "zenagent.cli.rpc.slot.claim_taiji_host",
        lambda runtime: TaijiClaim(root=runtime.root, owned=False),
    )

    async def fake_release(claim) -> None:
        return None

    monkeypatch.setattr("zenagent.cli.rpc.slot.release_taiji_host", fake_release)

    alpha = _repo(tmp_path, "alpha")
    beta = _repo(tmp_path, "beta")
    gamma = _repo(tmp_path, "gamma")
    writes: list[dict] = []
    prepared = await fake_prepare(RuntimeOptions(root=alpha))
    slot = RuntimeSlot.adopt(prepared, write_line=writes.append)

    def build(max_slots: int = 4) -> RpcHost:
        return RpcHost(
            initial=slot,
            options=RuntimeOptions(root=alpha),
            read_line=None,
            write_line=writes.append,
            max_slots=max_slots,
        )

    return SimpleNamespace(
        alpha=alpha,
        beta=beta,
        gamma=gamma,
        tmp_path=tmp_path,
        writes=writes,
        build=build,
        opened=opened,
        slot=slot,
    )


async def call(host: RpcHost, writes: list[dict], request: dict) -> dict:
    assert await host._handle_line(json.dumps(request))
    return writes[-1]


async def test_hello_lists_the_active_project_and_residents(host_env):
    host = host_env.build()
    frame = await call(host, host_env.writes, {"type": "hello", "id": "h"})
    assert frame["protocol_version"] == 2
    assert frame["active"] == str(host_env.alpha.resolve())
    assert [item["key"] for item in frame["projects"]] == [
        project_key(host_env.alpha)
    ]


async def test_open_switches_active_and_by_default_routes_there(host_env):
    host = host_env.build()
    opened = await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.beta), "id": "o"},
    )
    assert opened["ok"] is True
    assert opened["active"] == str(host_env.beta.resolve())
    assert len(opened["resident"]) == 2

    requested = await call(host, host_env.writes, {"type": "tools", "id": "t"})
    assert [tool["name"] for tool in requested["tools"]] == ["beta-tool"]


async def test_requests_route_by_the_project_field(host_env):
    host = host_env.build()
    await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.beta), "id": "o"},
    )
    requested = await call(
        host,
        host_env.writes,
        {"type": "tools", "id": "t", "project": str(host_env.alpha)},
    )
    assert [tool["name"] for tool in requested["tools"]] == ["alpha-tool"]
    assert requested["project"] == str(host_env.alpha.resolve())


async def test_a_named_project_that_is_not_resident_is_never_substituted(host_env):
    host = host_env.build()
    frame = await call(
        host,
        host_env.writes,
        {"type": "tools", "id": "t", "project": str(host_env.beta)},
    )
    assert frame["type"] == "error"
    assert frame["code"] == "no_project"


async def test_close_falls_back_to_the_most_recent_resident(host_env):
    host = host_env.build()
    await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.beta), "id": "o"},
    )
    closed = await call(
        host,
        host_env.writes,
        {"type": "project", "op": "close", "root": str(host_env.beta), "id": "c"},
    )
    assert closed["closed"] is True
    assert closed["resident"] == [
        {
            "key": project_key(host_env.alpha),
            "root": str(host_env.alpha.resolve()),
            "session": str(host_env.alpha.resolve() / "sessions" / "s.jsonl"),
        }
    ]
    assert host.active.key == project_key(host_env.alpha)


async def test_lru_eviction_drops_the_oldest_idle_slot(host_env):
    host = host_env.build(max_slots=2)
    await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.beta), "id": "b"},
    )
    alpha_runtime = host_env.opened[str(host_env.alpha.resolve())]
    await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.gamma), "id": "g"},
    )
    assert list(host._slots) == [
        project_key(host_env.beta),
        project_key(host_env.gamma),
    ]
    assert alpha_runtime.closed is True


async def test_eviction_refuses_when_every_slot_is_pinned(host_env):
    host = host_env.build(max_slots=2)
    await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.beta), "id": "b"},
    )
    host_env.slot.chat.busy = True
    host._slots[project_key(host_env.beta)].chat.busy = True
    frame = await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.gamma), "id": "g"},
    )
    assert frame["type"] == "error"
    assert frame["code"] == "project_busy"
    assert len(host._slots) == 2


async def test_relocate_refuses_a_resident_project_and_delegates_otherwise(
    host_env, monkeypatch
):
    host = host_env.build()
    await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.beta), "id": "b"},
    )
    refused = await call(
        host,
        host_env.writes,
        {
            "type": "project",
            "op": "relocate",
            "from": str(host_env.alpha),
            "to": str(host_env.tmp_path / "alpha2"),
            "id": "r1",
        },
    )
    assert refused["code"] == "project_busy"

    await call(
        host,
        host_env.writes,
        {"type": "project", "op": "close", "root": str(host_env.alpha), "id": "c"},
    )
    beta_slot = host._slots[project_key(host_env.beta)]
    calls: list[dict] = []

    async def fake_project(request, request_id):
        calls.append(request)
        beta_slot.server._send(
            {"type": "result", "id": request_id, "ok": True, "delegated": True}
        )

    monkeypatch.setattr(beta_slot.server, "_project", fake_project)
    delegated = await call(
        host,
        host_env.writes,
        {
            "type": "project",
            "op": "relocate",
            "from": str(host_env.alpha),
            "to": str(host_env.tmp_path / "alpha2"),
            "id": "r2",
        },
    )
    assert delegated["delegated"] is True
    assert calls[0]["from"] == str(host_env.alpha)
```

Add the approval-routing test:

```python
async def test_approval_routes_by_its_prefixed_id_even_when_project_names_beta(
    host_env,
):
    host = host_env.build()
    await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.beta), "id": "b"},
    )
    alpha_slot = host._slots[project_key(host_env.alpha)]
    pending = asyncio.create_task(alpha_slot.server.confirm("bash", {}))
    await asyncio.sleep(0)
    request_id = host_env.writes[-1]["event"]["request_id"]
    assert request_id.startswith(f"{project_key(host_env.alpha)}:a")

    frame = await call(
        host,
        host_env.writes,
        {
            "type": "approval",
            "id": "a",
            "project": str(host_env.beta),
            "request_id": request_id,
            "decision": "allow",
        },
    )
    assert frame["ok"] is True
    assert await pending == "allow"
```

- [ ] **Step 2: Run the host tests and watch them fail**

Run: `uv run pytest tests/test_rpc_host.py -q`
Expected: FAIL — `zenagent.cli.rpc.host` does not exist.

- [ ] **Step 3: Implement `RpcHost`**

Create `zenagent/cli/rpc/host.py`:

```python
"""The RPC host: one stdio loop over a registry of resident project slots.

A request may name its project root. Without one, the active slot serves it,
so a v2 client that never learns about slots keeps working exactly as before.
The host owns only routing, the project ops and slot lifetime; every other
request is forwarded verbatim to the target slot's server.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from dataclasses import replace
from typing import TYPE_CHECKING, Callable

from zenagent.app.bootstrap import prepare
from zenagent.cli.rpc.errors import Refusal, error_frame, failure_frame
from zenagent.cli.rpc.server import PROTOCOL_VERSION
from zenagent.cli.rpc.slot import RuntimeSlot
from zenagent.project import find_project_root, project_key

if TYPE_CHECKING:
    from zenagent.app.runtime import RuntimeOptions

logger = logging.getLogger(__name__)

#: Idle projects beyond this are evicted LRU (audit: cap, never unbounded).
MAX_RESIDENT_SLOTS = 4


class RpcHost:
    """Reads requests, routes them to a project slot, and owns slot lifetime."""

    def __init__(
        self,
        *,
        initial: RuntimeSlot | None,
        options: "RuntimeOptions",
        read_line,
        write_line,
        assume_yes: bool = False,
        stream_fn=None,
        max_slots: int = MAX_RESIDENT_SLOTS,
        approval_timeout_s: float = 120.0,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self._options = options
        self._read_line = read_line
        self._write_line = write_line
        self._assume_yes = assume_yes
        self._stream_fn = stream_fn
        self._max_slots = max(1, int(max_slots))
        self._approval_timeout_s = approval_timeout_s
        self._warn = warn
        self._slots: OrderedDict[str, RuntimeSlot] = OrderedDict()
        self._active: RuntimeSlot | None = None
        self._closed = False
        if initial is not None:
            self._slots[initial.key] = initial
            self._active = initial

    # -- introspection ------------------------------------------------------

    @property
    def active(self) -> RuntimeSlot | None:
        return self._active

    @property
    def resident(self) -> list[dict]:
        return [
            {
                "key": key,
                "root": slot.server.project,
                "session": str(slot.runtime.session.path),
            }
            for key, slot in self._slots.items()
        ]

    # -- loop ---------------------------------------------------------------

    async def run(self) -> int:
        while True:
            line = await self._read_line()
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            if not await self._handle_line(line):
                break
        await self.close()
        return 0

    async def _handle_line(self, line: str) -> bool:
        """Handle one request line; False means shutdown."""
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError(  # noqa: TRY004 - bad wire data is an error
                    "request must be a JSON object"
                )
        except ValueError as exc:
            self._send({"type": "error", "id": None, "message": f"bad request: {exc}"})
            return True

        kind = request.get("type")
        request_id = request.get("id")
        if kind == "hello":
            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": True,
                    "protocol_version": PROTOCOL_VERSION,
                    "active": self._active.server.project if self._active else "",
                    "projects": self.resident,
                }
            )
            return True
        if kind == "shutdown":
            self._send({"type": "result", "id": request_id, "ok": True})
            return False
        if kind == "project":
            await self._project(request, request_id)
            return True

        slot = self._slot_for(
            request, prefer_id=kind in ("approval", "ask")
        )
        if slot is None:
            self._send(
                error_frame(
                    request_id,
                    "no project: open one first",
                    "no_project",
                )
            )
            return True
        if kind == "approval":
            return slot.server._resolve_approval(request)
        if kind == "ask":
            return slot.server._resolve_ask(request)
        return await slot.server._handle_line(line)

    # -- slot registry ------------------------------------------------------

    def _slot_for(
        self, request: dict, *, prefer_id: bool = False
    ) -> RuntimeSlot | None:
        """The slot a request targets.

        A named project never falls back to the active slot: running a prompt
        in the wrong project is worse than a ``no_project`` refusal. An
        approval/ask id prefix outranks the project field so a pending run in
        a background project still resolves after the person switched away.
        """
        id_key = self._id_key(request)
        if prefer_id and id_key is not None:
            return self._slots.get(id_key)
        project = request.get("project")
        if isinstance(project, str) and project.strip():
            key = project_key(find_project_root(project))
            slot = self._slots.get(key)
            if slot is not None:
                self._slots.move_to_end(key)
            return slot
        if id_key is not None:
            slot = self._slots.get(id_key)
            if slot is not None:
                return slot
        return self._active

    @staticmethod
    def _id_key(request: dict) -> str | None:
        for field in ("request_id", "id"):
            value = request.get(field)
            if isinstance(value, str) and ":" in value:
                return value.split(":", 1)[0]
        return None

    async def _ensure(self, root: str, *, resume=None) -> RuntimeSlot:
        target = find_project_root(root)
        key = project_key(target)
        slot = self._slots.get(key)
        if slot is not None:
            self._slots.move_to_end(key)
            return slot
        if len(self._slots) >= self._max_slots:
            await self._make_room()
        options = replace(self._options, root=target, resume=resume)
        prepared = await prepare(
            options,
            stream_fn=self._stream_fn,
            assume_yes=self._assume_yes,
        )
        if self._warn is not None:
            for warning in prepared.runtime.warnings:
                self._warn(warning)
        slot = RuntimeSlot.adopt(
            prepared,
            write_line=self._write_line,
            approval_timeout_s=self._approval_timeout_s,
        )
        self._slots[slot.key] = slot
        return slot

    async def _make_room(self) -> None:
        if len(self._slots) < self._max_slots:
            return
        for key, slot in list(self._slots.items()):
            if not slot.pinned:
                await self._drop(key)
                return
        raise Refusal(
            f"all {self._max_slots} project slots are busy", code="project_busy"
        )

    async def _drop(self, key: str) -> None:
        slot = self._slots.pop(key, None)
        if slot is None:
            return
        if self._active is slot:
            self._active = (
                next(reversed(self._slots.values())) if self._slots else None
            )
        await slot.close()

    # -- project ops --------------------------------------------------------

    async def _project(self, request: dict, request_id) -> None:
        op = request.get("op")
        label = str(op or "")
        try:
            if op in ("open", "switch"):
                await self._project_open(request, request_id)
                return
            if op == "close":
                await self._project_close(request, request_id)
                return
            if op == "relocate":
                await self._project_relocate(request, request_id)
                return
            raise Refusal(f"unknown project op: {op}")
        except Refusal as exc:
            self._send(
                error_frame(
                    request_id,
                    str(exc),
                    exc.code or "project_invalid",
                    {"op": label},
                )
            )
        except Exception as exc:  # noqa: BLE001 - a bad op is a result, not a crash
            self._send(
                failure_frame(
                    request_id,
                    code="project_failed",
                    message="project failed",
                    exc=exc,
                    op=label,
                )
            )

    async def _project_open(self, request: dict, request_id) -> None:
        root = request.get("root")
        if not isinstance(root, str) or not root.strip():
            raise Refusal("open needs root")
        slot = await self._ensure(root, resume=request.get("resume"))
        if not request.get("background"):
            self._active = slot
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "root": slot.server.project,
                "key": slot.key,
                "session": str(slot.runtime.session.path),
                "active": self._active.server.project if self._active else "",
                "resident": self.resident,
            }
        )

    async def _project_close(self, request: dict, request_id) -> None:
        root = request.get("root")
        if not isinstance(root, str) or not root.strip():
            raise Refusal("close needs root")
        key = project_key(root)
        slot = self._slots.get(key)
        closed = slot is not None
        if slot is not None:
            if slot.pinned:
                raise Refusal("project is busy", code="project_busy")
            await self._drop(key)
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "closed": closed,
                "active": self._active.server.project if self._active else "",
                "resident": self.resident,
            }
        )

    async def _project_relocate(self, request: dict, request_id) -> None:
        old_root = request.get("from")
        new_root = request.get("to")
        if not isinstance(old_root, str) or not old_root.strip():
            raise Refusal("relocate needs from")
        if not isinstance(new_root, str) or not new_root.strip():
            raise Refusal("relocate needs to")
        if project_key(old_root) in self._slots:
            raise Refusal(
                "cannot relocate an open project; close it first",
                code="project_busy",
            )
        slot = self._active
        if slot is None:
            raise Refusal("no project to serve the relocate", code="no_project")
        await slot.server._project(request, request_id)

    # -- teardown -----------------------------------------------------------

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        slots = list(self._slots.values())
        self._slots.clear()
        self._active = None
        for slot in slots:
            await slot.close()

    def _send(self, payload: dict) -> None:
        if self._write_line is not None:
            self._write_line(
                json.dumps(payload, ensure_ascii=False, default=str)
            )
```

- [ ] **Step 4: Wire the CLI to the host**

Replace the body of `_run` in `zenagent/cli/rpc/cli.py` from `runtime = prepared.runtime` to the end of the function, and drop the now-unused taiji imports at the top:

```python
async def _run(args) -> int:
    from zenagent.app.bootstrap import prepare
    from zenagent.app.modes import resolve_mode
    from zenagent.app.runtime import RuntimeOptions
    from zenagent.cli.rpc.host import RpcHost
    from zenagent.cli.rpc.slot import RuntimeSlot

    # Resolve the mode exactly like the other frontends: an explicit --mode
    # wins, then --read-only (plan), then --yes (zen). Passing the raw
    # ``args.mode`` (None when the flag is omitted) into the gate used to
    # raise "unknown mode: None" for every documented ``zen rpc`` invocation.
    mode = resolve_mode(
        explicit=args.mode, assume_yes=args.yes, read_only=args.read_only
    )
    options = RuntimeOptions(
        root=Path.cwd(),
        model_id=args.model,
        thinking=args.thinking,
        resume=args.resume,
        mcp=not args.no_mcp,
        mcp_file=Path(args.mcp_file) if args.mcp_file else None,
        mcp_servers=args.mcp_servers,
        skills=not args.no_skills,
        templates=not args.no_templates,
        extension_paths=args.extension_paths or [],
        context_files=not args.no_context_files,
        auto_compact=not args.no_auto_compact,
        read_only=args.read_only,
        allow_dangerous=args.allow_dangerous,
        allow_outside=args.allow_outside or [],
        allow_destructive=args.allow_destructive,
        allow_protected=args.allow_protected,
        mode=mode,
        retries=args.retries,
        max_turns=args.max_turns,
        max_cost=args.max_cost,
        trace=args.trace,
        metrics=args.metrics,
    )
    try:
        prepared = await prepare(options, assume_yes=args.yes)
    except KeyError as exc:
        print(f"zenagent: unknown model: {exc}", file=sys.stderr)
        return 2
    except (RuntimeError, LookupError) as exc:
        print(f"zenagent: {exc}", file=sys.stderr)
        return 2
    for warning in prepared.runtime.warnings:
        print(f"zenagent: {warning}", file=sys.stderr)

    initial = RuntimeSlot.adopt(prepared, write_line=_stdout_line)
    host = RpcHost(
        initial=initial,
        options=options,
        read_line=_stdin_line,
        write_line=_stdout_line,
        assume_yes=args.yes,
        warn=lambda text: print(f"zenagent: {text}", file=sys.stderr),
    )
    try:
        return await host.run()
    finally:
        await host.close()
```

Remove these imports from the top of `zenagent/cli/rpc/cli.py` (no longer used):

```python
from zenagent.app.taiji import (
    close_taiji,
    ensure_taiji_session,
    own_taiji_lock,
    release_taiji_lock,
    should_init_taiji,
)
from zenagent.cli.rpc.server import RpcServer
from zenagent.project import (
    project_dir,
    project_sessions_dir,
)
```

- [ ] **Step 5: Run the host, slot and CLI tests**

Run:

```bash
uv run pytest tests/test_rpc_host.py tests/test_rpc_slot.py tests/test_rpc_v2.py tests/test_taiji_worker_client.py tests/test_examples.py -q
```

Expected: PASS. The documented smoke client (`status`, `shutdown`) still works because the active slot answers both.

- [ ] **Step 6: Commit**

```bash
git add zenagent/cli/rpc/host.py zenagent/cli/rpc/cli.py tests/test_rpc_host.py
git commit -m "feat(rpc): host resident project slots behind one stdio loop"
```

---

### Task 5: End-to-end acceptance — two projects in one host

**Files:**
- Test: new `tests/test_rpc_host_e2e.py`
- Modify: `docs/architecture/allowlist.json` only if the measured files grew past their registration

**Interfaces:**
- Consumes everything from Tasks 1-4 plus real `prepare`, real `Runtime.bootstrap`, real `TaijiSupervisor`.
- Produces: the acceptance gate from the audit: one child serves A and B; status/tools route; taiji is per root; close releases; LRU evicts; relocate refuses resident and works after close.

- [ ] **Step 1: Write the acceptance test**

Create `tests/test_rpc_host_e2e.py`:

```python
"""Phase B acceptance: two real projects, one host, no cross-project leaks."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from zenagent import project
from zenagent.app.bootstrap import prepare
from zenagent.app.runtime import RuntimeOptions
from zenagent.app.taiji import existing_taiji
from zenagent.app.taiji.supervisor import _git
from zenagent.cli.rpc.host import RpcHost
from zenagent.cli.rpc.slot import RuntimeSlot
from zenagent.config import UserConfig

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not installed"
)


def _repo(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "phase-b@example.com")
    _git(root, "config", "user.name", "phase-b")
    (root / "note.txt").write_text(f"{name} note\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    return root


def _options(root: Path) -> RuntimeOptions:
    return RuntimeOptions(
        root=root,
        mcp=False,
        skills=False,
        templates=False,
        context_files=False,
        mode="ask",
    )


@pytest.fixture
async def host(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    for target in (
        "zenagent.app.bootstrap.load_user_config",
        "zenagent.app.runtime.load_user_config",
    ):
        monkeypatch.setattr(
            target,
            lambda *args, **kwargs: UserConfig(default_model="deepseek-flash"),
        )
    alpha = _repo(tmp_path, "alpha")
    beta = _repo(tmp_path, "beta")
    gamma = _repo(tmp_path, "gamma")
    prepared = await prepare(_options(alpha), assume_yes=True)
    writes: list[dict] = []
    initial = RuntimeSlot.adopt(prepared, write_line=writes.append)
    instance = RpcHost(
        initial=initial,
        options=_options(alpha),
        read_line=None,
        write_line=writes.append,
        assume_yes=True,
        max_slots=2,
    )
    try:
        yield instance, writes, alpha, beta, gamma, tmp_path
    finally:
        await instance.close()


async def call(host: RpcHost, writes: list[dict], request: dict) -> dict:
    assert await host._handle_line(json.dumps(request))
    return writes[-1]


async def test_two_projects_share_one_host_without_leaking(host):
    instance, writes, alpha, beta, _, _ = host
    opened = await call(
        instance, writes, {"type": "project", "op": "open", "root": str(beta), "id": "o"}
    )
    assert opened["ok"] is True
    assert opened["active"] == str(beta.resolve())
    assert {item["key"] for item in opened["resident"]} == {
        project.project_key(alpha),
        project.project_key(beta),
    }

    alpha_slot = instance._slots[project.project_key(alpha)]
    beta_slot = instance._slots[project.project_key(beta)]
    assert alpha_slot.runtime.root == alpha.resolve()
    assert beta_slot.runtime.root == beta.resolve()
    assert alpha_slot.runtime.session.path.parent == project.project_sessions_dir(
        root=alpha
    )
    assert beta_slot.runtime.session.path.parent == project.project_sessions_dir(
        root=beta
    )

    alpha_tools = await call(
        instance,
        writes,
        {"type": "tools", "id": "t", "project": str(alpha)},
    )
    names = {tool["name"] for tool in alpha_tools["tools"]}
    assert "read_file" in names
    assert alpha_tools["project"] == str(alpha.resolve())

    assert existing_taiji(alpha) is not None
    assert existing_taiji(beta) is not None
    assert existing_taiji(alpha) is not existing_taiji(beta)


async def test_close_releases_the_project_and_relocate_then_succeeds(host):
    instance, writes, alpha, beta, _, tmp_path = host
    moved_root = tmp_path / "alpha2"
    moved_root.mkdir()
    await call(
        instance, writes, {"type": "project", "op": "open", "root": str(beta), "id": "o"}
    )

    refused = await call(
        instance,
        writes,
        {
            "type": "project",
            "op": "relocate",
            "from": str(alpha),
            "to": str(moved_root),
            "id": "r1",
        },
    )
    assert refused["code"] == "project_busy"

    closed = await call(
        instance,
        writes,
        {"type": "project", "op": "close", "root": str(alpha), "id": "c"},
    )
    assert closed["closed"] is True
    assert existing_taiji(alpha) is None
    assert not (
        project.project_dir(root=alpha) / "taiji.lock"
    ).exists()

    moved = await call(
        instance,
        writes,
        {
            "type": "project",
            "op": "relocate",
            "from": str(alpha),
            "to": str(moved_root),
            "id": "r2",
        },
    )
    assert moved["ok"] is True
    assert moved["old_key"] == project.project_key(alpha)
    assert moved["new_key"] == project.project_key(moved_root)
    assert moved["moved"] is True


async def test_lru_caps_residents_and_releases_the_evicted_taiji(host):
    instance, writes, alpha, beta, gamma, _ = host
    await call(
        instance, writes, {"type": "project", "op": "open", "root": str(beta), "id": "b"}
    )
    await call(
        instance, writes, {"type": "project", "op": "open", "root": str(gamma), "id": "g"}
    )
    assert project.project_key(alpha) not in instance._slots
    assert project.project_key(beta) in instance._slots
    assert project.project_key(gamma) in instance._slots
    assert existing_taiji(alpha) is None
```

- [ ] **Step 2: Run it and fix only real leaks**

Run: `uv run pytest tests/test_rpc_host_e2e.py -q`
Expected: PASS. If it fails because two runtimes share a session file, a
project root was not resolved consistently — fix `_options`/`RuntimeSlot`
root handling, not the test.

- [ ] **Step 3: Run the full gate**

```bash
uv run ruff check .
python3 scripts/check_architecture.py
uv run pytest -q
```

Expected: ruff clean, `architecture budget ok`, the full suite green
(Phase A baseline: `2594 passed`). If `docs/architecture/allowlist.json`
reports a grown allowlisted file, register its measured size and re-run.

- [ ] **Step 4: Commit**

```bash
git add tests/test_rpc_host_e2e.py docs/architecture/allowlist.json
git commit -m "test(rpc): prove two projects share one host without leaking"
```

---

## Self-Review

**Spec coverage (audit Phase B items):**

1. Split `RpcServer` into transport plus `RuntimeSlot` — Task 3 (`RuntimeSlot` composes the server; the server keeps its wire handlers).
2. Optional `project` request field, missing means current slot — Task 4 `_slot_for`.
3. Tag every event frame, prefix approval/ask ids — Task 3.
4. `project open` / `switch` / `close` — Task 4; lazy build via `Runtime.bootstrap` + resume + transcript seed (`RpcServer.__init__` already reloads the transcript).
5. Cap resident slots LRU (4), release MCP/taiji/jobs on close — Task 3 `RuntimeSlot.close` + Task 4 `_make_room`/`_drop`.
6. Taiji per root, job manager global with project-scoped views — Task 1 registry/claim + Task 2 job filter.

**Placeholder scan:** no TBD/TODO; every code step carries the code.
The only measurement left to execution is the server's post-edit line count,
which Task 3 registers with an explicit `wc -l` command.

**Type consistency:** `TaijiClaim(root, owned)`, `claim_taiji_host(runtime)`,
`release_taiji_host(claim)`, `RuntimeSlot.adopt(prepared, *, write_line,
approval_timeout_s)`, `RpcHost(initial, options, read_line, write_line, ...)`,
`RpcServer(..., project=...)` are used with the same names and arities across
Tasks 1-5. `existing_taiji(root)` takes an optional root everywhere it is
called after Task 1.
