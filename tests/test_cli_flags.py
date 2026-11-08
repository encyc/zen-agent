"""Runtime flags are declared once: ``cli/flags.py`` is the single home for
the shared zen runtime flag set; both the CLI and ``zen rpc`` build their
parsers from it. One declaration, two parsers, zero drift."""

import argparse

from zenagent.cli.flags import add_runtime_flags

SHARED_FLAGS = [
    "--model",
    "--yes",
    "--mode",
    "--read-only",
    "--thinking",
    "--max-turns",
    "--max-cost",
    "--retries",
    "--resume",
    "--no-mcp",
    "--mcp-file",
    "--mcp-server",
    "--no-skills",
    "--no-templates",
    "--no-auto-compact",
    "--extension",
    "--no-context-files",
    "--trace",
    "--metrics",
    "--cwd",
    "--allow-dangerous",
    "--allow-outside",
    "--allow-destructive",
    "--allow-protected",
]


def _decls(parser: argparse.ArgumentParser) -> dict:
    out = {}
    for action in parser._actions:
        if action.option_strings and action.option_strings[-1] in SHARED_FLAGS:
            out[action.option_strings[-1]] = (
                type(action).__name__,
                action.default,
                getattr(action, "nargs", None),
                getattr(action, "const", "<none>"),
                action.dest,
            )
    return out


def test_flags_module_declares_the_shared_set():
    bare = argparse.ArgumentParser()
    add_runtime_flags(bare)
    got = _decls(bare)
    assert set(got) == set(SHARED_FLAGS), f"missing: {set(SHARED_FLAGS) - set(got)}"


def test_both_parsers_match_the_single_declaration():
    from zenagent.cli.flags import build_rpc_parser_stub
    from zenagent.cli.main import build_parser

    canonical = _decls(build_rpc_parser_stub())
    assert _decls(build_parser()) == canonical
    assert set(canonical) == set(SHARED_FLAGS)


def test_thinking_choices_live_in_flags():
    from zenagent.cli import flags

    assert "off" in flags.THINKING_CHOICES
