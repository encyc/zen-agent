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


def _catalog(path: Path, entries: int, code_lines: int = 0) -> None:
    """Write a `key: value` dictionary with a controlled entry/code mix."""
    body = "".join(f'    "key{i}": "value {i}",\n' for i in range(entries))
    body += "".join(f"    x{i} = {i}\n" for i in range(code_lines))
    path.write_text(f"D = {{\n{body}}}\n")


def test_pure_catalog_is_measured_in_entries_not_lines(tmp_path):
    """822 lines but only 700 entries: the line cap does not apply."""
    pkg = tmp_path / "zenagent"
    pkg.mkdir()
    _catalog(pkg / "catalog.py", entries=700, code_lines=100)
    assert len((pkg / "catalog.py").read_text().splitlines()) > 800
    result = run_gate(tmp_path)
    assert result.returncode == 0, result.stdout


def test_catalog_over_the_entry_cap_fails(tmp_path):
    pkg = tmp_path / "zenagent"
    pkg.mkdir()
    _catalog(pkg / "catalog.py", entries=900)
    (tmp_path / "docs" / "architecture").mkdir(parents=True)
    (tmp_path / "docs" / "architecture" / "allowlist.json").write_text(
        json.dumps({"python": {"zenagent/catalog.py": 850}, "ts": {}})
    )
    result = run_gate(tmp_path)
    assert result.returncode == 1, result.stdout
    assert "900 entries" in result.stdout and "allowlisted at 850" in result.stdout


def test_logic_in_a_catalog_does_not_escape_the_line_cap(tmp_path):
    """Past the ratio the file is code again — and 900 lines of it still fails."""
    pkg = tmp_path / "zenagent"
    pkg.mkdir()
    # 100 entries + 880 code lines = 10% entries: below the 85% catalog ratio,
    # so the line cap applies and attributing it to "it's a dictionary" fails.
    _catalog(pkg / "catalog.py", entries=100, code_lines=880)
    result = run_gate(tmp_path)
    assert result.returncode == 1
    assert "lines" in result.stdout and "new file, cap 800" in result.stdout


def test_a_small_dictionary_stays_under_the_line_cap_too(tmp_path):
    """Below CATALOG_MIN_ENTRIES the ratio is moot; the plain 800 still applies."""
    pkg = tmp_path / "zenagent"
    pkg.mkdir()
    _catalog(pkg / "catalog.py", entries=40, code_lines=800)
    assert run_gate(tmp_path).returncode == 1


def test_stray_top_level_allowlist_keys_are_reported(tmp_path):
    """Three bare keys drifted to the allowlist's top level and were never read."""
    pkg = tmp_path / "zenagent"
    pkg.mkdir()
    (pkg / "tiny.py").write_text("x = 1\n")
    (tmp_path / "docs" / "architecture").mkdir(parents=True)
    (tmp_path / "docs" / "architecture" / "allowlist.json").write_text(
        json.dumps(
            {
                "python": {},
                "ts": {},
                "zenagent/app/runtime.py": 1200,
            }
        )
    )
    result = run_gate(tmp_path)
    assert result.returncode == 1
    assert "zenagent/app/runtime.py" in result.stdout
