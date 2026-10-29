"""Catastrophic-command policy: refused even under --yes, overridable explicitly."""

import pytest

from pyagent.cli.permissions import PermissionGate, dangerous_command_reason
from pyagent.types import ToolCall


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -rf /*",
        "sudo rm -fr ~",
        "rm -r -f /",
        "RM -RF /",
        ":(){ :|:& };:",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        "chmod -R 777 /",
        "shutdown -h now",
        "reboot",
        "echo x > /dev/sda",
        "init 0",
    ],
)
def test_catastrophic_commands_are_flagged(command):
    assert dangerous_command_reason(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf ./build",
        "rm -rf node_modules dist",
        "git push --force",
        "dd if=image.iso of=out.bin",
        "chmod -R 777 ./vendor",
        "echo hi > out.txt",
        "ls -la",
        "mkdocs build",
    ],
)
def test_normal_commands_pass(command):
    assert dangerous_command_reason(command) is None


def test_empty_command_is_not_dangerous():
    assert dangerous_command_reason("   ") is None


async def test_yes_does_not_disable_the_dangerous_command_policy():
    gate = PermissionGate(assume_yes=True, interactive=False)
    allowed, reason = await gate.check("bash", {"command": "rm -rf /"})
    assert allowed is False
    assert "refused" in reason and "--allow-dangerous" in reason


async def test_allow_dangerous_is_the_explicit_escape_hatch():
    gate = PermissionGate(assume_yes=True, interactive=False, allow_dangerous=True)
    assert (await gate.check("bash", {"command": "rm -rf /"}))[0] is True


async def test_safe_command_still_works_under_yes():
    gate = PermissionGate(assume_yes=True, interactive=False)
    assert (await gate.check("bash", {"command": "rm -rf ./build"}))[0] is True


async def test_hook_reports_the_refusal_to_the_model():
    gate = PermissionGate(assume_yes=True, interactive=False)
    call = ToolCall(id="c1", name="bash", arguments={"command": "mkfs.ext4 /dev/sda1"})
    verdict = await gate.before_tool_call(call, {"command": "mkfs.ext4 /dev/sda1"})
    assert verdict is not None and verdict["block"] is True
    assert "mkfs" in verdict["reason"]


async def test_non_bash_tools_are_unaffected():
    gate = PermissionGate(assume_yes=True, interactive=False)
    assert (await gate.check("write_file", {"path": "/tmp/x"}))[0] is True


async def test_loading_a_skill_is_read_only():
    """Reading a local SKILL.md must not require --yes."""
    gate = PermissionGate(assume_yes=False, interactive=False)
    allowed, reason = await gate.check("read_skill", {"name": "demo"})
    assert allowed is True, reason
