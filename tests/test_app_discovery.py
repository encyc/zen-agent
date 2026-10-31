"""``/skills`` rescans, ``/reload`` re-discovers -- both without a restart.

Regression cover for the TUI reading the bootstrap snapshot forever: a skill
installed into the roots after the app started never appeared in ``/skills``,
and ``/reload`` -- documented as "re-discover skills, templates and extensions"
-- only refreshed MCP.
"""

from __future__ import annotations

import pytest

from zenagent.app.commands import build_default_commands

EXTENSION = """
from zenagent.agent.loop import AgentTool
from zenagent.types import TextContent


async def ext_echo(call_id, args, abort, on_update=None):
    from zenagent.agent.loop import ToolResultAgent

    return ToolResultAgent(content=[TextContent(text="ext")])


def register(api):
    api.register_tool(
        AgentTool(
            name="ext_echo",
            description="echo",
            parameters={"type": "object", "properties": {}},
            execute=ext_echo,
        )
    )
    api.on("agent_end", lambda event: None)
"""


def write_skill(root, name, *, description="added on disk", body="body"):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return directory


@pytest.fixture
def skill_root(tmp_path, monkeypatch):
    """Isolate discovery so the developer's ~/.zenagent/skills never leaks in."""
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr("zenagent.app.runtime.default_skill_roots", lambda cwd: [root])
    return root


def make_services(runtime):
    from zenagent.app.chat import ChatController
    from zenagent.app.commands import AppServices
    from zenagent.app.models import ModelManager
    from zenagent.app.sessions import SessionManager

    return AppServices(
        runtime=runtime,
        chat=ChatController(runtime),
        models=ModelManager(runtime),
        sessions=SessionManager(runtime),
    )


def read_skill_tool(runtime):
    return next(tool for tool in runtime.agent.tools if tool.name == "read_skill")


async def test_skills_command_sees_a_skill_installed_after_bootstrap(
    runtime_factory, skill_root
):
    runtime = await runtime_factory(skills=True)
    registry = build_default_commands(make_services(runtime))

    before = await registry.dispatch("skills", "")
    assert "no skills found" in before.message
    assert not any(tool.name == "read_skill" for tool in runtime.agent.tools)

    write_skill(skill_root, "late", description="installed mid-session")

    after = await registry.dispatch("skills", "")
    assert "- late: installed mid-session" in after.message
    assert after.message.splitlines()[0] == "skills 0 -> 1 (+late)"

    # The list and the tool must agree: the model can load it right away, and
    # the prompt index advertises it.
    result = await read_skill_tool(runtime).execute("c1", {"name": "late"}, None)
    assert not result.is_error
    assert "body" in result.content[0].text
    assert "late: installed mid-session" in runtime.agent.system_prompt


async def test_skills_command_reports_when_skills_are_disabled(runtime_factory, skill_root):
    runtime = await runtime_factory()  # the fixture's default: skills=False
    registry = build_default_commands(make_services(runtime))

    result = await registry.dispatch("skills", "")

    assert "disabled" in result.message
    assert runtime.refresh_skills() == []


async def test_refresh_skills_does_nothing_when_the_roots_are_unchanged(
    runtime_factory, skill_root
):
    write_skill(skill_root, "early")
    runtime = await runtime_factory(skills=True)
    tools = runtime.agent.tools
    prompt = runtime.agent.system_prompt

    assert runtime.refresh_skills() == []
    assert runtime.agent.tools == tools
    assert runtime.agent.system_prompt == prompt


async def test_editing_a_skill_body_reaches_the_tool(runtime_factory, skill_root):
    write_skill(skill_root, "late", body="old body")
    runtime = await runtime_factory(skills=True)
    assert "old body" in (await read_skill_tool(runtime).execute("c", {"name": "late"}, None)).content[0].text

    write_skill(skill_root, "late", body="new body")
    notes = runtime.refresh_skills()

    assert notes == ["skills 1 -> 1 (~late)"]
    body = (await read_skill_tool(runtime).execute("c", {"name": "late"}, None)).content[0].text
    assert "new body" in body
    assert "old body" not in body


