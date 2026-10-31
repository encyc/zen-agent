"""Compaction blocks: prompt parity and the summary-result wrapper."""

from __future__ import annotations

import json
from pathlib import Path

from prompt_fixtures import TRANSCRIPT

from zenagent.prompts import compact_result, compose_compact

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "prompts"


def test_compose_compact_matches_golden() -> None:
    system, user = compose_compact(TRANSCRIPT)
    golden = json.loads((GOLDEN / "compact.json").read_text(encoding="utf-8"))
    assert system == golden["system"]
    assert user == golden["user"]


def test_compact_result_wraps_summary() -> None:
    assert compact_result("S") == "# 此前会话摘要\nS"


def test_compact_module_has_no_literal_prompts() -> None:
    source = (ROOT / "zenagent" / "app" / "compact.py").read_text(encoding="utf-8")
    assert "请把下面这段编码会话压缩" not in source
    assert "You compress coding-session transcripts" not in source
    assert "# 此前会话摘要" not in source
