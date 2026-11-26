"""Auto session title: normalization and the background titler."""

from __future__ import annotations

import pytest

from zenagent.app.title import AUTO_TITLE_LIMIT, clean_title


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("登录重试修复", "登录重试修复"),
        ("「登录重试修复」", "登录重试修复"),
        ("`登录重试修复`", "登录重试修复"),
        ("**登录重试修复**", "登录重试修复"),
        ("会话名：登录重试修复", "登录重试修复"),
        ("Title: login retry fix", "login retry fix"),
        ("```\n登录重试修复\n```", "登录重试修复"),
        ("登录重试修复。", "登录重试修复"),
        ("登录重试修复\n\n说明：这是会话名", "登录重试修复"),
        ("  登录   重试  ", "登录 重试"),
        ("", ""),
        ("   \n  ", ""),
        ("长" * (AUTO_TITLE_LIMIT + 20), "长" * AUTO_TITLE_LIMIT),
    ],
)
def test_clean_title(raw: str, expected: str) -> None:
    assert clean_title(raw) == expected
