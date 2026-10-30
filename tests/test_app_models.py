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

    result = models.switch(entry.id)
    assert result.ok is False and "no API key" in (result.reason or "")

    ok = models.switch("deepseek-flash")
    assert ok.ok is True
    assert runtime.agent.model.id == "deepseek-flash"
    assert runtime.guard.context_window == runtime.agent.model.context_window
