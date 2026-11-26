"""ModelManager: availability annotation and switch side effects."""

from zenagent.app.models import ModelManager


async def test_switch_reports_missing_key_and_syncs_context(
    runtime_without_glm_key,
):
    runtime = runtime_without_glm_key
    models = ModelManager(runtime)

    entry = next(e for e in models.entries() if e.provider == "zai-coding-cn")
    assert entry.available is False
    assert "no API key" in (entry.reason or "")

    result = models.switch(f"{entry.provider}/{entry.id}")
    assert result.ok is False and "no API key" in (result.reason or "")

    ok = models.switch("deepseek-flash")
    assert ok.ok is True
    assert runtime.agent.model.id == "deepseek-flash"
    assert runtime.guard.context_window == runtime.agent.model.context_window


def test_apply_model_switch_updates_every_side_effect(monkeypatch):
    from dataclasses import replace

    from zenagent.agent import Agent
    from zenagent.app.models import apply_model_switch
    from zenagent.app.support import session_id_for
    from zenagent.providers.catalog import load_catalog

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deep-key")
    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "glm-key")
    catalog = load_catalog()
    deepseek = catalog["deepseek"]
    custom = replace(deepseek.models[0], id="custom-x", context_window=4242)
    deepseek.models.append(custom)

    agent = Agent(catalog["zai-coding-cn"].models[0], "s", api_key="glm-key")
    guard = type("Guard", (), {"context_window": agent.model.context_window})()

    result = apply_model_switch(agent, catalog, guard, "custom-x")

    assert result.ok is True
    assert agent.model.id == "custom-x"
    assert agent.api_key == "deep-key"
    assert agent.session_id == session_id_for(custom)
    assert guard.context_window == 4242


async def test_switch_qualified_id_lands_on_that_provider(runtime_factory, monkeypatch):
    """The picker's row provider is part of the switch, not display only."""
    monkeypatch.setenv("OPENCODE_API_KEY", "oc-key")
    runtime = await runtime_factory()
    models = ModelManager(runtime)

    result = models.switch("opencode-go/deepseek-v4-pro")

    assert result.ok is True, result.reason
    assert runtime.agent.model.provider == "opencode-go"
    assert runtime.agent.model.base_url.startswith("https://opencode.ai/")
    assert runtime.agent.api_key == "oc-key"
    # runtime.model drives reload_api_key()/settings: a switch must adopt it.
    assert runtime.model is runtime.agent.model
    # Only the picked provider's row is current; the mirrored id is not.
    current = [(e.provider, e.id) for e in models.entries() if e.current]
    assert current == [("opencode-go", "deepseek-v4-pro")]


async def test_ambiguous_bare_id_is_refused_not_guessed(runtime_factory, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "oc-key")
    runtime = await runtime_factory()
    models = ModelManager(runtime)
    before = runtime.agent.model

    result = models.switch("deepseek-v4-pro")

    assert result.ok is False
    reason = result.reason or ""
    assert "ambiguous" in reason
    assert "deepseek/deepseek-v4-pro" in reason
    assert "opencode-go/deepseek-v4-pro" in reason
    assert runtime.agent.model is before
    assert runtime.model is before
