"""Golden baseline capture: the files must exist and be deterministic.

Task 1 records pre-refactor behavior; later tasks compare the new compose()
output against these files. A capture that changes between runs would make
every later parity test flaky, so determinism is asserted here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from prompt_fixtures import CONFIGS, TOOL_GOLDEN_NAMES

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "prompts"
SCRIPT = ROOT / "scripts" / "prompt_goldens.py"


def test_every_config_has_goldens() -> None:
    for name in CONFIGS:
        if name == "compact":
            continue
        text = GOLDEN / f"{name}.txt"
        base = GOLDEN / f"{name}.base.txt"
        assert text.is_file(), f"missing {text.name}"
        assert base.is_file(), f"missing {base.name}"
        assert text.read_text(encoding="utf-8"), f"{text.name} is empty"
        assert base.read_text(encoding="utf-8"), f"{base.name} is empty"
    tools = json.loads((GOLDEN / "tools.json").read_text(encoding="utf-8"))
    assert set(tools) == set(TOOL_GOLDEN_NAMES)
    compact = json.loads((GOLDEN / "compact.json").read_text(encoding="utf-8"))
    assert set(compact) == {"system", "user"}


def test_capture_is_deterministic(tmp_path: Path) -> None:
    runs = []
    for index in range(2):
        out = tmp_path / f"run{index}"
        subprocess.run(
            [sys.executable, str(SCRIPT), "--out", str(out)],
            check=True,
            cwd=ROOT,
            capture_output=True,
        )
        runs.append({path.name: path.read_bytes() for path in sorted(out.iterdir())})
    assert runs[0] == runs[1]


def test_check_mode_matches_committed_goldens() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
