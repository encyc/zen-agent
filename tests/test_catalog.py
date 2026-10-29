"""Tests for compat flags, base_url sniffing, and the packaged model catalog."""

import pytest

from pyagent.providers.catalog import _build_catalog, find_model, load_catalog
from pyagent.providers.compat import CompatFlags, sniff_compat
from pyagent.types import Cost


def _models_by_id(catalog, provider_id):
    return {model.id: model for model in catalog[provider_id].models}


# ---------------------------------------------------------------- compat flags


def test_compat_flags_all_defaults():
    flags = CompatFlags()
    assert flags.thinking_format is None
    assert flags.max_tokens_field == "max_tokens"
    assert flags.supports_developer_role is True
    assert flags.supports_reasoning_effort is False
    assert flags.zai_tool_stream is False
    assert flags.requires_reasoning_content_on_assistant is False
    assert flags.supports_usage_in_streaming is True


def test_sniff_compat_unknown_host_returns_all_defaults():
    assert sniff_compat("https://api.unknown.dev/v1") == CompatFlags()
    assert sniff_compat("https://example.org") == CompatFlags()


def test_sniff_compat_bigmodel_host_returns_zai_dialect():
    flags = sniff_compat("https://open.bigmodel.cn/api/coding/paas/v4")
    assert flags.thinking_format == "zai"
    assert flags.zai_tool_stream is True
    assert flags.max_tokens_field == "max_tokens"
    assert flags.supports_developer_role is False
    # unlisted flags keep their dataclass defaults
    assert flags.supports_reasoning_effort is False
    assert flags.supports_usage_in_streaming is True


def test_sniff_compat_deepseek_host_returns_deepseek_dialect():
    flags = sniff_compat("https://api.deepseek.com/chat/completions")
    assert flags.thinking_format == "deepseek"
    assert flags.requires_reasoning_content_on_assistant is True
    assert flags.max_tokens_field == "max_tokens"
    assert flags.supports_developer_role is False
    assert flags.zai_tool_stream is False


# ------------------------------------------------------------- load_catalog()


def test_load_catalog_returns_three_providers_in_file_order():
    assert list(load_catalog()) == ["zai-coding-cn", "deepseek", "opencode-go"]


def test_find_model_glm_53_is_zai_dialect_and_first_provider_wins():
    # zai-coding-cn precedes opencode-go in the file, so the duplicate
    # glm-5.3 id under opencode-go must lose.
    model = find_model(load_catalog(), "glm-5.3")
    assert model.provider == "zai-coding-cn"
    assert model.id == "glm-5.3"
    assert model.api == "openai-completions"
    assert model.base_url == "https://open.bigmodel.cn/api/coding/paas/v4"
    assert model.context_window == 1000000
    assert model.max_tokens == 131072
    assert model.reasoning is True
    assert model.cost == Cost(input=0.0, output=0.0, total=0.0)
    assert model.compat.thinking_format == "zai"
    assert model.compat.zai_tool_stream is True
    assert model.compat.supports_developer_role is False
    assert model.compat.supports_reasoning_effort is True


def test_zai_flash_shares_full_glm_53_compat_block():
    catalog = load_catalog()
    models = _models_by_id(catalog, "zai-coding-cn")
    flash = models["glm-5.3-flash"]
    assert flash.compat == models["glm-5.3"].compat  # written out in full, no anchors
    assert flash.api == "openai-completions"  # inherited from the provider
    assert flash.cost == Cost(input=0.0, output=0.0)
    assert flash.default is False


def test_default_flag_true_only_on_zai_glm_53():
    catalog = load_catalog()
    assert _models_by_id(catalog, "zai-coding-cn")["glm-5.3"].default is True
    assert find_model(catalog, "deepseek-chat").default is False
    assert all(m.default is False for m in catalog["opencode-go"].models)


def test_find_model_unknown_id_raises_key_error_naming_model():
    with pytest.raises(KeyError) as excinfo:
        find_model(load_catalog(), "gpt-99")
    assert "gpt-99" in str(excinfo.value)


# ---------------------------------------------------------------- deepseek


def test_deepseek_provider_pricing_and_compat():
    catalog = load_catalog()
    provider = catalog["deepseek"]
    assert provider.name == "DeepSeek"
    assert provider.base_url == "https://api.deepseek.com"
    assert provider.api_key_env == "DEEPSEEK_API_KEY"
    assert provider.adapter == "openai-completions"

    chat = find_model(catalog, "deepseek-chat")
    assert chat.context_window == 128000
    assert chat.max_tokens == 8192
    assert chat.reasoning is False
    assert chat.cost == Cost(input=0.27, output=1.10, total=0.0)
    assert chat.compat.thinking_format is None
    assert chat.compat.max_tokens_field == "max_tokens"
    assert chat.compat.supports_developer_role is False

    reasoner = find_model(catalog, "deepseek-reasoner")
    assert reasoner.max_tokens == 65536
    assert reasoner.reasoning is True
    assert reasoner.cost == Cost(input=0.55, output=2.19, total=0.0)
    assert reasoner.compat.thinking_format == "deepseek"
    assert reasoner.compat.requires_reasoning_content_on_assistant is True
    assert reasoner.compat.supports_developer_role is False


# ------------------------------------------------------------- opencode-go


def test_opencode_go_glm_is_gateway_normalized():
    glm = _models_by_id(load_catalog(), "opencode-go")["glm-5.3"]
    assert glm.compat.thinking_format is None  # gateway strips the zai dialect
    assert glm.compat == CompatFlags()  # no compat block at all: pure defaults


