"""Message catalog: key/placeholder parity, fallback, plurals and locale resolution."""

import pytest

from zenagent import i18n


def _catalogs() -> dict[str, dict[str, str]]:
    return i18n._catalogs()


def _fields(text: str) -> set[str]:
    from string import Formatter

    return {name for _, name, _, _ in Formatter().parse(text) if name}


def test_catalogs_are_key_and_placeholder_identical():
    en, zh = _catalogs()["en"], _catalogs()["zh"]
    assert set(en) == set(zh)
    for key, english in en.items():
        assert _fields(english) == _fields(zh[key]), key
        assert english.strip(), key
        assert zh[key].strip(), key


def test_plural_pairs_are_complete():
    en = _catalogs()["en"]
    ones = {key[: -len(".one")] for key in en if key.endswith(".one")}
    others = {key[: -len(".other")] for key in en if key.endswith(".other")}
    assert ones == others


def test_default_locale_is_english():
    assert i18n.available() == ("en", "zh")
    assert i18n.set_locale("en") == "en"
    assert i18n.active_locale() == "en"


def test_t_falls_back_to_english_then_key(monkeypatch):
    monkeypatch.setattr(
        i18n,
        "_catalogs",
        lambda: {"en": {"a": "A", "only_en": "E"}, "zh": {"a": "甲"}},
    )
    i18n.set_locale("zh")
    try:
        assert i18n.t("a") == "甲"
        assert i18n.t("only_en") == "E"
        assert i18n.t("missing.key") == "missing.key"
    finally:
        i18n.set_locale("en")


def test_placeholders_are_formatted():
    i18n.set_locale("zh")
    try:
        assert i18n.t("test.greeting", name="zen") == "你好 zen"
    finally:
        i18n.set_locale("en")
    assert i18n.t("test.greeting", name="zen") == "hello zen"


def test_plural_picks_one_or_other():
    i18n.set_locale("en")
    assert i18n.tn("test.apples", 1) == "1 apple"
    assert i18n.tn("test.apples", 3) == "3 apples"
    i18n.set_locale("zh")
    try:
        assert i18n.tn("test.apples", 3) == "3 个苹果"
    finally:
        i18n.set_locale("en")


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("zh", "zh"),
        ("zh-CN", "zh"),
        ("zh_cn.UTF-8", "zh"),
        ("zh-Hans", "zh"),
        ("en", "en"),
        ("en_US.UTF-8", "en"),
    ],
)
def test_resolve_language_normalizes_codes(spec, expected):
    assert i18n.resolve_language(spec, environ={}) == expected


def test_resolve_language_priority_and_environment():
    assert i18n.resolve_language("zh", pref="en", environ={}) == "zh"
    assert i18n.resolve_language(None, pref="zh", environ={}) == "zh"
    assert i18n.resolve_language(
        None, pref=None, environ={"ZENAGENT_LANG": "zh"}
    ) == "zh"
    assert i18n.resolve_language(None, pref=None, environ={"LANG": "zh_CN.UTF-8"}) == "zh"
    assert i18n.resolve_language(None, pref=None, environ={"LANG": "en_US.UTF-8"}) == "en"
    assert i18n.resolve_language(None, pref=None, environ={}) == "en"
    assert i18n.resolve_language("auto", pref="zh", environ={}) == "en"


def test_unknown_language_warns_and_falls_back():
    warnings: list[str] = []
    assert i18n.resolve_language("nonsense", environ={}, warnings=warnings) == "en"
    assert warnings and "nonsense" in warnings[0]


def test_set_locale_handles_auto_and_unknown():
    assert i18n.set_locale("auto", environ={"LANG": "zh_CN.UTF-8"}) == "zh"
    assert i18n.set_locale("nonsense") == "en"
    assert i18n.active_locale() == "en"
