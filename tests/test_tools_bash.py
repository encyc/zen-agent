"""bash tool tests: exit codes, timeout, abort, truncation, progress.

Commands are POSIX shell one-liners (macOS/Linux); nothing touches the network.
"""

import asyncio
import time

from zenagent.events import AbortToken
from zenagent.tools.bash import BASH, MAX_OUTPUT_BYTES


async def call(**args):
    return await BASH.execute("call_1", args, None)


async def test_echo_succeeds_and_reports_exit_code():
    result = await call(command="echo hello")
    assert not result.is_error
    text = result.content[0].text
    assert "hello" in text
    assert "exit code: 0" in text
    assert result.details["exit_code"] == 0
    assert result.details["timed_out"] is False


async def test_stderr_is_merged_into_the_output():
    result = await call(command="echo out; echo err 1>&2")
    text = result.content[0].text
    assert "out" in text and "err" in text


async def test_nonzero_exit_is_not_an_error_result():
    result = await call(command="exit 3")
    assert result.is_error is False  # command failure is information, not a tool failure
    text = result.content[0].text
    assert "exit code: 3" in text
    assert result.details["exit_code"] == 3


async def test_timeout_kills_and_annotates_promptly():
    started = time.monotonic()
    result = await call(command="echo before; sleep 10", timeout_s=0.3)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, f"timeout did not return promptly ({elapsed:.1f}s)"
    assert result.details["timed_out"] is True
    text = result.content[0].text
    assert "before" in text  # output produced before the kill is kept
    assert "timed out" in text


async def test_abort_kills_the_command_and_returns_partial_output():
    token = AbortToken()

    async def abort_soon():
        await asyncio.sleep(0.2)
        token.abort()

    task = asyncio.create_task(abort_soon())
    started = time.monotonic()
    result = await BASH.execute(
        "call_1", {"command": "echo started; sleep 10", "timeout_s": 30}, token
    )
    elapsed = time.monotonic() - started
    await task

    assert elapsed < 5.0
    assert result.details["aborted"] is True
    assert "started" in result.content[0].text
    assert "aborted" in result.content[0].text


async def test_large_output_is_truncated_keeping_head_and_tail():
    # 40k lines of "x" -> well past the 30 KB cap
    result = await call(command="yes x | head -c 40000")
    text = result.content[0].text

    assert result.details["truncated"] is True
    assert "truncated" in text and "bytes of output" in text
    assert len(text) < MAX_OUTPUT_BYTES + 2000  # bounded, marker included
    assert text.startswith("exit code: 0")
    body = text.split("exit code: 0\n", 1)[1]
    assert body.startswith("x")  # head of the output is kept
    assert body.rstrip().endswith("x")  # tail is kept too


async def test_progress_updates_are_streamed_while_running():
    updates: list = []

    def on_update(partial) -> None:
        updates.append(partial)

    result = await BASH.execute(
        "call_1",
        {"command": "for i in 1 2 3 4 5 6; do echo $i; sleep 0.2; done"},
        None,
        on_update,
    )
    assert not result.is_error
    assert updates, "expected at least one streamed progress update"
    assert all(update.details.get("streaming") is True for update in updates)
    # the final update reflects the tail of the output
    assert "6" in updates[-1].content[0].text


async def test_command_runs_in_the_current_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = await call(command="pwd")
    assert str(tmp_path) in result.content[0].text


async def test_empty_output_reports_only_exit_code():
    result = await call(command="true")
    assert result.content[0].text == "exit code: 0"


async def test_missing_command_argument_is_rejected_by_the_schema():
    from zenagent.agent.validate import validate_arguments

    assert validate_arguments(BASH.parameters, {}) is not None
    assert validate_arguments(BASH.parameters, {"command": "echo hi"}) is None


# ---------------------------------------------------------------------------
# Non-interactive environment and cwd
# ---------------------------------------------------------------------------


async def test_commands_run_without_a_pager_or_interactive_prompt(monkeypatch):
    """git/man/npm must not wait for a TTY that does not exist."""
    for key in ("PAGER", "GIT_TERMINAL_PROMPT", "PIP_NO_INPUT", "CI"):
        monkeypatch.delenv(key, raising=False)

    result = await call(
        command='printf "%s|%s|%s|%s" "$PAGER" "$GIT_TERMINAL_PROMPT" "$PIP_NO_INPUT" "$CI"'
    )
    assert not result.is_error
    assert "cat|0|1|true" in result.content[0].text


async def test_user_environment_wins_over_the_non_interactive_defaults(monkeypatch):
    monkeypatch.setenv("PAGER", "less")
    result = await call(command='printf "%s" "$PAGER"')
    assert "less" in result.content[0].text


async def test_cwd_argument_runs_the_command_in_that_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    result = await call(command="pwd", cwd="sub")
    assert not result.is_error
    assert str(sub) in result.content[0].text
    assert result.details["cwd"] == str(sub)


async def test_missing_cwd_is_an_error_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = await call(command="pwd", cwd="does/not/exist")
    assert result.is_error
    assert "cwd" in result.content[0].text.lower() or "directory" in result.content[0].text.lower()


async def test_cwd_does_not_leak_between_calls(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    await call(command="pwd", cwd="sub")
    result = await call(command="pwd")
    assert str(tmp_path) in result.content[0].text
    assert str(sub) not in result.content[0].text
