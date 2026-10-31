"""Artifact store: oversized tool results spilled to disk and read back.

The store is deliberately dumb and best-effort: ``save`` returns a path or
``None`` (never raises), writes atomically, and prunes the oldest files when the
per-session retention caps are exceeded.
"""

from pathlib import Path

from zenagent.agent.artifacts import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_FILES,
    ArtifactStore,
    build_artifact_sink,
)


def _store(tmp_path: Path, **kwargs) -> ArtifactStore:
    return ArtifactStore(tmp_path / "sessions" / "abc.jsonl", **kwargs)


def _files(store: ArtifactStore) -> list[Path]:
    return sorted(store.directory.glob("*"))


def test_store_writes_under_the_session_artifact_directory(tmp_path):
    store = _store(tmp_path)
    path = store.save("bash", "hello artifact")

    assert path is not None
    assert path.is_absolute()
    assert path.parent == tmp_path / "sessions" / "artifacts" / "abc"
    assert path.read_text(encoding="utf-8") == "hello artifact"
    assert path.name.startswith("bash")


def test_store_creates_the_directory_lazily(tmp_path):
    store = _store(tmp_path)
    assert not store.directory.exists()

    assert store.save("bash", "x") is not None
    assert store.directory.is_dir()


def test_store_returns_a_new_path_for_every_save(tmp_path):
    store = _store(tmp_path)
    first = store.save("grep", "one")
    second = store.save("grep", "two")

    assert first is not None and second is not None
    assert first != second
    assert first.read_text(encoding="utf-8") == "one"
    assert second.read_text(encoding="utf-8") == "two"


def test_store_sanitizes_the_tool_name_for_the_filename(tmp_path):
    store = _store(tmp_path)
    path = store.save("mcp__srv/tool", "payload")

    assert path is not None
    assert path.parent == store.directory
    assert "/" not in path.name
    assert path.name.startswith("mcp__srv_tool")


def test_successful_save_leaves_no_temporary_files_behind(tmp_path):
    store = _store(tmp_path)
    path = store.save("bash", "payload")

    assert path is not None
    assert _files(store) == [path]


def test_atomic_replace_failure_returns_none_and_leaves_no_partial_file(
    tmp_path, monkeypatch
):
    store = _store(tmp_path)

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("zenagent.agent.artifacts.os.replace", boom)
    assert store.save("bash", "payload") is None
    assert _files(store) == []


def test_save_failure_returns_none_instead_of_raising(tmp_path):
    # Make the artifacts parent path a *file* so mkdir cannot create the dir.
    blocked = tmp_path / "sessions" / "artifacts"
    blocked.parent.mkdir(parents=True)
    blocked.write_text("not a directory")
    store = ArtifactStore(tmp_path / "sessions" / "abc.jsonl")

    assert store.save("bash", "payload") is None


def test_a_single_artifact_larger_than_the_byte_cap_is_refused(tmp_path):
    store = _store(tmp_path, max_bytes=100)
    assert store.save("bash", "x" * 500) is None
    assert _files(store) == []


def test_store_prunes_oldest_files_beyond_the_file_cap(tmp_path):
    store = _store(tmp_path, max_files=3)
    paths = [store.save("bash", f"content-{index}") for index in range(5)]
    assert all(path is not None for path in paths)

    kept = _files(store)
    assert len(kept) == 3
    assert sorted(path.read_text(encoding="utf-8") for path in kept) == [
        "content-2",
        "content-3",
        "content-4",
    ]


def test_store_prunes_oldest_files_beyond_the_byte_cap(tmp_path):
    store = _store(tmp_path, max_bytes=2_500)
    for index in range(5):
        assert store.save("bash", f"{index}" + ("x" * 999)) is not None

    kept = _files(store)
    assert len(kept) == 2  # two ~1 KB files still fit in 2.5 KB
    assert sorted(path.read_text(encoding="utf-8")[0] for path in kept) == [
        "3",
        "4",
    ]


def test_default_retention_caps_match_the_documented_numbers():
    assert MAX_ARTIFACT_FILES == 64
    assert MAX_ARTIFACT_BYTES == 8 * 1024 * 1024


def test_build_artifact_sink_returns_a_save_callable(tmp_path):
    sink = build_artifact_sink(tmp_path / "sessions" / "def.jsonl")
    path = sink("fetch", "body")

    assert path is not None
    assert path.parent == tmp_path / "sessions" / "artifacts" / "def"
    assert path.read_text(encoding="utf-8") == "body"


async def test_read_file_can_read_a_saved_artifact(tmp_path):
    from zenagent.tools.fs import READ_FILE

    # 100 long lines (~50 KB): under read_file's 400-line page, over the budget.
    payload = "".join(f"line {index} " + ("y" * 480) + "\n" for index in range(100))
    assert len(payload.encode("utf-8")) > 40_000
    store = ArtifactStore(tmp_path / "sessions" / "s1.jsonl")
    path = store.save("bash", payload)
    assert path is not None

    result = await READ_FILE.execute("call_1", {"path": str(path)}, None)
    assert not result.is_error
    assert result.content[0].text == payload
