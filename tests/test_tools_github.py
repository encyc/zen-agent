"""github tool: conditional registration and read-only ops through a fake ``gh``."""

import os
import stat

import pytest

from zenagent.tools.github import GITHUB, build_github_tool, github_available

FAKE_GH = """#!/usr/bin/env python
import json
import sys

args = sys.argv[1:]
if args[:2] == ["pr", "view"]:
    print(json.dumps({
        "number": 7, "title": "Fix it", "state": "OPEN",
        "url": "https://example.test/pr/7", "author": {"login": "me"},
        "baseRefName": "main", "headRefName": "feat", "mergeable": "MERGEABLE",
        "reviewDecision": "APPROVED", "isDraft": False,
    }))
elif args[:2] == ["pr", "diff"]:
    print("diff --git a/x b/x")
elif args[:2] == ["pr", "checks"]:
    print("build\\tpass\\t1m")
elif args[:2] == ["run", "view"]:
    print("job log line")
else:
    print("unknown args: %r" % (args,), file=sys.stderr)
    sys.exit(2)
"""


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "gh"
    script.write_text(FAKE_GH)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return bin_dir


async def call(**args):
    return await GITHUB.execute("c1", args, None)


def test_github_tool_is_registered_only_when_gh_is_available(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    assert github_available() is False
    assert build_github_tool() is None


def test_github_tool_registers_when_gh_exists(fake_gh):
    assert github_available() is True
    assert build_github_tool() is GITHUB
    assert GITHUB.approval == "read"


async def test_pr_view_renders_key_values(fake_gh):
    result = await call(op="pr_view", pr="7")
    assert result.is_error is False
    text = result.content[0].text
    assert "PR #7: Fix it" in text
    assert "state: OPEN" in text
    assert "main ← head: feat" in text
    assert "review: APPROVED" in text


async def test_pr_diff_and_checks(fake_gh):
    diff = await call(op="pr_diff")
    assert diff.content[0].text == "diff --git a/x b/x"
    checks = await call(op="pr_checks")
    assert "build" in checks.content[0].text


async def test_run_logs_needs_a_run_id(fake_gh):
    result = await call(op="run_logs")
    assert result.is_error is True
    assert "run" in result.content[0].text


async def test_run_logs_reads_failed_job_logs(fake_gh):
    result = await call(op="run_logs", run="42")
    assert result.is_error is False
    assert "job log line" in result.content[0].text


async def test_unknown_op_is_an_error(fake_gh):
    result = await call(op="merge_pr")
    assert result.is_error is True
    assert "unknown op" in result.content[0].text


async def test_missing_gh_at_call_time_reports_installation(fake_gh, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    result = await call(op="pr_view")
    assert result.is_error is True
    assert "not installed" in result.content[0].text


async def test_runtime_registers_github_only_when_available(
    runtime_factory, monkeypatch
):
    monkeypatch.setenv("PATH", "/nonexistent")
    runtime = await runtime_factory()
    assert "github" not in [tool.name for tool in runtime.agent.tools]


async def test_runtime_registers_github_with_gh(fake_gh, runtime_factory):
    runtime = await runtime_factory()
    assert "github" in [tool.name for tool in runtime.agent.tools]
    # hidden from the prompt when the binary disappears again
    assert GITHUB.name == "github"
