"""The settings store: atomic writes, 0600, versioning, precedence, migration."""

import json
import stat

import pytest

from zenagent import settings


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setenv("ZENAGENT_SETTINGS_PATH", str(path))
    monkeypatch.setattr(settings, "config_path", lambda: tmp_path / "config.toml")
    for name in ("TAVILY_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    settings.clear_cache()
    yield path
    settings.clear_cache()


def test_save_and_reload_roundtrip(isolated_store):
    settings.set_api_key("TAVILY_API_KEY", "tvly-123")
    settings.set_pref("search_provider", "tavily")
    settings.clear_cache()

    store = settings.load()
    assert store.api_keys["TAVILY_API_KEY"] == "tvly-123"
    assert store.prefs["search_provider"] == "tavily"
    assert settings.get_api_key("TAVILY_API_KEY") == "tvly-123"


def test_document_carries_a_version(isolated_store):
    settings.set_api_key("TAVILY_API_KEY", "tvly-123")
    document = json.loads(isolated_store.read_text())
    assert document["version"] == settings.DOCUMENT_VERSION


def test_file_is_owner_only_and_written_atomically(isolated_store):
    settings.set_api_key("TAVILY_API_KEY", "tvly-123")
    assert stat.S_IMODE(isolated_store.stat().st_mode) == 0o600
    assert stat.S_IMODE(isolated_store.parent.stat().st_mode) == 0o700
    leftovers = [p.name for p in isolated_store.parent.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], "a rename must not leave temp files behind"


def test_stored_key_wins_over_the_environment(isolated_store, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "from-env")
    assert settings.get_api_key("TAVILY_API_KEY") == "from-env"

    settings.set_api_key("TAVILY_API_KEY", "from-ui")
    assert settings.get_api_key("TAVILY_API_KEY") == "from-ui"


def test_environment_is_the_fallback_when_nothing_is_stored(isolated_store, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-only")
    assert settings.get_api_key("DEEPSEEK_API_KEY") == "env-only"


def test_empty_value_clears_the_key(isolated_store):
    settings.set_api_key("TAVILY_API_KEY", "tvly-123")
    settings.set_api_key("TAVILY_API_KEY", "   ")
    assert settings.get_api_key("TAVILY_API_KEY") is None


def test_corrupt_file_is_reported_not_silently_reset(isolated_store, monkeypatch):
    isolated_store.write_text("{not json")
    monkeypatch.setenv("TAVILY_API_KEY", "from-env")
    settings.clear_cache()

    assert settings.get_api_key("TAVILY_API_KEY") == "from-env", "env still works"
    described = settings.describe()
    assert described["problem"], "the corruption is surfaced"
    assert isolated_store.exists(), "a corrupt file is never deleted"


def test_legacy_config_toml_keys_are_imported(isolated_store, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        'default_model = "deepseek-flash"\n\nDEEPSEEK_API_KEY="sk-legacy"\n'
    )
    settings.clear_cache()
    assert settings.get_api_key("DEEPSEEK_API_KEY") == "sk-legacy"
    assert json.loads(isolated_store.read_text())["api_keys"]["DEEPSEEK_API_KEY"] == "sk-legacy"


def test_describe_never_exposes_key_material(isolated_store):
    settings.set_api_key("TAVILY_API_KEY", "tvly-super-secret")
    described = settings.describe()
    assert described["keys"] == {"TAVILY_API_KEY": "set"}
    assert "tvly-super-secret" not in json.dumps(described)
