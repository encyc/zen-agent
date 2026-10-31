"""Runtime permission modes: ``plan`` / ``ask`` / ``full``.

The kernel has no permission concept on purpose (pi's philosophy); the mode is
harness policy. Keeping the vocabulary in one UI-agnostic module means the CLI,
REPL and TUI all resolve and display the same three words.

- ``plan``: read-only. Mutating tools are refused; the prompt tells the model to
  investigate and propose a plan instead of executing it.
- ``ask``: mutating tools need an interactive confirmation (REPL prompt, TUI
  modal). Non-interactive runs deny, as before.
- ``full``: mutating tools run without asking. Catastrophic shell commands and
  explicit deny patterns still win.
"""

from __future__ import annotations

__all__ = [
    "DEFAULT_MODE",
    "MODES",
    "describe_modes",
    "mode_prompt_note",
    "normalize_mode",
    "resolve_mode",
]

#: The three supported modes, in escalation order.
MODES: tuple[str, str, str] = ("plan", "ask", "full")

#: The default when neither a flag nor an explicit mode says otherwise.
DEFAULT_MODE = "ask"


def normalize_mode(raw: str | None) -> str | None:
    """Return the canonical mode for ``raw``, or ``None`` when unknown."""
    if raw is None:
        return None
    candidate = raw.strip().lower()
    return candidate if candidate in MODES else None


def resolve_mode(
    *,
    explicit: str | None,
    assume_yes: bool = False,
    read_only: bool = False,
) -> str:
    """Resolve the effective mode from the explicit flag and legacy switches.

    Precedence: ``--mode`` wins; then ``--read-only`` (plan) and ``--yes``
    (full) for backward compatibility; otherwise ``ask``.
    """
    if explicit is not None:
        mode = normalize_mode(explicit)
        if mode is None:
            raise ValueError(
                f"unknown mode: {explicit!r} (choose {'|'.join(MODES)})"
            )
        return mode
    if read_only:
        return "plan"
    if assume_yes:
        return "full"
    return DEFAULT_MODE


def mode_prompt_note(mode: str) -> str:
    """Extra system-prompt paragraph for the mode, or ``""`` when none."""
    if normalize_mode(mode) != "plan":
        return ""
    return (
        "PLAN MODE: you are in read-only plan mode. Investigate with read-only "
        "tools (read_file, list_dir, glob, grep, web_search, fetch), then present a concise, "
        "step-by-step plan. Do not modify files or run mutating commands; the "
        "user switches to ask or full mode to execute the plan."
    )


def describe_modes() -> str:
    """One-line description used by ``/mode`` and the help text."""
    return (
        "modes: plan = read-only planning · ask = confirm writes/commands · "
        "full = run writes/commands without asking (dangerous commands still blocked)"
    )
