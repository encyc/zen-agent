"""Runtime permission modes: ``plan`` / ``ask`` / ``zen``.

The kernel has no permission concept on purpose (pi's philosophy); the mode is
harness policy. Keeping the vocabulary in one UI-agnostic module means the CLI,
REPL and TUI all resolve and display the same three words.

- ``plan``: read-only. Mutating tools are refused; the prompt tells the model to
  investigate and propose a plan instead of executing it.
- ``ask``: mutating tools need an interactive confirmation (REPL prompt, TUI
  modal). Non-interactive runs deny, as before. The three confirmable risk
  tiers (destructive command, protected path, outside workspace) confirm here
  too, and catastrophic shell commands / explicit deny patterns still win.
- ``zen``: no confirmations at all. The risk tiers run instead of asking,
  headless runs included. Only policy stays: catastrophic shell commands still
  refuse (``--allow-dangerous`` lifts that too) and explicit deny patterns win.
"""

from __future__ import annotations

from zenagent.i18n import t
from zenagent.prompts.registry import read_block

__all__ = [
    "DEFAULT_MODE",
    "MODES",
    "describe_modes",
    "mode_prompt_note",
    "normalize_mode",
    "resolve_mode",
]

#: The three supported modes, in escalation order.
MODES: tuple[str, str, str] = ("plan", "ask", "zen")

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
    (zen) for backward compatibility; otherwise ``ask``.
    """
    if explicit is not None:
        mode = normalize_mode(explicit)
        if mode is None:
            raise ValueError(t("mode.unknown", mode=explicit))
        return mode
    if read_only:
        return "plan"
    if assume_yes:
        return "zen"
    return DEFAULT_MODE


def mode_prompt_note(mode: str) -> str:
    """Extra system-prompt paragraph for the mode, or ``""`` when none."""
    normalized = normalize_mode(mode)
    if normalized == "plan":
        return read_block("plan_mode")
    if normalized == "zen":
        return read_block("zen_mode")
    return ""


def describe_modes() -> str:
    """One-line description used by ``/mode`` and the help text."""
    return t("mode.describe")
