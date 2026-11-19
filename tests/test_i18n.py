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
    paired_others = {
        key[: -len(".other")]
        for key in en
        if key.endswith(".other") and key[: -len(".other")] + ".one" in en
    }
    assert ones == paired_others


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


def _literal_call_site_keys() -> set[str]:
    """Every message key a `t(...)`/`tn(...)` call site passes as a literal.

    Dynamic keys (variables, f-strings) are invisible to this scan by design:
    the guard pins the hand-written ones, where a rename or a typo otherwise
    ships the raw key to a user — `t()` falls back to the key itself.
    """
    import ast

    from pathlib import Path

    keys: set[str] = set()
    roots = [Path(__file__).resolve().parents[1] / "zenagent"]
    while roots:
        base = roots.pop()
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else (func.attr if isinstance(func, ast.Attribute) else None)
                )
                if name not in ("t", "tn") or not node.args:
                    continue
                arg = node.args[0]
                if not (
                    isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                ):
                    continue
                if name == "t":
                    keys.add(arg.value)
                else:  # tn() resolves <key>.one / <key>.other
                    keys.add(f"{arg.value}.one")
                    keys.add(f"{arg.value}.other")
    return keys


def test_every_literal_call_site_key_is_in_the_catalog():
    """A hand-written key that misses the catalog renders the raw key to the
    user (see `t`'s fallback) and passes every other test — this is the one
    gate that reads the call sites themselves."""
    en = _catalogs()["en"]
    missing = sorted(_literal_call_site_keys() - set(en))
    assert not missing, f"call-site keys missing from the en catalog: {missing}"


def test_the_call_site_scan_sees_a_real_surface():
    """A guard that scans nothing is a cap nobody enforces. If this floor
    breaks, either the package shrank massively (adjust it) or the scan
    broke (fix the scan)."""
    assert len(_literal_call_site_keys()) >= 400
