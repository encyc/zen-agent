"""Model/provider/API-key management: switching, cycling, key entry."""

from __future__ import annotations

from zenagent.i18n import t


class ModelsMixin:
    """Model switching and provider keys. State stays on the App/ModelManager."""

    def _model_providers(self) -> list[tuple[str, str, bool]]:
        from zenagent import settings

        rows = []
        for provider_id, provider in self.runtime.catalog.items():
            configured = settings.get_api_key(provider.api_key_env) is not None
            rows.append((f"{provider_id}", provider.api_key_env, configured))
        return rows

    def _search_providers(self) -> list[tuple[str, str, bool]]:
        from zenagent import settings
        from zenagent.tools.web import LABELS
        from zenagent.tools.web.providers import PROVIDERS

        rows = []
        for name in PROVIDERS:
            env_name = f"{name.upper()}_API_KEY"
            if name in ("bing", "duckduckgo"):
                continue  # keyless
            configured = settings.get_api_key(env_name) is not None
            rows.append((LABELS.get(name, name), env_name, configured))
        return rows

    def _apply_secret(self, env_name: str, value: str) -> bool:
        """Persist one key and adopt it in the live session.

        Only a key that drives the current model reloads the runtime — the wire
        path (``ops_secrets._reload_key``) has the same guard, so both entry
        points (the model picker flow and ``/secrets``) behave identically.
        Returns whether the live session is now authenticated.
        """
        from zenagent import settings

        settings.set_api_key(env_name, value)
        provider_id = getattr(getattr(self.runtime, "model", None), "provider", None)
        provider = self.runtime.catalog.get(provider_id) if provider_id else None
        recovered = False
        if provider is not None and provider.api_key_env == env_name:
            recovered = self.runtime.reload_api_key()
            if recovered:
                self.runtime.missing_api_key = None
                self.chat.clear_last_error()
            else:
                self.runtime.agent.api_key = ""
                self.runtime.missing_api_key = env_name
        self._refresh_status()
        return recovered

    def _prompt_secret(self, env_name: str, scope: str) -> None:
        from zenagent.cli.tui.screens.secrets import SecretInput

        async def entered(value: str | None) -> None:
            if not value:
                return
            recovered = self._apply_secret(env_name, value)
            self.notify(
                t(
                    "tui.app.saved_key_ready" if recovered else "tui.app.saved_key",
                    env=env_name,
                ),
                timeout=3,
            )

        scope_key = (
            "tui.secrets.scope_model" if scope == "model" else "tui.secrets.scope_search"
        )
        self.push_screen(
            SecretInput(
                title=env_name,
                hint=t("tui.secrets.hint", scope=t(scope_key)),
            ),
            entered,
        )

    def action_pick_model(self) -> None:
        self.open_model_picker()

    def _apply_model(self, model_id: str) -> None:
        """Switch models and repeat every side effect, for every entry point.

        The picker (``alt+p`` / bare ``/model``), the argument path
        (``/model <id>``, including Enter on a value hint) and ``ctrl+p``
        cycling must not drift: the banner healing, the confirmation toast and
        the status repaint happen here once.
        """
        result = self.models.switch(model_id)
        if not result.ok:
            self.notify(result.reason or t("tui.app.switch_failed"))
            return
        if self.runtime.missing_api_key:
            # Picking a model that already has a key heals the banner.
            self.runtime.missing_api_key = None
            self.chat.clear_last_error()
        self.notify(t("tui.app.model_set", model=self.models.current()), timeout=3)
        self._refresh_status()

    def _cycle_model(self, step: int) -> None:
        entries = [entry for entry in self.models.entries() if entry.available]
        if not entries:
            return
        ids = [entry.id for entry in entries]
        current = self.chat.status.model_id
        try:
            index = (ids.index(current) + step) % len(ids)
        except ValueError:
            index = 0
        self._apply_model(ids[index])

    def action_cycle_model(self) -> None:
        self._cycle_model(1)

    def action_cycle_model_backward(self) -> None:
        self._cycle_model(-1)

    def _announce_missing_key(self) -> None:
        """A keyless first launch still opens; the banner says where to paste one."""
        env_name = self.runtime.missing_api_key
        if not env_name:
            return
        self.chat.set_last_error(
            t(
                "tui.app.no_api_key",
                model=self.runtime.model.id,
                env=env_name,
            )
        )
