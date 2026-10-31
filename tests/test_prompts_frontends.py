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


def test_real_build_agent_composes_the_prompt() -> None:
    from zenagent.app.support import resolve_model
    from zenagent.cli.main import _build_agent
    from zenagent.cli.permissions import PermissionGate
    from zenagent.providers.catalog import load_catalog
    from zenagent.tools.fs import READ_FILE

    model = resolve_model(load_catalog(), None)
    gate = PermissionGate(assume_yes=True, interactive=False)
    agent = _build_agent(
        model,
        "test-key",
        "off",
        gate,
        tools=[READ_FILE],
        context_files=False,
        extra_prompt="EXT",
    )
    assert agent.system_prompt.endswith("\n\nEXT")
    assert "Available tools: read_file" in agent.system_prompt


def test_repl_compose_prompt_is_compose_based() -> None:
    from zenagent.cli.repl import _compose_prompt
    from zenagent.tools.fs import READ_FILE

    prompt = _compose_prompt([READ_FILE], context_files=False, extra_text="EXT")
    assert prompt.endswith("\n\nEXT")
    assert "Available tools: read_file" in prompt
