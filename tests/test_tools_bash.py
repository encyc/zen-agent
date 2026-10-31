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


# ---------------------------------------------------------------------------
# async background jobs (``async: true``)
# ---------------------------------------------------------------------------


async def _call_async(command: str, **args):
    """Call bash with the reserved-word argument ``async`` set to true."""
    return await BASH.execute(
        "call_async", {"command": command, "async": True, **args}, None
    )


async def _close_jobs() -> None:
    from zenagent.tools.jobs import get_job_manager

    await get_job_manager().close()


async def test_async_bash_returns_immediately_with_a_job_id():
    started = time.monotonic()
    result = await _call_async("sleep 5; echo late")
    elapsed = time.monotonic() - started
    await _close_jobs()

    assert elapsed < 2.0, f"async bash blocked for {elapsed:.2f}s"
    assert not result.is_error
    job_id = result.details["job_id"]
    assert job_id.startswith("bg_")
    assert result.details["status"] == "running"
    assert result.details["async"] is True
    text = result.content[0].text
    assert f"started background job {job_id}" in text
    assert "sleep 5; echo late" in text


async def test_async_bash_job_runs_to_completion_with_output():
    from zenagent.tools.jobs import get_job_manager

    result = await _call_async("echo background-done")
    manager = get_job_manager()
    job = await manager.wait(result.details["job_id"], timeout_s=5)

    assert job.status == "ok"
    assert job.exit_code == 0
    assert "background-done" in job.output


async def test_async_bash_timeout_is_clamped_and_kills_the_job():
    from zenagent.tools.jobs import get_job_manager

    result = await _call_async("echo before; sleep 10", timeout_s=0.01)
    manager = get_job_manager()
    job_id = result.details["job_id"]
    job = manager.snapshot(job_id)
    assert job.timeout_s == 1.0  # clamped up to the one-second floor
    done = await manager.wait(job_id, timeout_s=5)

    assert done.status == "timed_out"
    assert done.timed_out is True
    assert "before" in done.output


async def test_async_bash_timeout_is_clamped_down_to_one_hour():
    from zenagent.tools.jobs import get_job_manager

    result = await _call_async("true", timeout_s=99_999)
    manager = get_job_manager()
    job_id = result.details["job_id"]
    assert manager.snapshot(job_id).timeout_s == 3600.0
    await manager.wait(job_id, timeout_s=5)


async def test_async_bash_default_background_timeout_is_ten_minutes():
    from zenagent.tools.jobs import get_job_manager

    result = await _call_async("true")
    manager = get_job_manager()
    job_id = result.details["job_id"]
    assert manager.snapshot(job_id).timeout_s == 600.0
    await manager.wait(job_id, timeout_s=5)


async def test_async_bash_rejects_a_non_boolean_async_argument():
    result = await BASH.execute(
        "call_async", {"command": "true", "async": "yes"}, None
    )
    assert result.is_error
    assert "async" in result.content[0].text


async def test_async_bash_unknown_shell_is_an_error_result(monkeypatch):
    monkeypatch.setenv("ZENAGENT_SHELL", "fish")
    result = await _call_async("echo hi")
    assert result.is_error
    assert "fish" in result.content[0].text


# ---------------------------------------------------------------------------
# shell abstraction (resolve_shell)
# ---------------------------------------------------------------------------


def _resolve(monkeypatch, env_value: str | None, which: dict | None = None):
    from zenagent.tools import shell as shell_module

    monkeypatch.delenv("ZENAGENT_SHELL", raising=False)
    if env_value is not None:
        monkeypatch.setenv("ZENAGENT_SHELL", env_value)
    if which is not None:
        monkeypatch.setattr(
            shell_module.shutil, "which", lambda name: which.get(name)
        )
    return shell_module.resolve_shell("echo hi")


async def test_bash_defaults_to_posix_sh(monkeypatch):
    assert _resolve(monkeypatch, None) == ("/bin/sh", ["-c", "echo hi"])


async def test_shell_env_posix_value_is_the_default(monkeypatch):
    assert _resolve(monkeypatch, "posix") == ("/bin/sh", ["-c", "echo hi"])


async def test_shell_env_cmd_uses_cmd_exe(monkeypatch):
    assert _resolve(monkeypatch, "cmd") == ("cmd.exe", ["/c", "echo hi"])


async def test_shell_env_pwsh_uses_powershell_flags(monkeypatch):
    resolved = _resolve(monkeypatch, "pwsh", which={"pwsh": "/fake/pwsh"})
    assert resolved == (
        "/fake/pwsh",
        ["-NoLogo", "-NonInteractive", "-Command", "echo hi"],
    )


async def test_shell_env_powershell_falls_back_to_the_literal_name(monkeypatch):
    resolved = _resolve(monkeypatch, "powershell", which={})
    assert resolved == (
        "powershell",
        ["-NoLogo", "-NonInteractive", "-Command", "echo hi"],
    )


async def test_shell_env_value_is_case_insensitive(monkeypatch):
    resolved = _resolve(monkeypatch, "PWSH", which={"pwsh": "/fake/pwsh"})
    assert resolved[0] == "/fake/pwsh"


async def test_shell_env_absolute_path_is_run_as_a_posix_shell(monkeypatch, tmp_path):
    shell_path = str(tmp_path / "myshell")
    resolved = _resolve(monkeypatch, shell_path)
    assert resolved == (shell_path, ["-c", "echo hi"])


async def test_shell_unknown_env_value_is_rejected_by_bash(monkeypatch):
    monkeypatch.setenv("ZENAGENT_SHELL", "fish")
    result = await call(command="echo hi")
    assert result.is_error
    assert "fish" in result.content[0].text


async def test_bash_runs_through_an_absolute_configured_shell(monkeypatch):
    monkeypatch.setenv("ZENAGENT_SHELL", "/bin/sh")
    result = await call(command="echo configured-shell")
    assert not result.is_error
    assert "configured-shell" in result.content[0].text


async def test_windows_default_prefers_pwsh(monkeypatch):
    from zenagent.tools import shell as shell_module

    monkeypatch.delenv("ZENAGENT_SHELL", raising=False)
    monkeypatch.setattr(shell_module.os, "name", "nt")
    monkeypatch.setattr(
        shell_module.shutil,
        "which",
        lambda name: "/fake/pwsh" if name == "pwsh" else None,
    )
    assert shell_module.resolve_shell("echo hi") == (
        "/fake/pwsh",
        ["-NoLogo", "-NonInteractive", "-Command", "echo hi"],
    )


async def test_windows_default_falls_back_to_windows_powershell(monkeypatch):
    from zenagent.tools import shell as shell_module

    monkeypatch.delenv("ZENAGENT_SHELL", raising=False)
    monkeypatch.setattr(shell_module.os, "name", "nt")
    monkeypatch.setattr(
        shell_module.shutil,
        "which",
        lambda name: "/fake/powershell" if name == "powershell" else None,
    )
    assert shell_module.resolve_shell("echo hi") == (
        "/fake/powershell",
        ["-NoLogo", "-NonInteractive", "-Command", "echo hi"],
    )


async def test_windows_default_falls_back_to_cmd(monkeypatch):
    from zenagent.tools import shell as shell_module

    monkeypatch.delenv("ZENAGENT_SHELL", raising=False)
    monkeypatch.setattr(shell_module.os, "name", "nt")
    monkeypatch.setattr(shell_module.shutil, "which", lambda name: None)
    assert shell_module.resolve_shell("echo hi") == ("cmd.exe", ["/c", "echo hi"])
