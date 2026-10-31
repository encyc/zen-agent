"""Pure helpers for user-side /skill:<name> invocation."""

from pathlib import Path

from zenagent.app.skill_commands import (
    SKILL_COMMAND_PREFIX,
    build_skill_submission,
    load_skill_body,
    parse_skill_display,
    parse_skill_invocation,
    render_skill_prompt,
    skill_command_name,
)
from zenagent.skills import MAX_SKILL_BODY_BYTES, Skill


def make_skill(
    tmp_path: Path, name: str = "pdf-report", body: str = "Do the thing."
) -> Skill:
    directory = tmp_path / name
    directory.mkdir()
    path = directory / "SKILL.md"
    path.write_text(f"---\nname: {name}\ndescription: Build reports\n---\n{body}\n")
    return Skill(name=name, description="Build reports", path=path, body=body)


def test_command_name_and_prefix():
    skill = Skill(name="pdf-report", description="d", path=Path("/x"), body="")
    assert skill_command_name(skill) == "skill:pdf-report"
    assert SKILL_COMMAND_PREFIX == "skill:"


def test_parse_line_start_form():
    invocation = parse_skill_invocation("/skill:pdf-report review report.csv")
    assert invocation is not None
    assert invocation.name == "pdf-report"
    assert invocation.args == "review report.csv"
    assert (invocation.start, invocation.end) == (0, len("/skill:pdf-report"))
    assert invocation.prompt == "/skill:pdf-report review report.csv"


def test_parse_line_start_without_args():
    invocation = parse_skill_invocation("  /skill:pdf-report  ")
    assert invocation is not None
    assert invocation.name == "pdf-report"
    assert invocation.args == ""


def test_parse_line_start_keeps_multiline_args():
    invocation = parse_skill_invocation("/skill:pdf-report line one\nline two")
    assert invocation is not None
    assert invocation.args == "line one\nline two"


def test_parse_embedded_token_turns_prose_into_args():
    invocation = parse_skill_invocation("please /skill:pdf-report review this file")
    assert invocation is not None
    assert invocation.name == "pdf-report"
    assert invocation.args == "please review this file"
    assert invocation.prompt == "please /skill:pdf-report review this file"


def test_parse_embedded_token_at_end_keeps_before_text():
    invocation = parse_skill_invocation("please review /skill:pdf-report")
    assert invocation is not None
    assert invocation.args == "please review"


def test_parse_only_first_embedded_token():
    invocation = parse_skill_invocation("a /skill:one b /skill:two c")
    assert invocation is not None
    assert invocation.name == "one"
    assert invocation.args == "a b /skill:two c"


def test_parse_rejects_plain_text_and_other_commands():
    assert parse_skill_invocation("just a prompt") is None
    assert parse_skill_invocation("/mode plan") is None
    assert parse_skill_invocation("/skills") is None
    assert parse_skill_invocation("foo/skill:x") is None
    assert parse_skill_invocation("/skill:") is None


def test_render_skill_prompt_shape(tmp_path):
    skill = make_skill(tmp_path)
    rendered = render_skill_prompt(skill, "Do the thing.", "review this")
    lines = rendered.splitlines()
    assert lines[0] == "[skill:pdf-report] review this"
    assert '[IMPORTANT: User invoked the "pdf-report" skill' in rendered
    assert "Do the thing." in rendered
    assert f"[Skill directory: {skill.path.parent.resolve()}]" in rendered
    assert rendered.rstrip().endswith("User: review this")


def test_render_skill_prompt_clips_the_header_projection(tmp_path):
    skill = make_skill(tmp_path)
    rendered = render_skill_prompt(skill, "body", "x" * 500)
    header = rendered.splitlines()[0]
    assert header.startswith("[skill:pdf-report] x")
    assert header.endswith("…")
    assert len(header) < 260


def test_render_skill_prompt_marks_truncated_bodies(tmp_path):
    skill = make_skill(tmp_path)
    rendered = render_skill_prompt(skill, "body", "", truncated=True)
    assert f"[skill body truncated at {MAX_SKILL_BODY_BYTES} bytes]" in rendered


def test_parse_display_marker():
    display = parse_skill_display(
        "[skill:pdf-report] review this\n\n[IMPORTANT: x]\nbody"
    )
    assert display is not None
    assert display.name == "pdf-report"
    assert display.args == "review this"
    assert display.body.startswith("[IMPORTANT: x]")
    assert parse_skill_display("just a message") is None


def test_load_skill_body_rereads_the_file_and_strips_frontmatter(tmp_path):
    skill = make_skill(tmp_path)
    skill.path.write_text("---\nname: pdf-report\n---\nUpdated body.\n")
    body, truncated = load_skill_body(skill)
    assert body == "Updated body."
    assert truncated is False


def test_load_skill_body_falls_back_to_discovered_body(tmp_path):
    skill = make_skill(tmp_path)
    skill.path.unlink()
    body, truncated = load_skill_body(skill)
    assert body == "Do the thing."
    assert truncated is False


def test_load_skill_body_is_capped(tmp_path):
    skill = make_skill(tmp_path, body="x" * (MAX_SKILL_BODY_BYTES + 100))
    body, truncated = load_skill_body(skill)
    assert truncated is True
    assert len(body.encode()) <= MAX_SKILL_BODY_BYTES


def test_build_skill_submission_resolves_and_injects(tmp_path):
    skill = make_skill(tmp_path)
    resolution = build_skill_submission("/skill:pdf-report go", [skill])
    assert resolution is not None
    assert resolution.error is None
    assert resolution.submission is not None
    assert resolution.submission.name == "pdf-report"
    assert resolution.submission.args == "go"
    assert "Do the thing." in resolution.submission.prompt


def test_build_skill_submission_unknown_lists_available(tmp_path):
    skill = make_skill(tmp_path)
    resolution = build_skill_submission("/skill:nope", [skill])
    assert resolution is not None
    assert resolution.submission is None
    assert resolution.error is not None
    assert "unknown skill: nope" in resolution.error
    assert "pdf-report" in resolution.error


def test_build_skill_submission_ignores_non_invocations_and_reserved(tmp_path):
    skill = make_skill(tmp_path)
    assert build_skill_submission("hello", [skill]) is None
    # A command/template that owns the full skill: name wins.
    assert (
        build_skill_submission(
            "/skill:pdf-report", [skill], reserved_names={"skill:pdf-report"}
        )
        is None
    )
