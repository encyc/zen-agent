"""The one palette skeleton every picker reuses.

omp has 37 overlays but only three skeletons; the most common is a
bottom-anchored panel with a title, an optional search row, a list and a dim
footer hint (``05-overlays-and-surfaces.md`` §2). Keeping one class means the
model picker, the session picker and any future list look identical, and the
sizing rules (<100 columns -> full width) live in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Generic, TypeVar

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

__all__ = ["PaletteItem", "PaletteScreen"]

T = TypeVar("T")


_NAV_KEYS = {"up", "down", "pageup", "pagedown", "home", "end", "enter"}


class PaletteSearch(Input):
    """Search box that hands navigation keys to the list beneath it.

    When the box is empty, a screen's :attr:`PaletteScreen.ACTION_KEYS` also get
    a chance first: that is how a picker offers single-letter actions without
    swallowing letters the user needs for searching.
    """

    async def _on_key(self, event: events.Key) -> None:
        if event.key in _NAV_KEYS:
            event.stop()
            self.screen.navigate_list(event.key)
            return
        shortcuts = getattr(self.screen, "ACTION_KEYS", {})
        if shortcuts and not self.value and event.key in shortcuts:
            event.stop()
            getattr(self.screen, f"action_{shortcuts[event.key]}")()
            return
        await super()._on_key(event)


@dataclass(frozen=True)
class PaletteItem(Generic[T]):
    """One row: a label, the value it carries, an optional right-aligned note."""

    label: str
    value: T
    note: str = ""
    enabled: bool = True
    selected: bool = False


class PaletteScreen(ModalScreen[T | None], Generic[T]):
    """A bottom-anchored list panel with search and a footer hint."""

    BINDINGS = [("escape", "cancel", "Cancel")]  # noqa: RUF012 - Textual class API

    #: Empty-search single-key actions, e.g. ``{"n": "new"}``; a subclass that
    #: declares these must provide the matching ``action_<name>`` methods.
    ACTION_KEYS: ClassVar[dict[str, str]] = {}

    DEFAULT_CSS = """
    PaletteScreen {
        align: center bottom;
        background: transparent;
    }

    PaletteScreen #palette {
        width: 100%;
        height: auto;
        max-height: 70%;
        padding: 1 2;
        background: #141c24;
        border-top: solid #263441;
    }

    PaletteScreen #palette-title {
        height: 1;
        color: #d9a86c;
        text-style: bold;
    }

    PaletteScreen #palette-search {
        height: 1;
        margin: 0 0 1 0;
        padding: 0;
        border: none;
        background: #141c24;
        color: #d7e0e8;
    }

    PaletteScreen #palette-list {
        height: auto;
        max-height: 20;
        background: #141c24;
        border: none;
        padding: 0;
        scrollbar-size-vertical: 1;
    }

    PaletteScreen #palette-list > .option-list--option-highlighted {
        background: #263441;
        color: #d7e0e8;
    }

    PaletteScreen #palette-footer {
        height: 1;
        margin: 1 0 0 0;
        color: #5d6f7e;
    }
    """

    def __init__(
        self,
        *,
        title: str,
        items: list[PaletteItem[T]],
        placeholder: str = "Search",
        footer: str = "↑↓ move   enter select   esc cancel",
    ) -> None:
        super().__init__()
        self.title_text = title
        self.items = items
        self.placeholder = placeholder
        self.footer_text = footer
        self._filtered: list[PaletteItem[T]] = list(items)

    # -- composition --------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="palette"):
            yield Static(self.title_text, id="palette-title")
            yield PaletteSearch(placeholder=self.placeholder, id="palette-search")
            yield OptionList(id="palette-list")
            yield Static(self.footer_text, id="palette-footer")

    def on_mount(self) -> None:
        self._refresh_list()
        self.query_one("#palette-search", Input).focus()

    # -- filtering ----------------------------------------------------------

    def _option(self, item: PaletteItem[T]) -> Option:
        label = f"{item.label}"
        if item.note:
            label = f"{label}   {item.note}"
        return Option(label, id=str(self._filtered.index(item)), disabled=not item.enabled)

    def _refresh_list(self) -> None:
        option_list = self.query_one("#palette-list", OptionList)
        option_list.clear_options()
        if not self._filtered:
            option_list.add_option(Option("no matches", disabled=True))
            return
        option_list.add_options([self._option(item) for item in self._filtered])
        # Preselect the active entry (omp highlights the current model/session).
        option_list.highlighted = next(
            (index for index, item in enumerate(self._filtered) if item.selected), 0
        )

    def filter(self, query: str) -> None:
        needle = query.strip().lower()
        if not needle:
            self._filtered = list(self.items)
        else:
            self._filtered = [
                item
                for item in self.items
                if needle in item.label.lower() or needle in item.note.lower()
            ]
        self._refresh_list()

    def navigate_list(self, key: str) -> None:
        """Arrow/enter handling for the focused search box."""
        option_list = self.query_one("#palette-list", OptionList)
        if key == "enter":
            option_list.action_select()
            return
        actions = {
            "up": option_list.action_cursor_up,
            "down": option_list.action_cursor_down,
            "pageup": option_list.action_page_up,
            "pagedown": option_list.action_page_down,
            "home": option_list.action_first,
            "end": option_list.action_last,
        }
        actions[key]()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.filter(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        try:
            item = self._filtered[int(event.option.id or 0)]
        except (IndexError, ValueError):
            return
        if not item.enabled:
            return
        self.dismiss(item.value)

    def action_cancel(self) -> None:
        self.dismiss(None)
