"""resolve_task_model: the task tool's per-dispatch model resolution."""

import pytest

from zenagent.app.support import resolve_configured_model, resolve_task_model
from zenagent.providers.catalog import ModelRefError, ProviderConfig


def _catalog() -> dict[str, ProviderConfig]:
    def _model(mid, default=False, provider="zai"):
        from zenagent.providers.catalog import Model
        from zenagent.providers.compat import CompatFlags
        from zenagent.types import Cost

        return Model(
            id=mid,
            provider=provider,
            api="openai-completions",
            base_url="https://api.test/v1",
            context_window=10_000,
            max_tokens=1_000,
            reasoning=False,
            default=default,
            cost=Cost(input=0.0, output=0.0),
            compat=CompatFlags(),
        )

    return {
        "zai": ProviderConfig(
            id="zai",
            name="Z.AI",
            base_url="https://api.test/v1",
            api_key_env="ZAI_API_KEY",
            adapter="openai",
            models=[_model("glm-5.3"), _model("glm-5.3-flash", default=True)],
        ),
        "deepseek": ProviderConfig(
            id="deepseek",
            name="DeepSeek",
            base_url="https://ds.test/v1",
            api_key_env="DEEPSEEK_API_KEY",
            adapter="openai",
            models=[
                _model("glm-5.3", provider="deepseek")
            ],  # duplicate id: first provider wins
        ),
    }


def test_unique_bare_id_is_refused(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "k")
    with pytest.raises(ValueError) as exc_info:
        resolve_task_model(_catalog(), "glm-5.3-flash")
    assert "names no provider" in str(exc_info.value)
    assert "zai/glm-5.3-flash" in str(exc_info.value)


def test_qualified_form_resolves_within_the_provider(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d")
    model, key = resolve_task_model(_catalog(), "deepseek/glm-5.3")
    assert model.provider == "deepseek"
    assert key == "d"


def test_unknown_provider_names_the_catalog():
    with pytest.raises(ValueError, match="unknown provider 'nope'"):
        resolve_task_model(_catalog(), "nope/glm-5.3")


def test_unknown_model_hints_at_the_qualified_form():
    with pytest.raises(ValueError, match="provider/model-id"):
        resolve_task_model(_catalog(), "nope")


def test_missing_api_key_names_the_env_var(monkeypatch):
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ZAI_API_KEY"):
        resolve_task_model(_catalog(), "zai/glm-5.3")


def test_bare_duplicate_errors_listing_qualified_forms(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "k")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d")
    with pytest.raises(ValueError) as exc_info:
        resolve_task_model(_catalog(), "glm-5.3")
    message = str(exc_info.value)
    assert "zai/glm-5.3" in message and "deepseek/glm-5.3" in message


def test_configured_model_migrates_a_unique_legacy_id(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "k")
    warnings: list[str] = []

    model = resolve_configured_model(_catalog(), "glm-5.3-flash", warnings)

    assert model.provider == "zai"
    assert warnings and "default_model" in warnings[0]
    assert "zai/glm-5.3-flash" in warnings[0]


def test_configured_model_keeps_a_qualified_ref_verbatim():
    warnings: list[str] = []

    model = resolve_configured_model(_catalog(), "deepseek/glm-5.3", warnings)

    assert model.provider == "deepseek"
    assert warnings == []


def test_configured_model_refuses_a_legacy_duplicate():
    warnings: list[str] = []

    with pytest.raises(ModelRefError) as exc_info:
        resolve_configured_model(_catalog(), "glm-5.3", warnings)

    message = str(exc_info.value)
    assert "ambiguous" in message
    assert "zai/glm-5.3" in message and "deepseek/glm-5.3" in message
    assert warnings == []
