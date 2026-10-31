"""English catalog — the authoritative text for every user-facing string.

Keep the values byte-identical to the literals they replace: the default locale
is English, so the existing test suite doubles as the regression gate for this
file. Every key must also exist in :mod:`zenagent.i18n.zh`.
"""

from __future__ import annotations

__all__ = ["CATALOG"]

CATALOG: dict[str, str] = {
    # mechanism smoke tests
    "test.greeting": "hello {name}",
    "test.apples.one": "{n} apple",
    "test.apples.other": "{n} apples",
    # TUI: status band
    "tui.status.cache": "cache {pct}%",
    "tui.status.context": "context {pct}%",
    "tui.status.tokens": "{input} in / {output} out",
    "tui.status.token_rate": "{rate} tok/s",
    "tui.status.queued.one": "{n} queued",
    "tui.status.queued.other": "{n} queued",
    "tui.status.steering.one": "{n} steering",
    "tui.status.steering.other": "{n} steering",
    # TUI: welcome / work chrome
    "tui.welcome.subtitle": "v{version} · python agent loop",
    "tui.welcome.hints": "/ commands  ·  ctrl+b info  ·  ctrl+d quit",
    "tui.work.jobs.one": "{n} job",
    "tui.work.jobs.other": "{n} jobs",
    "tui.work.task": "1 task",
    "tui.work.working": "working",
    "tui.new_output.label": "new output",
    # TUI: transcript rows
    "tui.rows.thinking": "thinking",
    "tui.rows.thinking_active": "thinking…",
    "tui.rows.done": "done",
    "tui.rows.in": "in {n}",
    "tui.rows.out": "out {n}",
    "tui.rows.cache": "cache {n}",
    # TUI: todo HUD
    "tui.todo.title": "todo",
    "tui.todo.closed": "{closed}/{total} closed",
    "tui.todo.done": "all done",
    "tui.todo.blocked.one": "{n} blocked",
    "tui.todo.blocked.other": "{n} blocked",
    "tui.todo.tasks": "Tasks",
    # TUI: subagent banner
    "tui.subagent.label": "subagent: ",
    "tui.subagent.readonly": "read-only · esc to return",
    "tui.subagent.done": "done",
    # TUI: work panel sections
    "tui.sidebar.session": "Session",
    "tui.sidebar.model": "Model",
    "tui.sidebar.runtime": "Runtime",
    "tui.sidebar.context": "Context",
    "tui.sidebar.todo": "Todo",
    "tui.sidebar.subagent": "Subagent",
    "tui.sidebar.jobs": "Jobs",
    "tui.sidebar.messages.one": "{n} message",
    "tui.sidebar.messages.other": "{n} messages",
    "tui.sidebar.ctx": "ctx {used}/{window}",
    "tui.sidebar.ctx_total": "ctx {pct}% · {mark}{used}/{window}",
    "tui.sidebar.spend": "spend ${cost}",
    "tui.sidebar.state_running": "running",
    "tui.sidebar.state_idle": "idle",
    "tui.sidebar.queue": "queued {queued} · steer {steer}",
    "tui.sidebar.models.one": "{n} model",
    "tui.sidebar.models.other": "{n} models",
    "tui.sidebar.short.ctx": "ctx",
    "tui.sidebar.short.system": "sys",
    "tui.sidebar.short.agents": "AGENTS",
    "tui.sidebar.short.skills": "skills",
    "tui.sidebar.short.extensions": "ext",
    "tui.sidebar.short.plan": "plan",
    "tui.sidebar.short.mode": "mode",
    "tui.sidebar.short.tools": "tools",
    "tui.sidebar.short.session": "session",
    "tui.sidebar.short.other": "other",
    # context slice labels (sidebar summary + breakdown overlay)
    "tui.context.system": "system prompt",
    "tui.context.agents": "AGENTS.md",
    "tui.context.skills": "skills",
    "tui.context.extensions": "extensions",
    "tui.context.plan": "plan note",
    "tui.context.mode": "mode note",
    "tui.context.tools": "tool schemas",
    "tui.context.session": "session",
    "tui.context.other": "other (generation/overhead)",
    "tui.ctx.free": "free space",
    "tui.ctx.note_window_measured": "percentages are shares of the model window; slices are estimates (≈), the used total is provider-measured",
    "tui.ctx.note_window_estimated": "percentages are shares of the model window; slices and total are estimated (≈) — no provider usage yet",
}
