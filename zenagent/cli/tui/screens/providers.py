"""Provider/key pickers: orchestration glue over the secrets screens."""

from __future__ import annotations

from textual.app import App

from zenagent.cli.tui.screens.modals import ModelPicker
from zenagent.cli.tui.screens.secrets import (
    ProviderKeyPicker,
    SearchProviderPicker,
)
from zenagent.i18n import t


def open_model_picker(app: App) -> None:
    async def selected(model_id: str | None) -> None:
        if model_id:
            app._apply_model(model_id)

    app.open_menu("model", lambda: ModelPicker(app.models.entries()), selected)


def open_model_keys(app: App) -> None:
    async def selected(env_name: str | None) -> None:
        if env_name:
            app._prompt_secret(env_name, "model")

    app.open_menu(
        "model-keys",
        lambda: ProviderKeyPicker(app._model_providers(), scope="model"),
        selected,
    )


def open_search_keys(app: App) -> None:
    async def selected(env_name: str | None) -> None:
        if env_name:
            app._prompt_secret(env_name, "search")

    app.open_menu(
        "search-keys",
        lambda: ProviderKeyPicker(app._search_providers(), scope="search"),
        selected,
    )


def open_search_provider(app: App) -> None:
    async def selected(name: str | None) -> None:
        if not name:
            return
        from zenagent import settings

        settings.set_pref("search_provider", None if name == "auto" else name)
        app.notify(t("tui.app.search_provider_set", name=name), timeout=3)
        app._refresh_status()

    app.open_menu("search-provider", SearchProviderPicker, selected)
