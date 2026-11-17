"""Evidence stall guard: a re-read of the same bytes is not progress.

The guard compares *what a call asks the filesystem for*, not how the call is
spelled, so ``sed -n '1,100p' a.py`` and ``read_file a.py offset=0 limit=100``
are the same evidence. These tests pin the coverage bookkeeping and the
read-shaped shell idioms; ``test_loop`` covers the corrective itself.
"""

from zenagent.agent.guard import (
    LOOP_GUARD_PREFIX,
    STALL_GUARD_PREFIX,
    EvidenceLedger,
    is_stall_guard_message,
    probe_for,
)
from zenagent.types import ToolCall, UserMessage


def read(path: str, offset: int = 0, limit: int | None = None) -> ToolCall:
    arguments: dict = {"path": path, "offset": offset}
    if limit is not None:
        arguments["limit"] = limit
    return ToolCall(id="c1", name="read_file", arguments=arguments)


def bash(command: str) -> ToolCall:
    return ToolCall(id="c1", name="bash", arguments={"command": command})


def test_repeated_page_is_a_stall():
    ledger = EvidenceLedger()

    assert ledger.observe([read("a.py", 0, 100)]) == (False, [])
    stalled, labels = ledger.observe([read("a.py", 0, 100)])

    assert stalled is True
    assert labels == ["a.py:0-100"]


def test_the_next_page_is_progress():
    ledger = EvidenceLedger()

    ledger.observe([read("a.py", 0, 100)])

    assert ledger.observe([read("a.py", 100, 100)]) == (False, [])


def test_only_the_uncovered_part_has_to_be_new():
    ledger = EvidenceLedger()
    ledger.observe([read("a.py", 0, 100)])

    # 50-150 overlaps the first page but still carries 50 unseen lines.
    assert ledger.observe([read("a.py", 50, 100)]) == (False, [])
    # The union now covers 0-150.
    assert ledger.observe([read("a.py", 0, 150)])[0] is True


def test_shell_and_tool_reads_share_coverage():
    ledger = EvidenceLedger()
    ledger.observe([bash("cd repo && sed -n '1,100p' a.py")])

    stalled, labels = ledger.observe([read("a.py", 0, 100)])

    assert stalled is True
    assert labels == ["a.py:0-100"]


def test_a_batch_with_one_new_read_is_progress():
    ledger = EvidenceLedger()
    ledger.observe([read("a.py", 0, 100)])

    stalled, _ = ledger.observe([read("a.py", 0, 100), read("b.py", 0, 100)])

    assert stalled is False


def test_same_grep_twice_is_a_stall_but_a_new_pattern_is_not():
    ledger = EvidenceLedger()

    def grep(pattern: str, path: str = "zenagent") -> ToolCall:
        return ToolCall(
            id="c1", name="grep", arguments={"pattern": pattern, "path": path}
        )

    assert ledger.observe([grep("image")]) == (False, [])
    assert ledger.observe([grep("image")])[0] is True
    assert ledger.observe([grep("ImageContent")]) == (False, [])


def test_a_different_path_is_a_different_query():
    ledger = EvidenceLedger()

    def grep(path: str) -> ToolCall:
        return ToolCall(
            id="c1", name="grep", arguments={"pattern": "image", "path": path}
        )

    ledger.observe([grep("zenagent")])

    assert ledger.observe([grep("tests")]) == (False, [])


def test_editing_a_file_makes_its_old_pages_stale():
    ledger = EvidenceLedger()
    ledger.observe([read("a.py", 0, 100)])
    ledger.observe([ToolCall(id="c1", name="edit_file", arguments={"path": "a.py"})])

    assert ledger.observe([read("a.py", 0, 100)]) == (False, [])


def test_a_mutation_only_batch_is_never_stalled():
    ledger = EvidenceLedger()

    stalled, labels = ledger.observe(
        [ToolCall(id="c1", name="write_file", arguments={"path": "a.py"})]
    )

    assert stalled is False
    assert labels == []


def test_polls_tests_and_edits_request_no_evidence():
    assert probe_for(bash("sleep 2")) is None
    assert probe_for(bash("uv run pytest -q")) is None
    assert (
        probe_for(ToolCall(id="c1", name="wait", arguments={"job_id": "bg_1"})) is None
    )


def test_a_flag_is_never_taken_for_a_path():
    assert probe_for(bash("cat -n a.py")) is None
    assert probe_for(bash("head -50")) is None
    # ``grep ... | head -50`` is a grep, not a read of a file called "0".
    probe = probe_for(bash('grep -ril "x" --include=*.py . | head -50'))
    assert probe is not None and probe.kind == "query"


def test_git_show_only_counts_immutable_commits():
    assert probe_for(bash("git show 4e3a57b")) is not None
    assert (
        probe_for(bash("git show 4e3a57b:docs/architecture/allowlist.json")) is not None
    )
    # HEAD and branch names move; re-showing them can return new bytes.
    assert probe_for(bash("git show HEAD:docs/architecture/allowlist.json")) is None
    assert probe_for(bash("git show main")) is None


def test_grep_flags_are_not_mistaken_for_the_search_root():
    probe = probe_for(bash('grep -rn "image" --include=*.py zenagent/'))
    assert probe is not None and probe.target.endswith("\x00zenagent")

    probe = probe_for(bash('grep -n -A 5 "image" a.py'))
    assert probe is not None and probe.target.endswith("\x00a.py")


def test_cat_with_a_redirect_is_a_write_not_a_read():
    assert probe_for(bash("cat > /tmp/out.txt")) is None


def test_the_guard_message_is_recognized_by_its_prefix():
    assert is_stall_guard_message(
        UserMessage(content=STALL_GUARD_PREFIX + " x", timestamp=0)
    )
    assert not is_stall_guard_message(
        UserMessage(content=LOOP_GUARD_PREFIX + " x", timestamp=0)
    )
    assert not is_stall_guard_message("plain text")
