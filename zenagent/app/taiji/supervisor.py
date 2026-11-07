"""The taiji supervisor: spawns workers, tracks their turns, cleans up.

One supervisor per project host. Workers are ``zen rpc`` subprocesses bound
to their own worktree via a :class:`WorkerRunner`; the supervisor owns the
git plumbing (branch + worktree per task), the registry, and the rollback
paths. Milestone injection and the merge gate live on top (see below).
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from zenagent.app.taiji.registry import TaijiRegistry, WorkerRecord

GIT_TIMEOUT_S = 10.0
_SLUG_KEEP = re.compile(r"[^a-z0-9._-]+")
BRANCH_PREFIX = "task/"


def _slug(text: str) -> str:
    slug = _SLUG_KEEP.sub("-", text.lower()).strip("-._")
    return slug or "task"


def worktrees_root() -> Path:
    """Where worker worktrees live: outside the user's repository."""
    return Path.home() / ".zenagent" / "worktrees"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        return subprocess.run(
            [git, *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


@dataclass
class TurnResult:
    status: str
    summary: str
    session_path: str


class WorkerRunner(Protocol):
    async def run_turn(
        self, worktree: str, session_path: str | None, prompt: str
    ) -> TurnResult: ...


class TaijiSupervisor:
    """Owns the worker fleet for one project host."""

    def __init__(
        self,
        *,
        repo_root: Path,
        sessions_dir: Path,
        registry_path: Path,
        runner: WorkerRunner,
        chat: Any = None,
        mode_of: Callable[[], str],
    ) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.sessions_dir = Path(sessions_dir)
        self.registry_path = Path(registry_path)
        self.runner = runner
        self.chat = chat
        self.mode_of = mode_of
        self.registry = TaijiRegistry()
        self.registry.load(self.registry_path)
        self._tasks: set[asyncio.Task[None]] = set()

    # -- registry ----------------------------------------------------------

    def list_workers(self) -> list[WorkerRecord]:
        return list(self.registry.records.values())

    def _save(self) -> None:
        self.registry.save(self.registry_path)

    def _record(self, name: str) -> WorkerRecord:
        return self.registry.records[name]

    # -- spawn / message / kill ---------------------------------------------

    def _free_name(self, slug: str) -> str:
        name, bump = slug, 2
        while name in self.registry.records:
            name = f"{slug}-{bump}"
            bump += 1
        return name

    async def spawn(self, prompt: str, *, name: str | None = None) -> WorkerRecord:
        slug = _slug(name if name else prompt[:24])
        worker = self._free_name(slug)
        branch = BRANCH_PREFIX + worker
        worktree = worktrees_root() / worker

        if _git(self.repo_root, "branch", branch, "main") is None:
            raise RuntimeError("git is not available")
        added = _git(self.repo_root, "worktree", "add", str(worktree), branch)
        if added is None or added.returncode != 0:
            _git(self.repo_root, "branch", "-D", branch)
            raise RuntimeError(
                f"worktree add failed: {added.stderr.strip() if added else 'git missing'}"
            )

        record = WorkerRecord(
            name=worker,
            branch=branch,
            base="main",
            worktree=str(worktree),
            status="running",
            created_at=time.time(),
            updated_at=time.time(),
        )
        self.registry.records[worker] = record
        self._save()
        self._start_turn(record, prompt, resume=None, rollback_on_start_failure=True)
        return record

    async def message(self, name: str, text: str) -> WorkerRecord:
        record = self._record(name)
        if record.status in ("running", "killed"):
            raise RuntimeError(f"worker {name} is {record.status}")
        record.status = "running"
        record.updated_at = time.time()
        self._save()
        self._start_turn(
            record, text, resume=record.session or None, rollback_on_start_failure=False
        )
        return record

    async def kill(self, name: str) -> WorkerRecord:
        record = self._record(name)
        removed = _git(self.repo_root, "worktree", "remove", "--force", record.worktree)
        if removed is None or removed.returncode != 0:
            shutil.rmtree(record.worktree, ignore_errors=True)
        record.status = "killed"
        record.updated_at = time.time()
        self._save()
        return record

    # -- turns ---------------------------------------------------------------

    def _start_turn(
        self,
        record: WorkerRecord,
        prompt: str,
        *,
        resume: str | None,
        rollback_on_start_failure: bool,
    ) -> None:
        task = asyncio.create_task(
            self._run_turn(record, prompt, resume, rollback_on_start_failure)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_turn(
        self,
        record: WorkerRecord,
        prompt: str,
        resume: str | None,
        rollback_on_start_failure: bool,
    ) -> None:
        started = time.time()
        try:
            result = await self.runner.run_turn(record.worktree, resume, prompt)
        except Exception as exc:  # noqa: BLE001 - a failed turn is a state, not a crash
            if rollback_on_start_failure and not record.session:
                self._rollback(record)
                await self._emit_turn(
                    record, f"启动失败：{exc}", started, status="error"
                )
                return
            record.status = "error"
            record.summary = str(exc)
            record.updated_at = time.time()
            self._save()
            await self._emit_turn(record, str(exc), started, status="error")
            return
        record.status = result.status
        record.summary = result.summary
        record.session = result.session_path
        record.updated_at = time.time()
        self._save()
        await self._emit_turn(record, result.summary, started, status=result.status)

    def _rollback(self, record: WorkerRecord) -> None:
        _git(self.repo_root, "worktree", "remove", "--force", record.worktree)
        shutil.rmtree(record.worktree, ignore_errors=True)
        _git(self.repo_root, "branch", "-D", record.branch)
        self.registry.records.pop(record.name, None)
        self._save()

    async def _emit_turn(
        self, record: WorkerRecord, summary: str, started: float, *, status: str
    ) -> None:
        """Report the turn milestone; the merge gate joins in Task 5."""
        self._on_milestone(record, summary, started, status=status)

    def _on_milestone(
        self, record: WorkerRecord, summary: str, started: float, *, status: str
    ) -> None:
        del record, summary, started, status  # injection lands in Task 5

    async def wait_idle(self) -> None:
        """Wait for every in-flight worker turn (tests and shutdown)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def close(self) -> None:
        await self.wait_idle()


_SINGLETON: TaijiSupervisor | None = None


def get_taiji(**kwargs: Any) -> TaijiSupervisor:
    """The process-wide supervisor; first call's arguments win."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = TaijiSupervisor(**kwargs)
    return _SINGLETON


async def close_taiji() -> None:
    global _SINGLETON
    if _SINGLETON is not None:
        await _SINGLETON.close()
        _SINGLETON = None
