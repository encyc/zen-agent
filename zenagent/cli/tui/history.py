"""Prompt history for the TUI composer.

The REPL has used ``prompt_toolkit``'s ``FileHistory`` for years; the TUI had
no history at all. This is its equivalent: one JSONL file of submitted
prompts, newest last, with Ctrl+R searching it (newest match first).
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["PromptHistory", "default_history_path"]


def default_history_path() -> Path:
    """Where the TUI persists prompts across sessions."""
    return Path.home() / ".zenagent" / "tui-history.jsonl"


class PromptHistory:
    """A bounded, crash-tolerant prompt history.

    Matters:
    - a corrupt or unreadable file is an empty history, never an exception;
    - writing is best-effort (history is not on the critical path);
    - blank prompts and repeats of the last prompt are not stored.
    """

    def __init__(self, path: Path | None = None, *, limit: int = 500) -> None:
        self.path = Path(path) if path is not None else default_history_path()
        self.limit = limit
        self._entries = self._load()

    def _load(self) -> list[str]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return []
        entries: list[str] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line).get("text")
            except (ValueError, AttributeError):
                continue  # torn/corrupt line: skip it, keep the rest
            if isinstance(value, str) and value and value != (entries[-1] if entries else None):
                entries.append(value)
        return entries[-self.limit :]

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            body = "".join(
                json.dumps({"text": text}, ensure_ascii=False) + "\n"
                for text in self._entries
            )
            self.path.write_text(body, encoding="utf-8")
        except OSError:
            pass  # history must never break a prompt

    def add(self, text: str) -> None:
        text = text.strip()
        if not text or (self._entries and self._entries[-1] == text):
            return
        self._entries.append(text)
        if len(self._entries) > self.limit:
            self._entries = self._entries[-self.limit :]
        self._save()

    def entries(self) -> list[str]:
        """All remembered prompts, oldest first."""
        return list(self._entries)

    def search(self, query: str) -> list[str]:
        """Substring match, newest first -- what Ctrl+R shows."""
        needle = query.strip().lower()
        if not needle:
            return list(reversed(self._entries))
        return [text for text in reversed(self._entries) if needle in text.lower()]
