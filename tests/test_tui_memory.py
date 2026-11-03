"""Memory manager/editor modals: list, add/edit via MemoryEditor, delete via Confirm.

Modal screens sit on top of the screen stack, so queries go through
``app.screen`` (the repo-wide idiom, see tests/test_tui_approval.py).
"""

from textual.widgets import Input, OptionList, TextArea

from zenagent.agent.memory_store import MemoryStore
from zenagent.cli.tui.app import ZenAgentApp


def seeded_store(tmp_path, count=2):
    store = MemoryStore(tmp_path)
    for index in range(count):
        store.write_entry(title=f"条目{index}", body=f"正文{index}", source="test")
    return store


async def test_dispatch_memory_opens_manager_listing_entries(fake_runtime, tmp_path):
    app = ZenAgentApp(fake_runtime)
    app.runtime.memory_store = seeded_store(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("memory", "")
        await pilot.pause()
        manager = app.screen
        options = list(manager.query_one("#memory-list", OptionList).options)
        assert len(options) == 2
        ids = {entry.id for entry in app.runtime.memory_store.list_entries()}
        assert all(any(entry_id in str(option.prompt) for entry_id in ids) for option in options)


async def test_editor_saves_new_entry_with_ui_source(fake_runtime, tmp_path):
    app = ZenAgentApp(fake_runtime)
    store = MemoryStore(tmp_path)
    app.runtime.memory_store = store
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_memory_editor(title_prefill="标题X")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#memory-title", Input).value = "标题X"
        screen.query_one("#memory-body", TextArea).text = "正文Y"
        await pilot.press("ctrl+s")
        await pilot.pause()
        saved = store.list_entries()
        assert len(saved) == 1
        assert (saved[0].title, saved[0].body, saved[0].source) == ("标题X", "正文Y", "ui")


async def test_editor_escape_cancels_without_writing(fake_runtime, tmp_path):
    app = ZenAgentApp(fake_runtime)
    store = MemoryStore(tmp_path)
    app.runtime.memory_store = store
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_memory_editor(title_prefill="X")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#memory-title", Input).value = "不该保存"
        await pilot.press("escape")
        await pilot.pause()
        assert store.list_entries() == []


async def test_manager_delete_confirms_then_removes(fake_runtime, tmp_path):
    app = ZenAgentApp(fake_runtime)
    store = seeded_store(tmp_path, count=1)
    app.runtime.memory_store = store
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_memory_manager()
        await pilot.pause()
        entry = store.list_entries()[0]
        await pilot.press("x")          # Confirm pops up
        await pilot.pause()
        await pilot.press("y")          # confirm
        await pilot.pause()
        assert store.read_entry(entry.id) is None
        # Empty library renders the disabled placeholder, not zero options
        # (the plan's pseudocode pins this; same pattern as TodoEditor).
        manager = app.screen
        options = list(manager.query_one("#memory-list", OptionList).options)
        assert len(options) == 1 and options[0].disabled


async def test_manager_refresh_reflects_external_writes(fake_runtime, tmp_path):
    app = ZenAgentApp(fake_runtime)
    store = MemoryStore(tmp_path)
    app.runtime.memory_store = store
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_memory_manager()
        await pilot.pause()
        store.write_entry(title="外部写入", body="B", source="agent")  # model wrote underneath
        app.screen._refresh()
        await pilot.pause()
        options = list(app.screen.query_one("#memory-list", OptionList).options)
        assert len(options) == 1 and "外部写入" in str(options[0].prompt)
