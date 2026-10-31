"""User-side ``/skill:<name>`` invocation: parse, render and display helpers.

A skill is normally model-facing: only ``name + description`` reach the prompt
and the model calls ``read_skill`` when it decides the skill applies. That
depends on model judgement. omp instead registers ``/skill:<name>`` as a slash
command and *injects the SKILL.md body* into a user message as soon as the user
invokes it (``src/modes/skill-command.ts`` in omp 18.14.1); the model then
cannot miss the instructions.

This module owns the pure part of that behaviour so both frontends (REPL and
TUI) share one parser and one prompt shape:

* :func:`parse_skill_invocation` — line-start form ``/skill:<name> args`` and
  mid-sentence token form ``please /skill:<name> review this``;
* :func:`render_skill_prompt` — the user-attributed message content, whose
  first line doubles as the transcript display marker;
* :func:`parse_skill_display` — recognise that marker when rendering history;
* :func:`load_skill_body` — re-read the file at invocation time, capped;
* :func:`build_skill_submission` — parse + look up + render, or an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from zenagent.frontmatter import parse_frontmatter
from zenagent.skills import MAX_SKILL_BODY_BYTES, Skill

__all__ = [
    "SKILL_COMMAND_PREFIX",
    "SKILL_SLASH_PREFIX",
    "SkillDisplay",
    "SkillInvocation",
    "SkillResolution",
    "SkillSubmission",
    "SlashToken",
    "build_skill_submission",
    "load_skill_body",
    "parse_skill_display",
    "parse_skill_invocation",
    "render_skill_prompt",
    "skill_command_name",
    "slash_token_at",
]

#: Namespace that keeps skills out of the plain command/template namespace.
SKILL_COMMAND_PREFIX = "skill:"
SKILL_SLASH_PREFIX = "/" + SKILL_COMMAND_PREFIX

#: ``[skill:<name>] <args projection>``: first line of an injected message.
_DISPLAY_HEADER = re.compile(r"^\[skill:([^\]]+)\](?:\s+(.*))?$")

#: ``/skill:<name>`` at the start of the draft (args may span lines).
_LEADING_INVOCATION = re.compile(r"^\s*/skill:(\S+)(?:\s+([\s\S]*))?$")

#: ``/skill:<name>`` as a whitespace-delimited token inside a sentence.
_EMBEDDED_INVOCATION = re.compile(r"(?<!\S)/skill:(\S+)(?=\s|$)")

#: Args in the display header are folded to one clipped line.
_HEADER_ARGS_LIMIT = 200


@dataclass(frozen=True)
class SlashToken:
    """A whitespace-delimited token that starts with ``/``, with its span."""

    start: int
    end: int
    text: str
    line_start: int


def slash_token_at(text: str, cursor: int) -> SlashToken | None:
    """The ``/`` token around ``cursor`` (end may extend past the cursor)."""
    cursor = max(0, min(cursor, len(text)))
    start = cursor
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    end = cursor
    while end < len(text) and not text[end].isspace():
        end += 1
    token = text[start:end]
    if not token.startswith("/"):
        return None
    line_start = text.rfind("\n", 0, start) + 1
    return SlashToken(start=start, end=end, text=token, line_start=line_start)


@dataclass(frozen=True)
class SkillInvocation:
    """One parsed ``/skill:<name>`` invocation."""

    name: str
    #: Everything that is not the skill token (surrounding prose for the
    #: embedded form; the text after the token for the leading form).
    args: str
    #: Character span of the ``/skill:<name>`` token in the original draft.
    start: int
    end: int
    #: The draft as submitted (trimmed) — for history/transcript labels.
    prompt: str


@dataclass(frozen=True)
class SkillSubmission:
    """A resolved invocation ready to be sent as a user message."""

    skill: Skill
    name: str
    args: str
    prompt: str


@dataclass(frozen=True)
class SkillResolution:
    """``submission`` on success, ``error`` when the name is not a skill."""

    submission: SkillSubmission | None = None
    error: str | None = None


@dataclass(frozen=True)
class SkillDisplay:
    """The marker header of an injected message, split for the UI."""

    name: str
    args: str
    #: Message body after the marker line (the injected context).
    body: str


def skill_command_name(skill: Skill) -> str:
    """The slash command name without the leading slash (``skill:<name>``)."""
    return f"{SKILL_COMMAND_PREFIX}{skill.name}"


def parse_skill_invocation(text: str) -> SkillInvocation | None:
    """Parse a line-start or embedded ``/skill:<name>`` invocation.

    A draft that starts with a *different* slash command is never treated as a
    skill invocation (``/mode /skill:x`` is the mode command's argument). Only
    the first embedded token counts; the other text becomes ``args``.
    """
    stripped = text.strip()
    if not stripped:
        return None

    leading = _LEADING_INVOCATION.match(text)
    if leading is not None and stripped.startswith(SKILL_SLASH_PREFIX):
        name = leading.group(1)
        args = (leading.group(2) or "").strip()
        start = text.find(SKILL_SLASH_PREFIX)
        end = start + len(SKILL_SLASH_PREFIX) + len(name)
        return SkillInvocation(
            name=name, args=args, start=start, end=end, prompt=stripped
        )

    if stripped.startswith("/"):
        return None  # another slash command owns the draft

    match = _EMBEDDED_INVOCATION.search(text)
    if match is None:
        return None
    name = match.group(1)
    start = match.start()
    end = match.end()
    before = text[:start].strip()
    after = text[end:].strip()
    args = " ".join(part for part in (before, after) if part)
    return SkillInvocation(
        name=name, args=args, start=start, end=end, prompt=stripped
    )


def load_skill_body(skill: Skill) -> tuple[str, bool]:
    """Read a skill's instruction body at invocation time.

    Re-reading matters: the user may have edited SKILL.md since discovery. A
    read/parse failure falls back to the discovered body; either source is
    capped at :data:`MAX_SKILL_BODY_BYTES` because it goes straight into the
    context window.
    """
    body = skill.body
    try:
        raw = Path(skill.path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        pass
    else:
        _meta, parsed, problem = parse_frontmatter(raw)
        if problem is None:
            body = parsed
    return _truncate_bytes(body.strip(), MAX_SKILL_BODY_BYTES)


def _truncate_bytes(text: str, limit: int) -> tuple[str, bool]:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text, False
    return data[:limit].decode("utf-8", "ignore"), True


def _project_args(args: str) -> str:
    """One clipped line for the display header (the full args follow later)."""
    projected = " ".join(args.split())
    if len(projected) > _HEADER_ARGS_LIMIT:
        projected = projected[: _HEADER_ARGS_LIMIT - 1].rstrip() + "…"
    return projected


def render_skill_prompt(
    skill: Skill, body: str, args: str, *, truncated: bool = False
) -> str:
    """The user-attributed message that carries a skill invocation.

    The first line is the transcript marker; the rest is the omp-shaped
    payload: an explicit "follow these instructions" preamble, the body, the
    skill directory (so relative paths in the skill resolve), and the user's
    arguments.
    """
    name = skill.name
    base_dir = Path(skill.path).parent.resolve()
    projected_args = _project_args(args)
    header = f"[skill:{name}]"
    if projected_args:
        header += f" {projected_args}"

    parts = [
        header,
        "",
        (
            f'[IMPORTANT: User invoked the "{name}" skill; follow its '
            "instructions. Full skill below.]"
        ),
        "",
        body.rstrip(),
    ]
    if truncated:
        parts += ["", f"[skill body truncated at {MAX_SKILL_BODY_BYTES} bytes]"]
    parts += [
        "",
        "---",
        f"[Skill directory: {base_dir}]",
        (
            "Resolve relative paths in this skill (e.g. scripts/foo.py, "
            "templates/config.yaml) against this absolute directory; read "
            "referenced assets and templates, and run scripts with the bash "
            "tool when the skill instructions call for it."
        ),
    ]
    args = args.strip()
    if args:
        parts += ["", f"User: {args}"]
    return "\n".join(parts) + "\n"


def parse_skill_display(text: str) -> SkillDisplay | None:
    """Recognise an injected skill message for transcript rendering."""
    first, separator, rest = text.partition("\n")
    match = _DISPLAY_HEADER.match(first.strip())
    if match is None:
        return None
    return SkillDisplay(
        name=match.group(1),
        args=(match.group(2) or "").strip(),
        body=rest.lstrip("\n") if separator else "",
    )


def build_skill_submission(
    text: str,
    skills: list[Skill],
    *,
    reserved_names: set[str] | frozenset[str] = frozenset(),
) -> SkillResolution | None:
    """Resolve ``text`` into an injected skill prompt.

    Returns ``None`` when the draft is not a skill invocation, ``error`` when it
    names an unknown skill, and ``submission`` when it should be injected.
    ``reserved_names`` protects a command/template that happens to own the
    full ``skill:<name>`` name (normally none can, thanks to the namespace).
    """
    invocation = parse_skill_invocation(text)
    if invocation is None:
        return None
    if f"{SKILL_COMMAND_PREFIX}{invocation.name}" in reserved_names:
        return None

    skill = next(
        (candidate for candidate in skills if candidate.name == invocation.name),
        None,
    )
    if skill is None:
        available = sorted(candidate.name for candidate in skills)
        listed = ", ".join(available[:12]) or "(none)"
        if len(available) > 12:
            listed += f", … (+{len(available) - 12})"
        return SkillResolution(
            error=f"unknown skill: {invocation.name} (available: {listed})"
        )

    body, truncated = load_skill_body(skill)
    prompt = render_skill_prompt(
        skill, body, invocation.args, truncated=truncated
    )
    return SkillResolution(
        submission=SkillSubmission(
            skill=skill,
            name=skill.name,
            args=invocation.args,
            prompt=prompt,
        )
    )
