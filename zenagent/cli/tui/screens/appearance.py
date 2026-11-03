"""Theme and language pickers: orchestration glue around one-choice modals."""

from __future__ import annotations

from textual.app import App

from zenagent.app.slash_args import argument_values
from zenagent.cli.tui.screens.modals import ChoicePicker
from zenagent.i18n import t


def open_theme_picker(app: App) -> None:
    async def selected(value: str | None) -> None:
        if not value:
            return
        app.set_appearance(value)
        app.notify(t("tui.app.theme_set", value=value), timeout=3)

    app.open_menu(
        "theme",
        lambda: ChoicePicker(
            t("tui.app.theme_header"),
            argument_values("/theme"),
            app._theme_spec,
        ),
        selected,
    )


def open_language_picker(app: App) -> None:
    async def selected(value: str | None) -> None:
        if not value:
            return
        app.set_language(value)
        app.notify(t("tui.app.language_set", value=value), timeout=3)

    app.open_menu(
        "language",
        lambda: ChoicePicker(
            t("tui.app.language_header"),
            argument_values("/lang"),
            app._language_spec,
        ),
        selected,
    )