def test_opencode_go_multi_protocol_gateway():
    provider = load_catalog()["opencode-go"]
    assert provider.name == "OpenCode Go"
    assert provider.base_url == "https://opencode.ai/inference/go/v1"
    assert provider.api_key_env == "OPENCODE_API_KEY"
    assert provider.adapter == ""  # adapter lives on each model, not the provider
    models = _models_by_id(load_catalog(), "opencode-go")
    assert list(models) == ["claude-haiku-5-5", "glm-5.3", "deepseek-v4-pro"]
    assert models["claude-haiku-5-5"].api == "anthropic-messages"
    assert models["glm-5.3"].api == "openai-completions"
    assert models["deepseek-v4-pro"].api == "openai-completions"
    assert models["deepseek-v4-pro"].max_tokens == 384000
    assert all(m.base_url == provider.base_url for m in models.values())
    assert all(m.cost == Cost() for m in models.values())
    assert all(m.compat == CompatFlags() for m in models.values())


# ------------------------------------------------- parser (on synthetic input)

SYNTHETIC_CATALOG = """\
# A synthetic catalog exercising every parser feature.
providers:
  acme:  # trailing comment on the provider line
    name: Acme API
    base_url: https://api.acme.dev/v1   # trailing comment after a URL
    api_key_env: ACME_API_KEY
    adapter: openai-completions
    models:
      acme-mini:
        context_window: 1000
        max_tokens: 512
        reasoning: false
        cost: {input: 1.5, output: 2}
      acme-big:
        adapter: anthropic-messages
        base_url: https://override.acme.dev/v2
        context_window: 2000
        max_tokens: 1024
        reasoning: true
        compat:
          thinking_format: zai
"""


def _acme():
    return _models_by_id(_build_catalog(SYNTHETIC_CATALOG), "acme")


def test_parser_strips_full_line_and_trailing_comments():
    provider = _build_catalog(SYNTHETIC_CATALOG)["acme"]
    assert provider.name == "Acme API"
    assert provider.base_url == "https://api.acme.dev/v1"


def test_parser_inline_flow_map_cost():
    assert _acme()["acme-mini"].cost == Cost(input=1.5, output=2.0, total=0.0)


def test_parser_bools_and_ints_are_typed():
    mini = _acme()["acme-mini"]
    assert mini.reasoning is False
    assert mini.context_window == 1000
    assert mini.max_tokens == 512
    assert _acme()["acme-big"].reasoning is True


def test_parser_per_model_adapter_and_base_url_override():
    models = _acme()
    assert models["acme-mini"].api == "openai-completions"  # provider fallback
    assert models["acme-mini"].base_url == "https://api.acme.dev/v1"
    assert models["acme-big"].api == "anthropic-messages"  # model-level override
    assert models["acme-big"].base_url == "https://override.acme.dev/v2"
    assert models["acme-big"].compat == CompatFlags(thinking_format="zai")


def test_catalog_never_sniffs_even_on_known_hosts():
    # A catalog model with NO compat block on a bigmodel.cn host must get
    # pure dataclass defaults — explicit (or absent) compat always wins.
    text = (
        "providers:\n"
        "  relay:\n"
        "    name: Relay\n"
        "    base_url: https://relay.bigmodel.cn/v1\n"
        "    api_key_env: RELAY_API_KEY\n"
        "    adapter: openai-completions\n"
        "    models:\n"
        "      r1:\n"
        "        context_window: 100\n"
        "        max_tokens: 50\n"
        "        reasoning: false\n"
    )
    model = find_model(_build_catalog(text), "r1")
    assert model.base_url == "https://relay.bigmodel.cn/v1"
    assert model.compat == CompatFlags()
    assert model.compat.thinking_format is None  # NOT the sniffed "zai"


def test_parser_rejects_anchors_and_aliases():
    anchored = (
        "providers:\n"
        "  p:\n"
        "    name: P\n"
        "    base_url: https://p.dev/v1\n"
        "    api_key_env: P_KEY\n"
        "    adapter: openai-completions\n"
        "    models:\n"
        "      m:\n"
        "        compat: &shared\n"
        "          thinking_format: zai\n"
        "        context_window: 1\n"
        "        max_tokens: 1\n"
        "        reasoning: false\n"
    )
    with pytest.raises(ValueError, match="anchor"):
        _build_catalog(anchored)
    aliased = anchored.replace("&shared", "*shared")
    with pytest.raises(ValueError, match="alias|anchor"):
        _build_catalog(aliased)


def test_parser_rejects_duplicate_keys():
    text = SYNTHETIC_CATALOG.replace(
        "        max_tokens: 512\n", "        max_tokens: 512\n        max_tokens: 999\n"
    )
    with pytest.raises(ValueError, match="duplicate key"):
        _build_catalog(text)


def test_parser_rejects_tabs_and_list_items():
    with pytest.raises(ValueError, match="tab"):
        _build_catalog("providers:\n\tp: {}\n")
    with pytest.raises(ValueError, match="list"):
        _build_catalog("providers:\n  - item\n")


def test_parser_rejects_unknown_keys_with_context():
    typo = SYNTHETIC_CATALOG.replace("context_window: 1000", "context_windw: 1000")
    with pytest.raises(ValueError, match="unknown key 'context_windw'"):
        _build_catalog(typo)
    bad_compat = SYNTHETIC_CATALOG.replace(
        "thinking_format: zai", "thinking_formt: zai"
    )
    with pytest.raises(ValueError, match="unknown key 'thinking_formt'"):
        _build_catalog(bad_compat)


def test_parser_rejects_missing_required_model_keys():
    missing = SYNTHETIC_CATALOG.replace("        max_tokens: 512\n", "")
    with pytest.raises(ValueError, match="missing required key 'max_tokens'"):
        _build_catalog(missing)
