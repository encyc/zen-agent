"""Model catalog browsing and switching under a Runtime."""

from __future__ import annotations

from dataclasses import dataclass

from zenagent.app.runtime import Runtime
from zenagent.app.support import api_key_for, session_id_for
from zenagent.providers.catalog import ModelRefError, model_ref, resolve_model_ref

__all__ = ["ModelEntry", "ModelManager", "SwitchResult"]


@dataclass
class ModelEntry:
    id: str
    provider: str
    context_window: int
    max_tokens: int
    reasoning: bool
    supports_vision: bool
    available: bool
    reason: str | None = None
    current: bool = False


@dataclass
class SwitchResult:
    ok: bool
    reason: str | None = None


class ModelManager:
    """Lists catalog models, annotated with key availability, and switches."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    def entries(self) -> list[ModelEntry]:
        current = self.runtime.agent.model
        entries: list[ModelEntry] = []
        for provider in self.runtime.catalog.values():
            for model in provider.models:
                available = api_key_for(self.runtime.catalog, model) is not None
                reason = (
                    None
                    if available
                    else f"no API key for {model.provider}/{model.id}"
                )
                entries.append(
                    ModelEntry(
                        id=model.id,
                        provider=model.provider,
                        context_window=model.context_window,
                        max_tokens=model.max_tokens,
                        reasoning=model.reasoning,
                        supports_vision=model.supports_vision,
                        available=available,
                        reason=reason,
                        # Both rows of a duplicated id must not look current:
                        # the provider is part of the identity now.
                        current=(
                            model.provider == current.provider
                            and model.id == current.id
                        ),
                    )
                )
        return entries

    def current(self) -> str:
        return self.runtime.agent.model.id

    def current_ref(self) -> str:
        """The live model as ``provider/model-id`` (what a selection stores)."""
        return model_ref(self.runtime.agent.model)

    def switch(self, model_id: str) -> SwitchResult:
        result = apply_model_switch(
            self.runtime.agent, self.runtime.catalog, self.runtime.guard, model_id
        )
        if result.ok:
            # runtime.model is what reload_api_key() and the settings' "current
            # provider" read; leaving it at the bootstrap model re-authenticated
            # the wrong provider after a switch.
            self.runtime.model = self.runtime.agent.model
        return result


def apply_model_switch(
    agent, catalog, guard, model_id: str
) -> SwitchResult:
    """Switch one agent to ``model_id`` with every side effect, frontend-agnostic.

    ``model_id`` must be a ``provider/model-id`` reference. Resolution goes
    through :func:`resolve_model_ref`, which refuses a bare id outright (with
    the qualified candidates as a hint) instead of letting catalog order decide
    the provider — which is how "I picked opencode-go" used to become
    ``api.deepseek.com``.

    The REPL used to assign ``agent.model`` alone, which left the old provider's
    API key in place (a cross-provider switch then authenticated with the wrong
    key), skipped the thinking reset and the per-model session id, and could not
    resolve models the user defined in ``config.toml``. Both frontends go
    through here so a switch cannot mean two different things.
    """
    try:
        model = resolve_model_ref(catalog, model_id)
    except ModelRefError as exc:
        return SwitchResult(False, str(exc))
    api_key = api_key_for(catalog, model)
    if not api_key:
        return SwitchResult(
            False, f"no API key for {model.provider}/{model.id}"
        )
    agent.model = model
    agent.api_key = api_key
    agent.thinking = (
        "off" if not model.reasoning else (agent.thinking or "high")
    )
    agent.session_id = session_id_for(model)
    if guard is not None:
        guard.context_window = model.context_window
    return SwitchResult(True)
