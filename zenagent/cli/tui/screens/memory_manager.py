"""Memory manager/editor modals: the TUI's ``/memory`` panel and entry editor.

Both screens write only through the shared ``MemoryStore`` (``source="ui"`` on
creates), so the journal stays complete and the model's ``memory`` tools see
the same files. Every manager action re-reads the store: the model may write
underneath an open panel, and a cached list would lie about the library.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from zenagent.agent.memory_store import MemoryEntry, MemoryStore
from zenagent.cli.tui.screens.modals import Confirm, DialogScreen
from zenagent.i18n import t

__all__ = ["MemoryEditor", "MemoryManager"]


class MemoryEditor(DialogScreen["MemoryEntry | None"]):
    """Title+body editor; the write goes through the store on save."""

    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("ctrl+s", "save", "Save"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        store: MemoryStore,
        entry: MemoryEntry | None = None,
        *,
        title_prefill: str = "",
    ) -> None:
        super().__init__()
        self.store = store
        self.entry = entry
        self.title_prefill = title_prefill

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(t("tui.dialog.memory_editor"), id="dialog-title")
            with Vertical(id="dialog-body"):
                yield Input(
                    value=self.entry.title if self.entry else self.title_prefill,
                    id="memory-title",
                )
                yield TextArea(text=self.entry.body if self.entry else "", id="memory-body")
            yield Static(t("tui.dialog.memory_editor_hint"), id="dialog-hint")

    def action_save(self) -> None:
        title = self.query_one("#memory-title", Input).value
        body = self.query_one("#memory-body", TextArea).text
        try:
            saved = (
                self.store.update_entry(
                    self.entry.id, title=title, body=body, reason="ui edit"
                )
                if self.entry
                else self.store.write_entry(
                    title=title, body=body, source="ui", reason="ui edit"
                )
            )
        except OSError as exc:
            self.notify(t("tui.app.memory_save_failed", error=exc), timeout=3)
            return
        self.dismiss(saved)

    def action_cancel(self) -> None:
        self.dismiss(None)


class MemoryManager(DialogScreen[None]):
    """Project memory panel: list + a/e/x; every action re-reads the store."""

    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("a", "add", "Add"),
        ("e", "edit", "Edit"),
        ("x", "delete", "Delete"),
        ("escape", "close", "Close"),
    ]

    def __init__(self, store: MemoryStore) -> None:
        super().__init__()
        self.store = store
        #: Entries behind the option rows; the highlight index maps into this.
        self._entries: list[MemoryEntry] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(
                t("tui.dialog.memory_manager", count=len(self.store.list_entries())),
                id="dialog-title",
            )
            with Vertical(id="dialog-body"):
                yield OptionList(id="memory-list")
            yield Static(t("tui.dialog.memory_hint"), id="dialog-hint")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#memory-list", OptionList).focus()

    def _refresh(self) -> None:
        option_list = self.query_one("#memory-list", OptionList)
        option_list.clear_options()
        self._entries = self.store.list_entries()
        if not self._entries:
            option_list.add_option(Option(t("cmdmsg.memory_empty"), disabled=True))
            return
        for entry in self._entries:
            option_list.add_option(Option(f"{entry.id}  {entry.title}  {entry.updated}"))
        # A fresh OptionList has no highlight until a navigation key moves it;
        # select the first row so e/x work the moment the panel opens.
        if option_list.highlighted is None:
            option_list.highlighted = 0
        self.query_one("#dialog-title", Static).update(
            t("tui.dialog.memory_manager", count=len(self._entries))
        )

    def _selected(self) -> MemoryEntry | None:
        option_list = self.query_one("#memory-list", OptionList)
        index = option_list.highlighted
        if index is None or index < 0 or index >= len(self._entries):
            return None
        return self._entries[index]

    @on(OptionList.OptionSelected)
    def _open_selected(self, _event: OptionList.OptionSelected) -> None:
        self.action_edit()

    def action_add(self) -> None:
        self.app.push_screen(MemoryEditor(self.store), self._saved)

    def action_edit(self) -> None:
        entry = self._selected()
        if entry is not None:
            self.app.push_screen(MemoryEditor(self.store, entry), self._saved)

    def _saved(self, saved) -> None:
        if saved is not None:
            self._refresh()

    def action_delete(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        self.app.push_screen(
            Confirm(
                t("tui.dialog.memory_delete_confirm", id=entry.id, title=entry.title)
            ),
            lambda ok: ok and self._delete(entry),
        )

    def _delete(self, entry: MemoryEntry) -> None:
        self.store.delete_entry(entry.id, reason="ui delete")
        self._refresh()

    def action_close(self) -> None:
        self.dismiss(None)
