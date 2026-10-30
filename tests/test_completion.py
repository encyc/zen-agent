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
