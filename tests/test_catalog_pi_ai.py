"""The pi-ai catalog expansion must load, stay wired, and not overwrite hand-tuned entries."""

import pytest

from zenagent.providers.catalog import load_catalog

NEW_PROVIDERS = {
    "openai": ("openai-responses", 42),
    "zai": ("openai-completions", 5),
    "kimi": ("anthropic-messages", 2),
    "moonshotai": ("openai-completions", 7),
    "moonshotai-cn": ("openai-completions", 7),
    "minimax": ("anthropic-messages", 2),
    "minimax-cn": ("anthropic-messages", 2),
    "google": ("google-generative-ai", 27),
    "xai": ("openai-completions", 25),
    "xiaomi": ("anthropic-messages", 5),
    "xiaomi-token-plan-cn": ("anthropic-messages", 5),
    "xiaomi-token-plan-ams": ("anthropic-messages", 5),
    "xiaomi-token-plan-sgp": ("anthropic-messages", 5),
    "openrouter": ("openai-completions", 82),
}


@pytest.mark.parametrize("provider_id", sorted(NEW_PROVIDERS))
def test_new_provider_loads_with_expected_adapter_and_model_count(provider_id):
    adapter, count = NEW_PROVIDERS[provider_id]
    provider = load_catalog()[provider_id]
    assert provider.adapter == adapter, (
        f"{provider_id}: expected adapter {adapter!r}, got {provider.adapter!r}"
    )
    assert len(provider.models) == count, (
        f"{provider_id}: expected {count} models, got {len(provider.models)}. "
        "If the pi-ai source was re-synced, update this table (and the provider's "
        "catalog entry) deliberately -- do not just match the new number."
    )


def test_every_catalog_model_uses_a_registered_adapter():
    from zenagent.providers.registry import _REGISTRY

    for provider in load_catalog().values():
        for model in provider.models:
            assert model.api in _REGISTRY, (
                f"{provider.id}/{model.id} uses unregistered {model.api}"
            )


def test_no_catalog_model_id_contains_a_colon():
    """A colon in a key would be split by the catalog tokenizer's partition(':').

    The tokenizer partitions every line on the first ':', so an id like
    'anthropic/claude-3.7-sonnet:thinking' silently breaks the entry -- and
    the error it eventually raises ("unexpected indentation") points nowhere
    near the real cause. Pi-ai's openrouter catalog carries such ids; this
    test is why the curated subset excludes them.
    """
    for provider in load_catalog().values():
        for model in provider.models:
            assert ":" not in model.id, f"{provider.id}/{model.id}"


def test_hand_tuned_providers_are_untouched():
    """zai-coding-cn's explicit zai dialect must survive catalog regeneration."""
    glm = load_catalog()["zai-coding-cn"].models[0]
    assert glm.id == "glm-5.3"
    assert glm.compat.thinking_format == "zai"
    assert glm.compat.zai_tool_stream is True
    assert glm.compat.supports_developer_role is False
    assert glm.compat.supports_reasoning_effort is True


def test_anthropic_style_providers_keep_base_urls_without_v1():
    """The anthropic adapter appends /v1/messages; a base_url ending in /v1 doubles it."""
    catalog = load_catalog()
    for provider_id in ("kimi", "xiaomi", "minimax"):
        assert not catalog[provider_id].base_url.endswith("/v1"), provider_id


def test_synced_providers_declare_an_api_key_env():
    """Every bundled provider must name the env var it reads (no OAuth in the kernel)."""
    for provider in load_catalog().values():
        assert provider.api_key_env, provider.id
