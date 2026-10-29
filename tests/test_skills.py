"""Skills: frontmatter parsing, discovery order, the read_skill tool, prompt index."""

from pathlib import Path

from zenagent.skills import (
    MAX_INDEX_BYTES,
    Skill,
    build_skill_tool,
    default_skill_roots,
    discover_skills,
    skills_prompt_index,
)


def make_skill(root: Path, directory: str, text: str) -> Path:
    path = root / directory / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_discovers_frontmatter_name_and_description(tmp_path):
    make_skill(
        tmp_path,
        "pdf",
        "---\nname: pdf-report\ndescription: Build a PDF report\n---\n\nStep one.\n",
    )
    skills, warnings = discover_skills([tmp_path])
    assert warnings == []
    assert [skill.name for skill in skills] == ["pdf-report"]
    assert skills[0].description == "Build a PDF report"
    assert skills[0].body.strip() == "Step one."


def test_falls_back_to_directory_name_and_first_body_line(tmp_path):
    make_skill(tmp_path, "deploy", "# Deploy\n\nPush to production carefully.\n")
    skills, warnings = discover_skills([tmp_path])
    assert warnings == []
    assert skills[0].name == "deploy"
    assert "Push to production" in skills[0].description


def test_unclosed_frontmatter_is_reported_and_skipped(tmp_path):
    make_skill(tmp_path, "broken", "---\nname: broken\nno closing fence\n")
    skills, warnings = discover_skills([tmp_path])
    assert skills == []
    assert warnings and "not closed" in warnings[0]


def test_bad_frontmatter_line_is_reported(tmp_path):
    make_skill(tmp_path, "bad", "---\njust a line without colon\n---\nbody\n")
    skills, warnings = discover_skills([tmp_path])
    assert skills == []
    assert warnings and "bad frontmatter line" in warnings[0]


def test_project_root_wins_over_user_root(tmp_path):
    project = tmp_path / "project"
    user = tmp_path / "user"
    make_skill(project, "one", "---\nname: shared\ndescription: project version\n---\n")
    make_skill(user, "one", "---\nname: shared\ndescription: user version\n---\n")
    skills, warnings = discover_skills([project, user])
    assert len(skills) == 1
    assert skills[0].description == "project version"
    assert warnings and "duplicate skill" in warnings[0]


def test_directories_without_skill_md_are_ignored(tmp_path):
    (tmp_path / "not-a-skill").mkdir()
    (tmp_path / "not-a-skill" / "README.md").write_text("hi")
    assert discover_skills([tmp_path]) == ([], [])


def test_read_skill_tool_returns_the_body_and_rejects_unknown_names():
    import asyncio

    skills = [
        Skill(name="deploy", description="d", path=Path("/x/SKILL.md"), body="do it"),
    ]
    tool = build_skill_tool(skills)
    result = asyncio.run(tool.execute("c1", {"name": "deploy"}, None))
    assert result.is_error is False
    assert result.content[0].text == "do it"
    assert result.details["skill"] == "deploy"

    missing = asyncio.run(tool.execute("c2", {"name": "ghost"}, None))
    assert missing.is_error is True
    assert "deploy" in missing.content[0].text


def test_prompt_index_lists_skills_and_is_bounded():
    skills = [
        Skill(name=f"s{i}", description="x" * 60, path=Path("/x"), body="")
        for i in range(200)
    ]
    index = skills_prompt_index(skills, max_bytes=400)
    assert "s0" in index
    assert "more skills omitted" in index
    assert len(index.encode()) <= 400 + 100


def test_empty_skills_produce_no_index():
    assert skills_prompt_index([]) == ""
    assert MAX_INDEX_BYTES > 0


def test_default_roots_put_the_project_first(tmp_path):
    roots = default_skill_roots(tmp_path)
    assert roots[0] == tmp_path / ".zenagent" / "skills"
    assert roots[1].name == "skills"
