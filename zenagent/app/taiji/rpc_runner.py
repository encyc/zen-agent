"""The worker RPC runner: one ``zen rpc`` subprocess per worker turn.

Split out of the supervisor (2026-11-08 review) — running a turn is its
own concern, not supervision.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class TurnResult:
    status: str
    summary: str
    session_path: str
    #: A worker question waiting on a person (from an ``ask_request`` event).
    attention: dict | None = None


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
