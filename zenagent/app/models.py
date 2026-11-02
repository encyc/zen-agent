"""Model catalog browsing and switching under a Runtime."""

from __future__ import annotations

from dataclasses import dataclass

from zenagent.app.runtime import Runtime
from zenagent.app.support import api_key_for, session_id_for
from zenagent.providers.catalog import find_model

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
        current = self.runtime.agent.model.id
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
                        current=model.id == current,
                    )
                )
        return entries

    def current(self) -> str:
        return self.runtime.agent.model.id

    def switch(self, model_id: str) -> SwitchResult:
        return apply_model_switch(
            self.runtime.agent, self.runtime.catalog, self.runtime.guard, model_id
        )


def apply_model_switch(
    agent, catalog, guard, model_id: str
) -> SwitchResult:
    """Switch one agent to ``model_id`` with every side effect, frontend-agnostic.

    The REPL used to assign ``agent.model`` alone, which left the old provider's
    API key in place (a cross-provider switch then authenticated with the wrong
    key), skipped the thinking reset and the per-model session id, and could not
    resolve models the user defined in ``config.toml``. Both frontends go
    through here so a switch cannot mean two different things.
    """
    try:
        model = find_model(catalog, model_id)
    except KeyError:
        return SwitchResult(False, f"unknown model: {model_id}")
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
