"""Prompt history: persisted prompts and Ctrl+R search/recall."""

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.history import PromptHistory
from zenagent.cli.tui.screens.modals import HistorySearch
from zenagent.cli.tui.widgets.prompt_input import PromptInput


def test_history_roundtrips_and_skips_blank_and_duplicate(tmp_path):
    path = tmp_path / "hist.jsonl"
    history = PromptHistory(path)
    history.add("one")
    history.add("")
    history.add("one")
    history.add("two")

    assert PromptHistory(path).entries() == ["one", "two"]


def test_search_is_newest_first_and_case_insensitive(tmp_path):
    history = PromptHistory(tmp_path / "h.jsonl")
    for text in ("fix login", "Fix LOGIN bug", "other"):
        history.add(text)

    assert history.search("fix") == ["Fix LOGIN bug", "fix login"]


def test_corrupt_history_lines_are_skipped(tmp_path):
    path = tmp_path / "hist.jsonl"
    path.write_text('{"text": "ok"}\nnot json\n', encoding="utf-8")

    assert PromptHistory(path).entries() == ["ok"]


def test_multiline_prompts_roundtrip(tmp_path):
    path = tmp_path / "hist.jsonl"
    history = PromptHistory(path)
    history.add("line1\nline2")

    assert PromptHistory(path).entries() == ["line1\nline2"]


async def test_ctrl_r_searches_and_recalls_a_prompt(tmp_path, fake_runtime):
    app = ZenAgentApp(fake_runtime, history_path=tmp_path / "hist.jsonl")
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.focus()
        prompt.insert("hello world")
        await pilot.press("enter")
        await app.chat.wait_for_idle()
        await pilot.pause()

        await pilot.press("ctrl+r")
        await pilot.pause()
        assert isinstance(app.screen, HistorySearch)

        for key in ("h", "e", "l", "l"):  # focus is in the search box
            await pilot.press(key)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert prompt.text == "hello world"
