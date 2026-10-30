"""Screens for settings the user edits at runtime: provider choice and API keys.

Keys are typed here instead of being hand-written into config files, and are
persisted by :mod:`zenagent.settings` (0600). The same store feeds the model
resolver and the search providers, so a key entered once works for both.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from zenagent.cli.tui.screens.palette import PaletteItem, PaletteScreen
from zenagent.tools.web import LABELS
from zenagent.tools.web.providers import available_providers
from zenagent.tools.web.registry import default_provider

__all__ = ["ProviderKeyPicker", "SearchProviderPicker", "SecretInput"]


class SecretInput(ModalScreen[str | None]):
    """A masked one-field prompt; Enter saves, Esc cancels."""

    BINDINGS = [("escape", "cancel", "Cancel")]  # noqa: RUF012 - Textual class API

    DEFAULT_CSS = """
    SecretInput {
        align: center middle;
        background: transparent;
    }

    SecretInput #secret-panel {
        width: 72;
        height: auto;
        padding: 1 2;
        background: #141c24;
        border: round #263441;
    }

    SecretInput #secret-title {
        height: 1;
        color: #d9a86c;
        text-style: bold;
    }

    SecretInput #secret-hint {
        height: 1;
        color: #5d6f7e;
    }

    SecretInput #secret-input {
        height: 1;
        border: none;
        background: #0d1319;
        color: #d7e0e8;
    }
    """

    def __init__(self, *, title: str, hint: str, placeholder: str = "paste the key") -> None:
        super().__init__()
        self.title_text = title
        self.hint_text = hint
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="secret-panel"):
            yield Static(self.title_text, id="secret-title")
            yield Static(self.hint_text, id="secret-hint")
            yield Input(password=True, placeholder=self.placeholder, id="secret-input")

    def on_mount(self) -> None:
        self.query_one("#secret-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SearchProviderPicker(PaletteScreen[str]):
    """Pick the default search provider (``auto`` keeps the chain order)."""

    def __init__(self) -> None:
        current = default_provider() or "auto"
        items = [
            PaletteItem(
                label=f"{'●' if current == 'auto' else ' '} Auto (chain order)",
                value="auto",
                note="deepseek → tavily → brave → bing → duckduckgo",
                selected=current == "auto",
            )
        ]
        for name, available, reason in available_providers():
            items.append(
                PaletteItem(
                    label=f"{'●' if current == name else ' '} {LABELS.get(name, name)}",
                    value=name,
                    note="ready" if available else reason,
                    enabled=True,
                    selected=current == name,
                )
            )
        super().__init__(title="Search provider", items=items, placeholder="Search providers")


class ProviderKeyPicker(PaletteScreen[str]):
    """Pick a provider to set its API key for.

    ``scope`` is ``model`` (catalog providers) or ``search`` (the web search
    chain); both end in a :class:`SecretInput` for that provider's env name.
    """

    def __init__(self, providers: list[tuple[str, str, bool]], *, scope: str) -> None:
        self.scope = scope
        items = [
            PaletteItem(
                label=f"{'●' if configured else ' '} {label}",
                value=env_name,
                note="key set" if configured else f"needs {env_name}",
                enabled=True,
                selected=configured,
            )
            for label, env_name, configured in providers
        ]
        title = "Model API keys" if scope == "model" else "Search API keys"
        super().__init__(title=title, items=items, placeholder="Search providers")
