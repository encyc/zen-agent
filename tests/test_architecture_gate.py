"""The LOC budget gate: only-shrink allowlist, 800 cap for new files."""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_architecture.py"


def run_gate(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_small_tree_passes(tmp_path):
    (tmp_path / "zenagent").mkdir()
    (tmp_path / "zenagent" / "tiny.py").write_text("x = 1\n")
    (tmp_path / "docs").mkdir()
    result = run_gate(tmp_path)
    assert result.returncode == 0, result.stdout


def test_oversized_new_file_fails(tmp_path):
    pkg = tmp_path / "zenagent"
    pkg.mkdir()
    (pkg / "big.py").write_text("# line\n" * 801)
    result = run_gate(tmp_path)
    assert result.returncode == 1
    assert "big.py" in result.stdout and "cap 800" in result.stdout


def test_allowlisted_file_may_not_grow(tmp_path):
    pkg = tmp_path / "zenagent"
    pkg.mkdir()
    (pkg / "legacy.py").write_text("# line\n" * 900)
    (tmp_path / "docs" / "architecture").mkdir(parents=True)
    (tmp_path / "docs" / "architecture" / "allowlist.json").write_text(
        json.dumps({"python": {"zenagent/legacy.py": 900}, "ts": {}})
    )
    # at the recorded size: clean
    assert run_gate(tmp_path).returncode == 0
    # one line more: the gate bites
    (pkg / "legacy.py").write_text("# line\n" * 901)
    result = run_gate(tmp_path)
    assert result.returncode == 1
    assert "allowlisted at 900" in result.stdout


def test_allowlisted_file_may_shrink(tmp_path):
    pkg = tmp_path / "zenagent"
    pkg.mkdir()
    (pkg / "legacy.py").write_text("# line\n" * 500)
    (tmp_path / "docs" / "architecture").mkdir(parents=True)
    (tmp_path / "docs" / "architecture" / "allowlist.json").write_text(
        json.dumps({"python": {"zenagent/legacy.py": 900}, "ts": {}})
    )
    assert run_gate(tmp_path).returncode == 0
