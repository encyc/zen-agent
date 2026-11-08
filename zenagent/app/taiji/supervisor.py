"""The taiji supervisor: spawns workers, tracks their turns, cleans up.

One supervisor per project host. Workers are ``zen rpc`` subprocesses bound
to their own worktree via a :class:`WorkerRunner`; the supervisor owns the
registry, the turn lifecycle and the merge orchestration. The git plumbing
lives in :mod:`zenagent.app.taiji.worktree`, the merge computations in
:mod:`zenagent.app.taiji.merge_gate`, and the worker RPC protocol in
:mod:`zenagent.app.taiji.rpc_runner` (re-exported here for import compat).
"""

from __future__ import annotations

import asyncio
import json as _json
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from zenagent.app.git import git_status
from zenagent.app.taiji import board, merge_gate
from zenagent.app.taiji.merge_gate import AUTO_MERGE_MAX_DIFF_LINES
from zenagent.app.taiji.registry import TaijiRegistry, WorkerRecord
from zenagent.app.taiji.rpc_runner import RpcRunner, TurnResult
from zenagent.app.taiji.worktree import (
    BRANCH_PREFIX,
    _git,
    _git_async,
    _slug,
    branch_ref_exists,
    retire_worktree,
    rollback,
    worktrees_root,
)
from zenagent.project import project_key

CLOSE_GRACE_S = 5.0

#: Import-compat surface: the supervisor used to own all of these; the
#: modules under ``app/taiji`` do now, but existing importers (tools.py,
#: tests, monkeypatch targets) keep working through this namespace.
__all__ = [
    "AUTO_MERGE_MAX_DIFF_LINES",
    "BRANCH_PREFIX",
    "CLOSE_GRACE_S",
    "RpcRunner",
    "TurnResult",
    "_git",
    "_git_async",
    "_slug",
    "worktrees_root",
]


def _attention_question(record: WorkerRecord) -> str:
    parsed = board.parse_attention(record.attention)
    if parsed is None:
        return ""
    return parsed["question"]


