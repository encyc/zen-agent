"""One metrics file for a runtime: a recorder per run, one line per run.

Split out of ``Runtime`` — the session owns the path and the recorder
lifecycle; the runtime only creates it at bootstrap and flushes at run end.
"""

from __future__ import annotations

from pathlib import Path

from zenagent.telemetry import MetricsRecorder, append_metrics

__all__ = ["MetricsSession"]


class MetricsSession:
    """Append-only metrics log with a fresh recorder per run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._recorder: MetricsRecorder | None = None

    def start(self, agent) -> None:
        recorder = MetricsRecorder(agent.model.id)
        self._recorder = recorder
        agent.subscribe(recorder)

    def flush(self, agent) -> None:
        """Append one metrics line for the finished run, then re-arm."""
        recorder, self._recorder = self._recorder, None
        if recorder is None or self.path is None:
            return
        append_metrics(self.path, recorder.build(agent.termination))
        self.start(agent)
