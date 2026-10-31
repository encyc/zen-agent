"""Golden parity: the new system assembly must reproduce v0.4.1 byte-for-byte."""

from __future__ import annotations

from pathlib import Path

import pytest
from prompt_fixtures import CONFIGS, CWD

from zenagent.prompts import PromptContext, compose_system

GOLDEN = Path(__file__).resolve().parent / "golden" / "prompts"
BASE_CONFIGS = ("default-11", "tui-14", "headless-14", "rpc-13", "eval-11")


@pytest.mark.parametrize("name", BASE_CONFIGS)
def test_system_prompt_matches_golden(name: str) -> None:
    cfg = CONFIGS[name]
    rendered = compose_system(
        PromptContext(
            cwd=CWD,
            tool_names=cfg.tool_names,
            system_override=cfg.system_override,
            include_context=False,
            include_skills=False,
            include_extensions=False,
            include_mode=False,
        )
    )
    expected = (GOLDEN / f"{name}.base.txt").read_text(encoding="utf-8")
    assert rendered.text == expected


FULL_CONFIGS = (
    "default-11",
    "tui-14",
    "headless-14",
    "plan",
    "skills",
    "context",
    "extension",
    "rpc-13",
    "eval-11",
)


@pytest.mark.parametrize("name", FULL_CONFIGS)
def test_full_prompt_matches_golden(name: str) -> None:
    from zenagent.skills import format_skill_entries

    cfg = CONFIGS[name]
    rendered = compose_system(
        PromptContext(
            cwd=CWD,
            tool_names=cfg.tool_names,
            mode=cfg.mode,
            context_text=cfg.context_text,
            skill_entries=format_skill_entries(list(cfg.skills)),
            extension_fragments=cfg.extension_fragments,
            system_override=cfg.system_override,
        )
    )
    expected = (GOLDEN / f"{name}.txt").read_text(encoding="utf-8")
    assert rendered.text == expected
