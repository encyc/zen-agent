"""Config refusals that must be quotable in a bug report.

A config file written by a newer zen can name a table or key this build does
not know. The strict parser (deliberately: a typo must fail loudly) refuses the
whole file, so the one message a user sees has to carry everything they need:
which file, which field, what this build is, and the two ways out. The same
refusal class serves every table -- ``[limits]``, ``[task]``, ``[zen]``,
``[swarm]``, ``[tools]`` and the provider tables -- so a future knob cannot
reintroduce a dead-end error in just one of them.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["UnknownConfigField"]

from collections.abc import Iterable


def _zen_version() -> str:
    """The running version, quoted in the message so a report can cite it.

    Imported lazily: ``zenagent/__init__`` pulls in the kernel, and config
    errors can be raised while the package is still being imported.
    """
    from zenagent import __version__

    return __version__


class UnknownConfigField(ValueError):
    """A table or key this zen does not know, usually written by a newer build.

    ``field`` is the exact config path (``[limits.goal]``,
    ``[limits.goal].max_tokens``) so the message can name it for deletion and
    the reader can search for it in the file.
    """

    @classmethod
    def keys(
        cls,
        *,
        target: Path,
        prefix: str,
        unknown: Iterable[str],
        allowed: Iterable[str] | None = None,
    ) -> UnknownConfigField:
        """Refusal for unknown keys under ``prefix`` (``[zen].future``)."""
        names = sorted(unknown)
        detail = f"{prefix} has unknown keys {names}"
        if allowed is not None:
            detail += f" (allowed: {sorted(allowed)})"
        return cls(
            target=target,
            field=", ".join(f"{prefix}.{name}" for name in names),
            detail=detail,
        )

    @classmethod
    def tables(
        cls,
        *,
        target: Path,
        section: str,
        unknown: Iterable[str],
        allowed: Iterable[str],
    ) -> UnknownConfigField:
        """Refusal for unknown tables in ``section`` (``[limits.goal]``)."""
        names = sorted(unknown)
        return cls(
            target=target,
            field=", ".join(f"[{section}.{name}]" for name in names),
            detail=(
                f"[{section}] has unknown tables {names} "
                f"(allowed: {sorted(allowed)})"
            ),
        )

    def __init__(self, *, target: Path, field: str, detail: str) -> None:
        self.field = field
        self.target = str(target)
        super().__init__(
            f"config {target}: {detail}; this zen {_zen_version()} does not know "
            f"{field} and refused the file, so none of its settings were applied. "
            f"A newer zen may have written it: upgrade zen, or remove {field} "
            "from the file. If that looks right, file an issue with this message."
        )
