"""Registry invariants: every declared block exists and its variables match."""

from __future__ import annotations

from zenagent.prompts.registry import validate_registry
from zenagent.prompts.spec import BLOCKS, SYSTEM_BLOCK_ORDER


def test_registry_is_valid() -> None:
    assert validate_registry() == []


def test_every_system_block_is_ordered() -> None:
    assert set(SYSTEM_BLOCK_ORDER) == {
        block_id for block_id in BLOCKS if block_id.startswith("system.")
    }


def test_budgets_are_positive() -> None:
    assert all(spec.budget_chars > 0 for spec in BLOCKS.values())


def test_versions_are_current() -> None:
    from zenagent.prompts.spec import PROMPT_VERSION

    assert all(spec.version == PROMPT_VERSION for spec in BLOCKS.values())
