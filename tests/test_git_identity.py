"""The suite must never depend on the machine's git identity.

CI's ubuntu runners have no ``user.name``/``user.email`` and a dotless
hostname, so git cannot auto-detect an author there and ``git commit`` exits
128 — which is exactly how the worktree-heavy tests went red on ubuntu while
the same code stayed green on macOS. ``tests/conftest.py`` puts the identity in
the environment; this test pins that contract.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_git_commits_carry_the_suite_identity(tmp_path: Path, monkeypatch) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    # Pin every config file away, so the only identity left is the environment.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.delenv("EMAIL", raising=False)

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

    author = subprocess.run(
        ["git", "log", "-1", "--format=%an <%ae>"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert author == "ZenAgent Tests <tests@zenagent.invalid>"
