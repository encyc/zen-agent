"""resolve_task_model: the task tool's per-dispatch model resolution."""

import pytest

from zenagent.app.support import resolve_task_model
from zenagent.providers.catalog import ProviderConfig


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


def test_bare_id_resolves_to_first_provider(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "k")
    model, key = resolve_task_model(_catalog(), "glm-5.3")
    assert model.provider == "zai"  # first provider defining the id wins
    assert key == "k"


def test_qualified_form_resolves_within_the_provider(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d")
    model, key = resolve_task_model(_catalog(), "deepseek/glm-5.3")
    assert model.provider == "deepseek"
    assert key == "d"


def test_unknown_provider_names_the_catalog():
    with pytest.raises(ValueError, match="unknown provider 'nope'"):
        resolve_task_model(_catalog(), "nope/glm-5.3")


def test_unknown_model_hints_at_the_qualified_form():
    with pytest.raises(ValueError, match="provider/model form"):
        resolve_task_model(_catalog(), "nope")


def test_missing_api_key_names_the_env_var(monkeypatch):
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ZAI_API_KEY"):
        resolve_task_model(_catalog(), "glm-5.3")