async def test_reload_refuses_while_the_agent_is_streaming(
    runtime_factory, skill_root, monkeypatch
):
    runtime = await runtime_factory(skills=True)
    registry = build_default_commands(make_services(runtime))
    monkeypatch.setattr(type(runtime.agent), "is_streaming", property(lambda self: True))

    result = await registry.dispatch("reload", "")

    assert result.message == "cannot reload while the agent is working"
    write_skill(skill_root, "late")
    assert runtime.refresh_skills() == ["skills 0 -> 1 (+late); applies after this run"]
    assert runtime.skills == []  # the roster the model was given stays put
    assert not any(tool.name == "read_skill" for tool in runtime.agent.tools)


async def test_reload_picks_up_a_skill_added_after_bootstrap(runtime_factory, skill_root):
    runtime = await runtime_factory(skills=True)
    registry = build_default_commands(make_services(runtime))
    write_skill(skill_root, "late", description="added while running")

    result = await registry.dispatch("reload", "")

    assert result.message == "reloaded: skills 0 -> 1 (+late)"
    assert any(tool.name == "read_skill" for tool in runtime.agent.tools)
    loaded = await read_skill_tool(runtime).execute("c", {"name": "late"}, None)
    assert not loaded.is_error


async def test_reload_reports_an_edited_skill_not_only_a_new_one(
    runtime_factory, skill_root
):
    write_skill(skill_root, "late", body="old body")
    runtime = await runtime_factory(skills=True)
    registry = build_default_commands(make_services(runtime))
    write_skill(skill_root, "late", body="new body")

    result = await registry.dispatch("reload", "")

    assert result.message == "reloaded: skills 1 -> 1 (~late)"


async def test_reload_reports_an_edited_template(runtime_factory, tmp_path, monkeypatch):
    root = tmp_path / "templates"
    root.mkdir()
    template = root / "review.md"
    template.write_text(
        "---\ndescription: review a file\n---\nold body {{args}}\n", encoding="utf-8"
    )
    monkeypatch.setattr("zenagent.app.runtime.default_template_roots", lambda cwd: [root])
    runtime = await runtime_factory(templates=True)

    template.write_text(
        "---\ndescription: review a file\n---\nnew body {{args}}\n", encoding="utf-8"
    )
    notes = runtime.rediscover()

    assert "templates 1 -> 1 (~review)" in notes


async def test_reload_reports_nothing_changed(runtime_factory, skill_root):
    runtime = await runtime_factory(skills=True)
    registry = build_default_commands(make_services(runtime))

    result = await registry.dispatch("reload", "")

    assert result.message == "reloaded: nothing changed"


async def test_skills_command_keeps_the_note_when_the_last_skill_disappears(
    runtime_factory, skill_root
):
    write_skill(skill_root, "late")
    runtime = await runtime_factory(skills=True)
    registry = build_default_commands(make_services(runtime))

    import shutil

    shutil.rmtree(skill_root / "late")
    result = await registry.dispatch("skills", "")

    assert result.message.splitlines()[0] == "skills 1 -> 0 (-late)"
    assert "no skills found" in result.message


async def test_reload_does_not_duplicate_mcp_tools(runtime_factory, skill_root):
    from zenagent.agent.loop import AgentTool

    class StubMcp:
        def __init__(self):
            self.tools = [
                AgentTool(
                    name="mcp__echo__echo",
                    description="echo",
                    parameters={"type": "object", "properties": {}},
                )
            ]

        async def retry_failed(self, *, force: bool = False):
            return []

        def tool_defs(self):
            return list(self.tools)

        async def close(self):
            return None

    runtime = await runtime_factory(skills=True)
    runtime.mcp = StubMcp()

    runtime.rediscover()
    assert [tool.name for tool in runtime.agent.tools].count("mcp__echo__echo") == 1

    await runtime.refresh_mcp()
    assert [tool.name for tool in runtime.agent.tools].count("mcp__echo__echo") == 1


