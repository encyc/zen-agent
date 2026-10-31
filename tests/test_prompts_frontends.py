"""Frontend parity: each frontend's context reproduces its golden prompt."""

from __future__ import annotations

from pathlib import Path

import pytest
from prompt_fixtures import CONFIGS, CWD

from zenagent.prompts import PromptContext, compose_system
from zenagent.skills import format_skill_entries

GOLDEN = Path(__file__).resolve().parent / "golden" / "prompts"
FRONTEND_CONFIGS = ("tui-14", "headless-14", "rpc-13", "eval-11")


def frontend_prompt(name: str) -> str:
    cfg = CONFIGS[name]
    return compose_system(
        PromptContext(
            cwd=CWD,
            tool_names=cfg.tool_names,
            mode=cfg.mode,
            context_text=cfg.context_text,
            skill_entries=format_skill_entries(list(cfg.skills)),
            extension_fragments=cfg.extension_fragments,
            system_override=cfg.system_override,
        )
    ).text


@pytest.mark.parametrize("name", FRONTEND_CONFIGS)
def test_frontend_prompt_matches_golden(name: str) -> None:
    expected = (GOLDEN / f"{name}.txt").read_text(encoding="utf-8")
    assert frontend_prompt(name) == expected


def test_tui_and_headless_keep_their_own_roster_order() -> None:
    tui = frontend_prompt("tui-14")
    headless = frontend_prompt("headless-14")
    assert "todo, task, read_skill" in tui
    assert "read_skill, todo, task" in headless
