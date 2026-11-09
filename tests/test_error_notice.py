"""Run-failure classification: raw provider text → actionable notice."""

import pytest

from zenagent.app.error_notice import bug_notice, run_error_notice
from zenagent.i18n import set_locale, t


@pytest.fixture(autouse=True)
def english():
    set_locale("en")
    yield
    set_locale("en")


def test_deepseek_balance_error_tells_the_user_to_top_up():
    # Verbatim from a real run_end detail: the request id even contains "402",
    # which must not skew the classification — the phrase markers decide.
    detail = (
        "HTTPStatusError: Client error '402 Payment Required' for url "
        "'https://api.deepseek.com/chat/completions': Insufficient Balance "
        "(request_id: deffeddd-acd2-4f1d-9df9-fd2ad9a402ff)"
    )
    assert run_error_notice(detail).startswith(t("notice.error.balance"))
    assert detail in run_error_notice(detail)


def test_every_category_maps_to_its_headline():
    cases = {
        "auth": "HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.example.com/v1'",
        "context": "BadRequestError: This model's maximum context length is 131072 tokens",
        "rate_limit": "HTTPStatusError: Client error '429 Too Many Requests': rate limit exceeded",
        "network": "ConnectError: [Errno 8] nodename nor servname provided, or not known",
        "outage": "HTTPStatusError: Server error '503 Service Unavailable' for url 'https://api.example.com/v1'",
    }
    for category, detail in cases.items():
        assert run_error_notice(detail).startswith(t(f"notice.error.{category}"))


def test_a_request_id_containing_402_does_not_imply_balance():
    detail = (
        "HTTPStatusError: Server error '500 Internal Server Error' for url "
        "'https://api.example.com/v1': upstream exploded "
        "(request_id: deffeddd-acd2-4f1d-9df9-fd2ad9a402ff)"
    )
    assert run_error_notice(detail).startswith(t("notice.error.outage"))


def test_blank_or_literal_error_falls_back_to_the_generic_notice():
    assert run_error_notice(None) == t("notice.run_failed")
    assert run_error_notice("") == t("notice.run_failed")
    assert run_error_notice("error") == t("notice.run_failed")


def test_an_unknown_provider_error_keeps_the_generic_provider_headline():
    assert run_error_notice("boom").startswith(t("notice.error.provider"))


def test_bug_notice_names_the_tracker_and_keeps_the_exception():
    notice = bug_notice("RuntimeError: name 'self' is not defined")
    assert notice.startswith(t("notice.error.bug"))
    assert "RuntimeError: name 'self' is not defined" in notice


def test_zh_locale_speaks_chinese():
    set_locale("zh")
    assert "余额不足" in run_error_notice("Insufficient Balance")
    assert "bug" in bug_notice("RuntimeError: boom")
