"""Completion: slash commands at line start, @path tokens after whitespace."""

from pathlib import Path

from prompt_toolkit.document import Document

from zenagent.cli.completion import SLASH_COMMANDS, ReplCompleter, help_text


def complete(completer: ReplCompleter, text: str) -> list[str]:
    document = Document(text, cursor_position=len(text))
    return [c.text for c in completer.get_completions(document, None)]


def test_slash_prefix_completes_commands():
    completer = ReplCompleter(base_dir=Path("/tmp"))
    assert complete(completer, "/mo") == ["/mode", "/model"]
    assert set(complete(completer, "/se")) == {"/session", "/sessions"}


def test_bare_slash_lists_every_command():
    completer = ReplCompleter(base_dir=Path("/tmp"))
    got = complete(completer, "/")
    assert set(got) == set(SLASH_COMMANDS)


def test_plain_text_has_no_completions():
    completer = ReplCompleter(base_dir=Path("/tmp"))
    assert complete(completer, "hello there") == []


def test_at_token_completes_files_in_cwd(tmp_path):
    (tmp_path / "alpha.py").write_text("x")
    (tmp_path / "beta.py").write_text("x")
    (tmp_path / "pkg").mkdir()
    completer = ReplCompleter(base_dir=tmp_path)

    assert complete(completer, "@al") == ["@alpha.py"]
    # directories sort first, then files alphabetically
    assert complete(completer, "@") == ["@pkg/", "@alpha.py", "@beta.py"]


def test_at_token_in_middle_of_a_word_does_not_trigger(tmp_path):
    (tmp_path / "alpha.py").write_text("x")
    completer = ReplCompleter(base_dir=tmp_path)
    assert complete(completer, "mail me at me@al") == []


def test_nested_directory_completion(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x")
    completer = ReplCompleter(base_dir=tmp_path)
    assert complete(completer, "@pkg/") == ["@pkg/mod.py"]
    assert complete(completer, "@pkg/mo") == ["@pkg/mod.py"]


def test_hidden_files_are_not_offered(tmp_path):
    (tmp_path / ".secret").write_text("x")
    (tmp_path / "visible.txt").write_text("x")
    completer = ReplCompleter(base_dir=tmp_path)
    assert complete(completer, "@") == ["@visible.txt"]


def test_help_text_is_generated_from_the_command_table():
    text = help_text()
    for name in SLASH_COMMANDS:
        assert name in text
    assert "steering" in text


def test_mode_arguments_are_completed():
    completer = ReplCompleter(base_dir=Path("/tmp"))
    assert complete(completer, "/mode ") == ["plan", "ask", "full"]
    assert complete(completer, "/mode p") == ["plan"]
    assert complete(completer, "/mode f") == ["full"]


def test_thinking_arguments_are_completed():
    completer = ReplCompleter(base_dir=Path("/tmp"))
    assert complete(completer, "/thinking ") == [
        "off",
        "low",
        "medium",
        "high",
        "max",
    ]
    assert complete(completer, "/thinking h") == ["high"]


def test_theme_arguments_are_completed():
    completer = ReplCompleter(base_dir=Path("/tmp"))
    assert complete(completer, "/theme ") == ["default", "mono", "solarized"]


def test_commands_without_argument_values_complete_nothing():
    completer = ReplCompleter(base_dir=Path("/tmp"))
    assert complete(completer, "/help ") == []
    assert complete(completer, "/nope p") == []


def test_extra_values_supply_dynamic_model_ids():
    completer = ReplCompleter(
        base_dir=Path("/tmp"),
        extra_values={"/model": [("deepseek-flash", "ready")]},
    )
    assert complete(completer, "/model ") == ["deepseek-flash"]


# ---------------------------------------------------------------------------
# Embedded skill tokens + typed slash entries (0.4.x)
# ---------------------------------------------------------------------------


def complete_items(completer: ReplCompleter, text: str) -> list[tuple[str, str]]:
    document = Document(text, cursor_position=len(text))
    return [
        (completion.text, completion.display_meta_text)
        for completion in completer.get_completions(document, None)
    ]


def skill_list():
    return [("pdf-report", "Build reports"), ("review", "Review a diff")]


def test_line_start_slash_lists_skill_commands_with_meta():
    completer = ReplCompleter(base_dir=Path("/tmp"), skill_provider=skill_list)
    items = complete_items(completer, "/skill:p")
    assert items == [("/skill:pdf-report", "skill · Build reports")]

    bare = complete(completer, "/")
    assert "/skill:pdf-report" in bare and "/skill:review" in bare


def test_typed_commands_carry_a_type_label():
    completer = ReplCompleter(
        base_dir=Path("/tmp"),
        typed_commands=[
            ("/review", "Review a file carefully", "template"),
            ("/deploy", "Deploy the service", "extension"),
        ],
    )
    items = dict(complete_items(completer, "/re"))
    assert items["/review"] == "template · Review a file carefully"
    items = dict(complete_items(completer, "/dep"))
    assert items["/deploy"] == "extension · Deploy the service"


def test_builtin_commands_are_tagged_as_command():
    completer = ReplCompleter(base_dir=Path("/tmp"))
    items = dict(complete_items(completer, "/mo"))
    assert items["/mode"] == "command · set runtime mode: plan|ask|full (plan = read-only)"


def test_mid_sentence_slash_offers_only_skills():
    completer = ReplCompleter(base_dir=Path("/tmp"), skill_provider=skill_list)
    # "/p" is a skill-name prefix; "/sk" is the /skill: command prefix, so it
    # lists the whole skill namespace (never commands).
    assert complete(completer, "please /p") == ["/skill:pdf-report"]
    assert set(complete(completer, "please /sk")) == {
        "/skill:pdf-report",
        "/skill:review",
    }


def test_mid_sentence_skill_prefix_filters_names():
    completer = ReplCompleter(base_dir=Path("/tmp"), skill_provider=skill_list)
    assert complete(completer, "look /skill:re") == ["/skill:review"]


def test_mid_sentence_other_command_suppresses_slash_completion():
    completer = ReplCompleter(base_dir=Path("/tmp"), skill_provider=skill_list)
    assert complete(completer, "/mode /sk") == []
    assert complete(completer, "hello there") == []


def test_skill_provider_is_read_live():
    skills = [("pdf-report", "Build reports")]
    completer = ReplCompleter(
        base_dir=Path("/tmp"), skill_provider=lambda: list(skills)
    )
    assert complete(completer, "/skill:") == ["/skill:pdf-report"]
    skills.append(("review", "Review a diff"))
    assert set(complete(completer, "/skill:")) == {
        "/skill:pdf-report",
        "/skill:review",
    }