async def test_reload_delivers_extension_hooks_exactly_once_per_event(
    runtime_factory, tmp_path, skill_root, monkeypatch
):
    """The unsubscribe bookkeeping matters, so assert on delivery, not on a list."""
    from conftest import text_stream

    monkeypatch.setenv("EXT_HOOK_LOG", str(tmp_path / "hook.log"))
    extension = tmp_path / "ext.py"
    extension.write_text(
        """
import os


def register(api):
    def note(event):
        with open(os.environ["EXT_HOOK_LOG"], "a", encoding="utf-8") as handle:
            handle.write("x")

    api.on("agent_end", note)
""",
        encoding="utf-8",
    )
    runtime = await runtime_factory(
        extension_paths=[str(extension)], stream_fn=text_stream()
    )

    runtime.rediscover()  # re-executes ext.py: the old generation must go first
    runtime.rediscover()
    await runtime.agent.prompt("hi")
    await runtime.agent.wait_for_idle()

    assert (tmp_path / "hook.log").read_text(encoding="utf-8") == "x"


async def test_reload_keeps_the_todo_tool_exactly_once(runtime_factory, skill_root):
    runtime = await runtime_factory(skills=True)
    names = [tool.name for tool in runtime.agent.tools]
    assert names.count("todo") == 1

    runtime.rediscover()

    after = [tool.name for tool in runtime.agent.tools]
    assert after.count("todo") == 1
    assert runtime.todo_tool is not None


async def test_reload_failure_paths_return_messages_instead_of_raising(
    runtime_factory, skill_root, monkeypatch
):
    """A run that starts during the MCP retry must not take the TUI down."""
    runtime = await runtime_factory(skills=True)
    registry = build_default_commands(make_services(runtime))
    awaited: list[str] = []

    async def slow_refresh():
        # the run begins while /reload is waiting; then the guard trips
        awaited.append("refresh_mcp")
        monkeypatch.setattr(
            type(runtime.agent), "is_streaming", property(lambda self: True)
        )

    monkeypatch.setattr(runtime, "refresh_mcp", slow_refresh)
    result = await registry.dispatch("reload", "")

    # The message must come from the rediscover() guard, not the early return:
    # refresh_mcp had to be awaited first for this to be the real race.
    assert awaited == ["refresh_mcp"]
    assert result.message == "cannot reload while the agent is working"


async def test_mcp_command_does_not_refresh_tools_during_a_run(
    runtime_factory, skill_root, monkeypatch
):
    from zenagent.agent.loop import AgentTool

    class StubMcp:
        def __init__(self):
            self.refreshed = 0

        async def retry_failed(self, *, force: bool = False):
            self.refreshed += 1
            return []

        def tool_defs(self):
            return [
                AgentTool(
                    name="mcp__echo__echo",
                    description="echo",
                    parameters={"type": "object", "properties": {}},
                )
            ]

        @property
        def connected_names(self):
            return ["echo"]

        async def close(self):
            return None

    runtime = await runtime_factory()
    manager = StubMcp()
    runtime.mcp = manager
    registry = build_default_commands(make_services(runtime))
    monkeypatch.setattr(type(runtime.agent), "is_streaming", property(lambda self: True))

    result = await registry.dispatch("mcp", "")

    assert manager.refreshed == 0  # tools stay put under an in-flight request
    assert result.message == (
        "mcp connected: echo · tools not refreshed while the agent is working"
    )


async def test_reload_does_not_duplicate_extension_tools_or_hooks(
    runtime_factory, tmp_path, monkeypatch, skill_root
):
    extension = tmp_path / "ext.py"
    extension.write_text(EXTENSION, encoding="utf-8")
    runtime = await runtime_factory(extension_paths=[str(extension)])

    def extension_tools():
        return [tool.name for tool in runtime.agent.tools].count("ext_echo")

    assert extension_tools() == 1
    assert len(runtime._hook_unsubscribes) == 1

    runtime.rediscover()  # re-executes ext.py: the old generation must go first
    runtime.rediscover()

    assert extension_tools() == 1
    assert len(runtime._hook_unsubscribes) == 1
