"""The taiji supervisor: spawns workers, tracks their turns, cleans up.

One supervisor per project host. Workers are ``zen rpc`` subprocesses bound
to their own worktree via a :class:`WorkerRunner`; the supervisor owns the
git plumbing (branch + worktree per task), the registry, and the rollback
paths. Milestone injection and the merge gate live on top (see below).
"""

from __future__ import annotations

import asyncio
import json as _json
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from zenagent.app.git import git_status
from zenagent.app.taiji import board
from zenagent.app.taiji.registry import TaijiRegistry, WorkerRecord
from zenagent.project import project_key

CLOSE_GRACE_S = 5.0
AUTO_MERGE_MAX_DIFF_LINES = 200

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


async def _git_async(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """``_git`` off the event loop — async paths call this, never ``_git``.

    One slow git (a locked repo, a cold NFS mount) must not stall the RPC
    pump or the Router chat; only boot-time reconcile keeps the sync form.
    """
    return await asyncio.to_thread(_git, root, *args)


@dataclass
class TurnResult:
    status: str
    summary: str
    session_path: str
    #: A worker question waiting on a person (from an ``ask_request`` event).
    attention: dict | None = None


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


class RpcRunner:
    """The real WorkerRunner: one ``zen rpc`` subprocess per turn.

    The worker session persists on disk across turns (lifecycle (a)): each
    turn spawns a fresh subprocess bound to the worktree and resumes the
    session, so a host restart costs a re-spawn, not the transcript.
    """

    def __init__(
        self,
        *,
        timeout_s: float = 1800.0,
        client_factory: Callable[[], Any] | None = None,
        attention_grace_s: float = 2.0,
        worker_model: str | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._client_factory = client_factory
        self._attention_grace_s = attention_grace_s
        self._worker_model = worker_model

    async def run_turn(
        self, worktree: str, session_path: str | None, prompt: str
    ) -> TurnResult:
        from zenagent.app.taiji.worker_client import WorkerRpcClient

        client = (
            self._client_factory()
            if self._client_factory
            else WorkerRpcClient(model=self._worker_model)
        )
        events: list[dict] = []
        asked = asyncio.Event()

        def on_event(event: dict) -> None:
            events.append(event)
            if event.get("type") == "ask_request":
                asked.set()

        await client.start(Path(worktree), on_event=on_event)
        attention = None
        summary = ""
        answer = None
        try:
            if session_path:
                resume_result = await client.resume(session_path)
                if resume_result.get("ok") is not True:
                    return TurnResult(
                        status="error",
                        summary=f"resume failed: {resume_result.get('message', '')}",
                        session_path=str(session_path),
                    )
            prompt_task = asyncio.create_task(
                client.prompt(prompt, timeout_s=self._timeout_s)
            )
            asked_wait = asyncio.create_task(asked.wait())
            done, _pending = await asyncio.wait(
                [prompt_task, asked_wait], return_when=asyncio.FIRST_COMPLETED
            )
            if prompt_task in done:
                answer = prompt_task.result()
                asked_wait.cancel()
            else:
                # the worker asked a question: the run blocks on it, so the
                # turn ends here and the question becomes the attention
                await asyncio.sleep(self._attention_grace_s)
                await client.stop()
                asked_wait.cancel()
        finally:
            await client.stop()
        if answer is None:
            # the turn ended on the question; that question is the attention
            for event in events:
                if event.get("type") == "ask_request":
                    questions = event.get("questions") or []
                    options: list[str] = []
                    for question in questions:
                        if not isinstance(question, dict):
                            continue
                        for option in question.get("options") or []:
                            label = (
                                option.get("label", "")
                                if isinstance(option, dict)
                                else ""
                            )
                            if isinstance(label, str) and label:
                                options.append(label)
                    attention = {
                        "request_id": str(event.get("request_id", "")),
                        "question": "; ".join(
                            str(question.get("question", ""))
                            for question in questions
                            if isinstance(question, dict)
                        ).strip("; "),
                        "options": options[:4],
                    }
        if answer is not None and answer.get("ok") is False:
            # A graceful ok:false frame (provider error, max turns, abort) is
            # a failed turn — never a mergeable success.
            reason = str(answer.get("error") or answer.get("text") or "turn failed")
            return TurnResult(
                status="error",
                summary=reason[:2000],
                session_path=str(answer.get("session") or session_path or ""),
                attention=attention,
            )
        if answer is None:
            return TurnResult(
                status="ok",
                summary=summary or (attention or {}).get("question", "")[:200],
                session_path=str(session_path or ""),
                attention=attention,
            )
        summary = str(answer.get("text") or "").strip()
        return TurnResult(
            status="ok",
            summary=summary[:2000] or "(no output)",
            session_path=str(answer.get("session") or session_path or ""),
            attention=attention,
        )


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
        #: Human messages stashed while the worker's turn is mid-flight; the
        #: turn's tail chains them as the next prompt (spec 附录 C 决策 5).
        self._inbox: dict[str, list[str]] = {}

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
            ref = await _git_async(
                self.repo_root,
                "rev-parse",
                "--verify",
                "--quiet",
                f"refs/heads/{BRANCH_PREFIX}{name}",
            )
            return ref is not None and ref.returncode == 0

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
        )
        self.registry.records[worker] = record
        self._save()
        self._start_turn(
            record, prompt, resume=None, rollback_on_start_failure=True, use_slot=True
        )
        return record

    async def message(self, name: str, text: str) -> WorkerRecord:
        record = self._record(name)
        if record.status == "killed":
            raise RuntimeError(f"worker {name} is {record.status}")
        if record.status == "queued":
            # A human redirect supersedes the queued spawn turn: cancel it so
            # exactly one turn for this worker ever runs.
            queued = self._turns.get(name)
            if queued is not None and not queued.done():
                queued.cancel()
                await asyncio.gather(queued, return_exceptions=True)
            self._inbox.pop(name, None)
        else:
            # The worker is turn-based: while a turn is live — running, or
            # past its terminal status but still inside the milestone emit —
            # input cannot be injected, so the words wait in the inbox and
            # ride the turn chain's tail. _turns presence, not status,
            # decides: status flips to terminal before the emit finishes.
            live = self._turns.get(name)
            if record.status == "running" or live is not None:
                self._inbox.setdefault(name, []).append(text)
                return record
        if record.status == "queued":
            # A human redirect supersedes the queued spawn turn: cancel it so
            # exactly one turn for this worker ever runs.
            queued = self._turns.get(name)
            if queued is not None and not queued.done():
                queued.cancel()
                await asyncio.gather(queued, return_exceptions=True)
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
        self._inbox.pop(name, None)
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
        pending = self._inbox.pop(record.name, None)
        await self._emit_turn(record, result.summary, started, status=result.status)
        # Words that arrived during the emit window land after the pop; the
        # chain must carry them too, in arrival order.
        pending = [*(pending or []), *self._inbox.pop(record.name, [])]
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
                self._inbox.setdefault(record.name, [])[:0] = pending

    async def _rollback(self, record: WorkerRecord) -> None:
        await _git_async(self.repo_root, "worktree", "remove", "--force", record.worktree)
        shutil.rmtree(record.worktree, ignore_errors=True)
        await _git_async(self.repo_root, "branch", "-D", record.branch)
        self._inbox.pop(record.name, None)
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
        completed = _git(
            self.repo_root,
            "diff",
            "--numstat",
            f"{record.base}...{record.branch}",
        )
        if completed is None or completed.returncode != 0:
            return -1  # unknowable: refuse rather than guess
        total = 0
        for line in completed.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                for raw in parts[:2]:
                    if raw.isdigit():
                        total += int(raw)
        return total

    def diffstat(self, record: WorkerRecord) -> list[dict] | None:
        """Per-file added/deleted counts for the acceptance card's evidence.

        Binary files (numstat's ``-``) count as zero; an unknowable diff is
        None rather than a guess.
        """
        completed = _git(
            self.repo_root,
            "diff",
            "--numstat",
            f"{record.base}...{record.branch}",
        )
        if completed is None or completed.returncode != 0:
            return None
        files: list[dict] = []
        for line in completed.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                files.append(
                    {
                        "path": parts[2],
                        "adds": int(parts[0]) if parts[0].isdigit() else 0,
                        "dels": int(parts[1]) if parts[1].isdigit() else 0,
                    }
                )
        return files

    async def merge_block_reason(self, name: str) -> str | None:
        """Why an explicit merge would refuse, in board-displayable words.

        Mirrors :meth:`merge`'s preconditions without executing it; None
        means the merge would be attempted.
        """
        record = self.registry.records.get(name)
        if record is None or record.status != "ok":
            return "任务未处于待验收状态"
        current = await _git_async(self.repo_root, "branch", "--show-current")
        if current is None or current.stdout.strip() != record.base:
            return f"基分支 {record.base} 未在主检出"
        status = await asyncio.to_thread(git_status, self.repo_root)
        if status is None or not status.is_clean:
            return "主工作区有未提交更改"
        return None

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
        if auto and await asyncio.to_thread(
            self._diff_lines, record
        ) > AUTO_MERGE_MAX_DIFF_LINES:
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
        dirty = await _git_async(record.worktree, "status", "--porcelain")
        if dirty is not None and dirty.returncode == 0 and dirty.stdout.strip():
            return  # uncommitted worker output survives for inspection
        removed = await _git_async(self.repo_root, "worktree", "remove", record.worktree)
        if removed is None or removed.returncode != 0:
            shutil.rmtree(record.worktree, ignore_errors=True)

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
