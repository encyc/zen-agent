"""标题提示词：渲染与变量声明。"""

from __future__ import annotations

from zenagent.prompts import compose_title
from zenagent.prompts.registry import read_block
from zenagent.prompts.spec import BLOCKS


def test_compose_title_renders_the_prompt() -> None:
    system, user = compose_title("帮我修一下登录重试的 bug")
    assert system == read_block("title.system")
    assert "帮我修一下登录重试的 bug" in user
    assert "只输出会话名" in user


def test_title_blocks_declare_their_vars() -> None:
    assert BLOCKS["title.system"].required_vars == ()
    assert BLOCKS["title.user"].required_vars == ("prompt",)
