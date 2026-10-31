"""``zen prompts``: read-only inspection of the prompt blocks."""

from __future__ import annotations

from zenagent.cli.prompts_cmd import main
from zenagent.prompts import PROMPT_VERSION


def test_list_prints_every_block(capsys) -> None:
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert PROMPT_VERSION in out
    assert "system.identity" in out
    assert "tool.read_file" in out


def test_show_prints_template(capsys) -> None:
    assert main(["show", "system.identity"]) == 0
    assert "You are zenagent" in capsys.readouterr().out


def test_show_unknown_block_exits_2(capsys) -> None:
    assert main(["show", "no.such.block"]) == 2
    assert "unknown" in capsys.readouterr().err


def test_estimate_prints_total(capsys) -> None:
    assert main(["estimate"]) == 0
    out = capsys.readouterr().out
    assert "total" in out
    assert "token" in out