def supervisor_summary(record: WorkerRecord, summary: str, duration_s: float) -> str:
    """The one-line milestone the Router receives for a finished turn."""
    brief = summary if len(summary) <= 200 else summary[:197] + "…"
    minutes, seconds = int(duration_s) // 60, int(duration_s) % 60
    return f"[worker {record.branch} · {record.status}] {brief} · {minutes:02d}:{seconds:02d}"


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
        max_workers: int = 3,
    ) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.sessions_dir = Path(sessions_dir)
        self.registry_path = Path(registry_path)
        self.runner = runner
        self.chat = chat
        self.mode_of = mode_of
        self.registry = TaijiRegistry()
        self.registry.load(self.registry_path)
        self.registry.reconcile(self._worktree_paths())
        self._tasks: set[asyncio.Task[None]] = set()
        self._turns: dict[str, asyncio.Task[None]] = {}
        self._slots = asyncio.Semaphore(max_workers)
        self._max_workers = max_workers
        self._busy = 0

    def _worktree_paths(self) -> list[str]:
        # Boot-time reconcile keeps the sync form: the event loop is not
        # serving traffic yet when the supervisor is constructed.
        listed = _git(self.repo_root, "worktree", "list", "--porcelain")
        if listed is None or listed.returncode != 0:
            return []
        return [
            line[len("worktree ") :]
            for line in listed.stdout.splitlines()
            if line.startswith("worktree ")
        ]

    # -- registry ----------------------------------------------------------

    def list_workers(self) -> list[WorkerRecord]:
        return list(self.registry.records.values())

    def find_record(self, name: str) -> WorkerRecord | None:
        """The worker's registry row, or ``None`` when there is no such worker."""
        return self.registry.records.get(name)

    def has_running(self, name: str) -> bool:
        """Whether the worker holds a live turn right now.

        Surfaces read this *before* awaiting ``message`` so ``queued`` on
        the reply means "the text rides the current turn's tail", not "a
        turn we just started".
        """
        record = self.find_record(name)
        return record is not None and record.status == "running"

    def slots_state(self) -> dict:
        """The dispatch board's slot gauge.

        ``busy`` counts slot *holders* — turns that went through the
        semaphore. Inbox-chained and human-wake turns run outside the
        slots (they must never queue behind work), so they are not
        counted here and ``busy`` may legitimately stay below the true
        number of live turns.
        """
        queued = sum(
            1 for record in self.registry.records.values() if record.status == "queued"
        )
        return {"busy": self._busy, "max": self._max_workers, "queued": queued}

    def _save(self) -> None:
        self.registry.save(self.registry_path)

    def _record(self, name: str) -> WorkerRecord:
        return self.registry.records[name]

    # -- spawn / message / kill ---------------------------------------------

    async def _free_name(self, slug: str) -> str:
        """A worker name whose branch does not exist in git or the registry.

        The registry forgets finished workers whose worktree was cleaned up,
        but their branches stay — so git refs, not just records, decide.
        """

        async def taken(name: str) -> bool:
            if name in self.registry.records:
                return True
            return await asyncio.to_thread(branch_ref_exists, self.repo_root, name)

        name, bump = slug, 2
        while await taken(name):
            name = f"{slug}-{bump}"
            bump += 1
        return name

    async def spawn(self, prompt: str, *, name: str | None = None) -> WorkerRecord:
        slug = _slug(name if name else prompt[:24])
        worker = await self._free_name(slug)
        branch = BRANCH_PREFIX + worker
        worktree = worktrees_root() / project_key(self.repo_root) / worker

        current = await _git_async(self.repo_root, "branch", "--show-current")
        base = (
            current.stdout.strip()
            if current is not None and current.stdout.strip()
            else "main"
        )
        branched = await _git_async(self.repo_root, "branch", branch, base)
        if branched is None or branched.returncode != 0:
            detail = branched.stderr.strip() if branched else "git is not available"
            raise RuntimeError(f"branch {branch} failed: {detail}")
        added = await _git_async(self.repo_root, "worktree", "add", str(worktree), branch)
        if added is None or added.returncode != 0:
            await _git_async(self.repo_root, "branch", "-D", branch)
            raise RuntimeError(
                f"worktree add failed: {added.stderr.strip() if added else 'git missing'}"
            )

        record = WorkerRecord(
            name=worker,
            branch=branch,
            base=base,
            worktree=str(worktree),
            status="running",
            created_at=time.time(),
            updated_at=time.time(),
            prompt=prompt,
        )
        self.registry.records[worker] = record
        self._save()
        self._start_turn(
            record, prompt, resume=None, rollback_on_start_failure=True, use_slot=True
        )
        return record

    async def _cancel_queued_turn(self, name: str) -> None:
        """Cancel a worker's queued (not yet started) spawn turn."""
        queued = self._turns.get(name)
        if queued is not None and not queued.done():
            queued.cancel()
            await asyncio.gather(queued, return_exceptions=True)

    async def message(self, name: str, text: str) -> WorkerRecord:
        record = self._record(name)
        if record.status == "killed":
            raise RuntimeError(f"worker {name} is {record.status}")
        if record.status == "queued":
            # A human redirect supersedes the queued spawn turn: cancel it so
            # exactly one turn for this worker ever runs.
            await self._cancel_queued_turn(name)
            record.inbox.clear()
        else:
            # The worker is turn-based: while a turn is live — running, or
            # past its terminal status but still inside the milestone emit —
            # input cannot be injected, so the words wait in the inbox and
            # ride the turn chain's tail. _turns presence, not status,
            # decides: status flips to terminal before the emit finishes.
            live = self._turns.get(name)
            if record.status == "running" or live is not None:
                record.inbox.append(text)
                self._save()
                return record
        record.status = "running"
        record.updated_at = time.time()
        self._save()
        self._start_turn(
            record,
            text,
            resume=record.session or None,
            rollback_on_start_failure=False,
            use_slot=False,
        )
        return record

    async def kill(self, name: str) -> WorkerRecord:
        record = self._record(name)
        record.inbox.clear()
        task = self._turns.get(name)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        removed = await _git_async(
            self.repo_root, "worktree", "remove", "--force", record.worktree
        )
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
        use_slot: bool,
    ) -> None:
        task = asyncio.create_task(
            self._run_turn(record, prompt, resume, rollback_on_start_failure, use_slot)
        )
        self._tasks.add(task)
        self._turns[record.name] = task

        def _settled(done: asyncio.Task[None]) -> None:
            self._tasks.discard(done)
            # Only clear our own slot: a successor turn (chained inbox or a
            # human message) may already have claimed the mapping, and a
            # stale clear would make kill cancel nothing (P2 kill race).
            if self._turns.get(record.name) is done:
                self._turns.pop(record.name, None)

        task.add_done_callback(_settled)

    async def _execute_turn(
        self,
        record: WorkerRecord,
        resume: str | None,
        prompt: str,
        started: float,
        *,
        rollback_on_start_failure: bool,
    ) -> TurnResult | None:
        """Run one turn and absorb its failure states; None when absorbed."""
        try:
            result = await self.runner.run_turn(record.worktree, resume, prompt)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed turn is a state, not a crash
            if rollback_on_start_failure and not record.session:
                await self._rollback(record)
                await self._emit_turn(
                    record, f"启动失败：{exc}", started, status="error"
                )
                return None
            record.status = "error"
            record.summary = str(exc)
            record.updated_at = time.time()
            self._save()
            await self._emit_turn(record, str(exc), started, status="error")
            return None
        if record.status == "killed":
            return None  # a kill during the turn is final; do not overwrite
        return result

    async def _run_turn(
        self,
        record: WorkerRecord,
        prompt: str,
        resume: str | None,
        rollback_on_start_failure: bool,
        use_slot: bool,
    ) -> None:
        started = time.time()
        if use_slot:
            if self._slots.locked():
                record.status = "queued"
                record.updated_at = time.time()
                self._save()
            await self._slots.acquire()
            try:
                self._busy += 1
                if record.status == "queued":
                    record.status = "running"
                    record.updated_at = time.time()
                    self._save()
                result = await self._execute_turn(
                    record,
                    resume,
                    prompt,
                    started,
                    rollback_on_start_failure=rollback_on_start_failure,
                )
            finally:
                self._busy -= 1
                self._slots.release()
        else:
            # Human wake-ups and inbox chains run outside the slots: they
            # must never queue behind spawned work.
            result = await self._execute_turn(
                record,
                resume,
                prompt,
                started,
                rollback_on_start_failure=rollback_on_start_failure,
            )
        if result is None:
            return
        record.status = result.status
        record.summary = result.summary
        record.session = result.session_path
        record.attention = (
            _json.dumps(result.attention, ensure_ascii=False)
            if result.attention
            else ""
        )
        record.updated_at = time.time()
        self._save()
        pending = list(record.inbox)
        record.inbox.clear()
        self._save()
        await self._emit_turn(record, result.summary, started, status=result.status)
        # Words that arrived during the emit window land after the pop; the
        # chain must carry them too, in arrival order.
        pending = [*pending, *record.inbox]
        record.inbox.clear()
        if pending and record.status not in ("killed", "merged"):
            if self._turns.get(record.name) is asyncio.current_task():
                self._start_turn(
                    record,
                    "\n\n".join(pending),
                    resume=record.session or None,
                    rollback_on_start_failure=False,
                    use_slot=False,
                )
            else:
                # A successor claimed the slot while we were emitting; hand
                # the words to its tail instead of running a parallel turn.
                record.inbox[:0] = pending
                self._save()

    async def _rollback(self, record: WorkerRecord) -> None:
        await asyncio.to_thread(rollback, self.repo_root, record.branch, record.worktree)
        self.registry.records.pop(record.name, None)
        self._save()

    async def _emit_turn(
        self, record: WorkerRecord, summary: str, started: float, *, status: str
    ) -> None:
        """Milestone injection plus the deterministic merge gate.

        zen mode merges finished work automatically (the LLM is never in the
        critical path — the gate is this code); ask mode and a zen mode whose
        preconditions are unmet stop at an acceptance question; plan mode
        only reports.
        """
        if (
            status == "ok"
            and not record.attention
            and self.mode_of() == "zen"
            and await self.merge(record.name, auto=True)
        ):
            status = "merged"
        if self.chat is None:
            return
        await self.chat.submit(
            supervisor_summary(record, summary, time.time() - started)
        )
        if record.attention:
            await self.chat.submit(
                f"worker {record.branch} 提问：{_attention_question(record)}。"
                f"回答后用 message_worker 转达给 {record.name}。"
            )
        elif status == "ok" and self.mode_of() in ("zen", "ask"):
            await self.chat.submit(
                f"任务 {record.branch} 完成，待验收：{record.summary or summary}。"
                f"请说「合并 {record.branch}」或提返工意见。"
            )

    def _diff_lines(self, record: WorkerRecord) -> int:
        """Added plus deleted lines between the base and the task branch."""
        return merge_gate.diff_lines(self.repo_root, record)

    def diffstat(self, record: WorkerRecord) -> list[dict] | None:
        """Per-file added/deleted counts for the acceptance card's evidence.

        Sync git — the board projects off-loop for this same reason; never
        call it on the event loop.
        """
        return merge_gate.compute_diffstat(self.repo_root, record)

    async def merge_block_reason(self, name: str) -> str | None:
        """Why an explicit merge would refuse, in board-displayable words.

        Mirrors :meth:`merge`'s preconditions without executing it; None
        means the merge would be attempted.
        """
        record = self.registry.records.get(name)
        if record is None or record.status != "ok":
            return "任务未处于待验收状态"
        return await asyncio.to_thread(merge_gate.block_reason, self.repo_root, record)

    async def merge(self, name: str, *, auto: bool = False) -> bool:
        """Merge a finished task into its base branch, mode-agnostic.

        Explicit merges (the Router tool) work in every mode — the mode gate
        only decides whether zen merges *without asking*. Preconditions keep
        the user's working tree sacred: the base branch must be checked out
        in the main worktree and clean. The diff-size threshold guards the
        zen *auto* merge only — an explicit human-directed merge is the
        escape hatch for large work.
        """
        record = self.registry.records.get(name)
        if record is None or record.status != "ok":
            return False
        if (
            auto
            and await asyncio.to_thread(merge_gate.diff_lines, self.repo_root, record)
            > AUTO_MERGE_MAX_DIFF_LINES
        ):
            return False
        current = await _git_async(self.repo_root, "branch", "--show-current")
        if current is None or current.stdout.strip() != record.base:
            return False
        status = await asyncio.to_thread(git_status, self.repo_root)
        if status is None or not status.is_clean:
            return False
        completed = await _git_async(
            self.repo_root,
            "merge",
            "--no-ff",
            record.branch,
            "-m",
            f"taiji: merge {record.branch}",
        )
        if completed is None or completed.returncode != 0:
            # A conflicted merge would otherwise leave MERGE_HEAD and markers
            # in the user's working tree — the tree stays sacred.
            await _git_async(self.repo_root, "merge", "--abort")
            return False
        record.status = "merged"
        record.updated_at = time.time()
        self._save()
        await self._retire_worktree(record)
        return True

    async def _retire_worktree(self, record: WorkerRecord) -> None:
        """Remove the worker's worktree unless it still holds uncommitted work."""
        await asyncio.to_thread(retire_worktree, self.repo_root, record.worktree)

    async def wait_idle(self) -> None:
        """Wait for every in-flight worker turn (tests and shutdown)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def close(self) -> None:
        """Stop the fleet promptly: a short grace, then cancel stragglers.

        Lifecycle (a): the host owns the process tree, so cancelled turns
        leave their worker subprocesses to die with the host; their records
        become ``interrupted`` and their sessions survive on disk.
        """
        if self._tasks:
            _done, pending = await asyncio.wait(self._tasks, timeout=CLOSE_GRACE_S)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        for record in self.registry.records.values():
            if record.status == "running":
                record.status = "interrupted"
        self._save()


_SINGLETON: TaijiSupervisor | None = None


def get_taiji(**kwargs: Any) -> TaijiSupervisor:
    """The process-wide supervisor; first call's arguments win."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = TaijiSupervisor(**kwargs)
    return _SINGLETON


def existing_taiji() -> TaijiSupervisor | None:
    """The live supervisor, or None when this host never started taiji.

    RPC panel ops consult this: a worker subprocess or a linked worktree
    must read ``active: false``, never silently construct a bare fleet.
    """
    return _SINGLETON


async def close_taiji() -> None:
    global _SINGLETON
    if _SINGLETON is not None:
        await _SINGLETON.close()
        _SINGLETON = None
