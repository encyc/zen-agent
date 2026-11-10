"""Bound tool instances resolve relative paths under their own cwd."""

import sys

from zenagent.tools import base_tools
from zenagent.tools.ask import build_ask_tool


def _tool(tools, name):
    return next(tool for tool in tools if tool.name == name)


def _ask():
    return build_ask_tool(lambda: None)


async def test_fs_tools_resolve_under_the_bound_cwd(tmp_path):
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    (alpha / "note.txt").write_text("alpha note\n", encoding="utf-8")
    (beta / "note.txt").write_text("beta note\n", encoding="utf-8")

    alpha_read = await _tool(base_tools(_ask(), cwd=alpha), "read_file").execute(
        "c1", {"path": "note.txt"}, None, None
    )
    beta_read = await _tool(base_tools(_ask(), cwd=beta), "read_file").execute(
        "c1", {"path": "note.txt"}, None, None
    )

    assert alpha_read.content[0].text == "alpha note\n"
    assert beta_read.content[0].text == "beta note\n"


async def test_search_tools_default_to_the_bound_cwd(tmp_path):
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    (alpha / "one.py").write_text("alpha marker\n", encoding="utf-8")
    (beta / "two.py").write_text("beta marker\n", encoding="utf-8")

    alpha_tools = base_tools(_ask(), cwd=alpha)
    globbed = await _tool(alpha_tools, "glob").execute(
        "c1", {"pattern": "*.py"}, None, None
    )
    grepped = await _tool(alpha_tools, "grep").execute(
        "c1", {"pattern": "marker"}, None, None
    )

    assert "one.py" in globbed.content[0].text
    assert "two.py" not in globbed.content[0].text
    assert "alpha marker" in grepped.content[0].text
    assert "beta marker" not in grepped.content[0].text


async def test_bash_defaults_to_the_bound_cwd(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    command = (
        f'{sys.executable} -c "'
        "import pathlib; print(pathlib.Path.cwd().resolve())"
        '"'
    )

    result = await _tool(base_tools(_ask(), cwd=root), "bash").execute(
        "c1", {"command": command}, None, None
    )

    assert result.content[0].text.strip().endswith(str(root.resolve()))


async def test_bash_relative_cwd_arguments_rebase_on_the_bound_cwd(tmp_path):
    root = tmp_path / "project"
    sub = root / "sub"
    sub.mkdir(parents=True)
    command = (
        f'{sys.executable} -c "'
        "import pathlib; print(pathlib.Path.cwd().resolve())"
        '"'
    )

    result = await _tool(base_tools(_ask(), cwd=root), "bash").execute(
        "c1", {"command": command, "cwd": "sub"}, None, None
    )

    assert result.content[0].text.strip().endswith(str(sub.resolve()))
