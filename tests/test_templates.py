"""Prompt templates: discovery, expansion, and REPL integration."""

import asyncio
from pathlib import Path

from pyagent.templates import (
    PromptTemplate,
    default_template_roots,
    discover_templates,
    expand,
)


def write(root: Path, name: str, text: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_expand_replaces_args_placeholder():
    assert expand("Review {{args}} carefully", "src/x.py") == "Review src/x.py carefully"


def test_expand_uses_default_when_no_argument():
    assert expand("Deploy to {{args:staging}}", "") == "Deploy to staging"
    assert expand("Deploy to {{args:staging}}", "prod") == "Deploy to prod"


def test_expand_appends_argument_without_a_placeholder():
    assert expand("Summarize the diff.", "HEAD~1") == "Summarize the diff.\n\nHEAD~1"
    assert expand("Summarize the diff.", "") == "Summarize the diff."


def test_discover_uses_frontmatter_and_falls_back_to_filename(tmp_path):
    write(tmp_path, "review", "---\ndescription: Review a file\n---\nReview {{args}}")
    write(tmp_path, "explain", "Explain the code at {{args}}.\n")
    templates, warnings = discover_templates([tmp_path])
    names = {template.name for template in templates}
    assert names == {"review", "explain"}
    review = next(t for t in templates if t.name == "review")
    assert review.description == "Review a file"
    explain = next(t for t in templates if t.name == "explain")
    assert explain.description.startswith("Explain the code")
    assert warnings == []


def test_project_root_overrides_user_root(tmp_path):
    project = tmp_path / "project"
    user = tmp_path / "user"
    write(project, "review", "---\ndescription: project\n---\nA")
    write(user, "review", "---\ndescription: user\n---\nB")
    templates, warnings = discover_templates([project, user])
    assert len(templates) == 1
    assert templates[0].description == "project"
    assert warnings and "duplicate template" in warnings[0]


def test_broken_frontmatter_is_skipped(tmp_path):
    write(tmp_path, "broken", "---\ndescription: x\nno closing fence\n")
    templates, warnings = discover_templates([tmp_path])
    assert templates == []
    assert warnings and "not closed" in warnings[0]


def test_default_roots_put_the_project_first(tmp_path):
    roots = default_template_roots(tmp_path)
    assert roots[0] == tmp_path / ".pyagent" / "commands"
    assert roots[1].name == "commands"


# ---------------------------------------------------------------------------
# REPL integration
# ---------------------------------------------------------------------------


def test_repl_slash_command_expands_a_template(tmp_path, monkeypatch):
    import pyagent.agent.session as session_module
    import pyagent.cli.repl as repl_module
    from pyagent.cli.repl import repl
    from pyagent.config import UserConfig

    monkeypatch.setattr(session_module, "sessions_dir", lambda: tmp_path / "sessions")
    monkeypatch.setattr(repl_module, "load_user_config", lambda: UserConfig())
    monkeypatch.chdir(tmp_path)
    write(tmp_path / ".pyagent" / "commands", "review", "Review {{args}} with care.")

    requests: list[list] = []

    async def stream(model, messages, tools, options):
        from pyagent.events import AssistantEvent
        from pyagent.types import AssistantMessage, TextContent, Usage

        requests.append(list(messages))
        partial = AssistantMessage(
            content=[TextContent(text="ok")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="stop",
            timestamp=0,
        )
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    async def input_fn() -> str:
        await asyncio.sleep(0.01)
        return "/review src/x.py" if not requests else "/exit"

    code = asyncio.run(
        repl(
            "glm-5.3",
            stream_fn=stream,
            input_fn=input_fn,
            print_fn=lambda *a, **k: None,
            api_key="k",
        )
    )
    assert code == 0
    user_message = requests[0][1]
    assert user_message.content == "Review src/x.py with care."


def test_repl_help_lists_templates():
    from pyagent.agent import Agent
    from pyagent.cli.repl import handle_slash

    agent = Agent.__new__(Agent)  # handle_slash only needs attributes for /help
    lines: list[str] = []
    asyncio.run(
        handle_slash(
            agent,
            "help",
            "",
            print_fn=lambda *a, **k: lines.append("".join(str(x) for x in a)),
            template_list=[
                PromptTemplate(name="review", description="Review a file", path=Path("/x"), body="")
            ],
        )
    )
    assert any("/review" in line for line in lines)
