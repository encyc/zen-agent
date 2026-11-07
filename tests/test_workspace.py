"""Workspace inspection: file browsing and git worktrees for the GUI panel."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from zenagent.app.workspace import (
    list_directory,
    read_file,
    workspace_files,
    worktree_overview,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("export const value = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    return tmp_path


def test_list_directory_sorts_folders_first_and_skips_git(project: Path) -> None:
    result = list_directory(project, "")

    assert result["path"] == ""
    assert result["truncated"] is False
    assert [entry["name"] for entry in result["entries"]] == ["src", ".hidden", "README.md"]
    assert result["entries"][0] == {
        "name": "src",
        "path": "src",
        "kind": "dir",
        "size": 0,
        "hidden": False,
    }
    assert result["entries"][1]["hidden"] is True


def test_list_directory_reads_a_nested_folder(project: Path) -> None:
    result = list_directory(project, "src")

    assert result["path"] == "src"
    assert [entry["path"] for entry in result["entries"]] == ["src/main.ts"]
    assert result["entries"][0]["kind"] == "file"
    assert result["entries"][0]["size"] == 23


def test_workspace_paths_cannot_escape_the_project(project: Path) -> None:
    with pytest.raises(ValueError):
        list_directory(project, "../")
    with pytest.raises(ValueError):
        read_file(project, "/etc/passwd")


def test_read_file_returns_text_and_line_count(project: Path) -> None:
    result = read_file(project, "src/main.ts")

    assert result["kind"] == "text"
    assert result["path"] == "src/main.ts"
    assert result["text"] == "export const value = 1\n"
    assert result["total_lines"] == 1
    assert result["truncated"] is False


def test_read_file_marks_binary_files(project: Path) -> None:
    (project / "blob.bin").write_bytes(b"abc\x00def")

    result = read_file(project, "blob.bin")

    assert result["kind"] == "binary"
    assert result["size"] == 7
    assert "text" not in result


def test_read_file_returns_an_image_data_url(project: Path) -> None:
    png = b"\x89PNG\r\n\x1a\n"
    (project / "pixel.png").write_bytes(png)

    result = read_file(project, "pixel.png")

    assert result["kind"] == "image"
    assert result["mime"] == "image/png"
    assert result["data_url"].startswith("data:image/png;base64,")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_workspace_files_walks_outside_a_repository(tmp_path: Path) -> None:
    (tmp_path / "src" / "nested").mkdir(parents=True)
    (tmp_path / "src" / "main.ts").write_text("x", encoding="utf-8")
    (tmp_path / "src" / "nested" / "deep.py").write_text("x", encoding="utf-8")
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.pyc").write_text("x", encoding="utf-8")

    result = workspace_files(tmp_path)

    kinds = {entry["path"]: entry["kind"] for entry in result["entries"]}
    assert kinds["src/main.ts"] == "file"
    assert kinds["src/nested/deep.py"] == "file"
    assert kinds["src"] == "dir"
    assert kinds["src/nested"] == "dir"
    assert "node_modules/pkg/index.js" not in kinds
    assert "__pycache__/a.pyc" not in kinds
    assert result["truncated"] is False


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_workspace_files_honors_gitignore_and_keeps_untracked(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("secret/\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("x", encoding="utf-8")
    (tmp_path / "new.txt").write_text("x", encoding="utf-8")
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "key.txt").write_text("x", encoding="utf-8")

    result = workspace_files(tmp_path)

    paths = {entry["path"] for entry in result["entries"]}
    assert {"app.py", "new.txt", ".gitignore"} <= paths
    assert "secret/key.txt" not in paths
    assert "secret" not in paths


def test_workspace_files_stops_at_the_index_cap(tmp_path: Path, monkeypatch) -> None:
    from zenagent.app import workspace

    for index in range(5):
        (tmp_path / f"file-{index}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(workspace, "FS_INDEX_MAX_ENTRIES", 3)

    result = workspace_files(tmp_path)

    files = [entry for entry in result["entries"] if entry["kind"] == "file"]
    assert len(files) == 3
    assert result["truncated"] is True


def test_workspace_files_reuses_its_snapshot(tmp_path: Path) -> None:
    (tmp_path / "first.txt").write_text("x", encoding="utf-8")
    first = workspace_files(tmp_path)

    (tmp_path / "second.txt").write_text("x", encoding="utf-8")
    second = workspace_files(tmp_path)

    assert first == second
    assert "second.txt" not in {entry["path"] for entry in second["entries"]}


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_worktree_overview_lists_the_main_and_linked_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "e2e@example.com")
    _git(repo, "config", "user.name", "e2e")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-q", "-b", "feature", str(linked))

    overview = worktree_overview(repo)

    assert overview["is_repo"] is True
    assert overview["branch"] == "main"
    assert overview["is_worktree"] is False
    assert overview["main"] == str(repo.resolve())
    assert [item["branch"] for item in overview["worktrees"]] == ["main", "feature"]
    assert [item["current"] for item in overview["worktrees"]] == [True, False]
    assert overview["status"]["branch"] == "main"
    assert overview["status"]["dirty"] == 0

    linked_overview = worktree_overview(linked)
    assert linked_overview["branch"] == "feature"
    assert linked_overview["is_worktree"] is True
    assert [item["current"] for item in linked_overview["worktrees"]] == [False, True]


def test_worktree_overview_outside_a_repository(tmp_path: Path) -> None:
    overview = worktree_overview(tmp_path)

    assert overview["is_repo"] is False
    assert overview["worktrees"] == []
    assert overview["branch"] == ""


def test_a_symlinked_directory_stays_a_file_and_never_crashes(tmp_path, monkeypatch):
    """`Path.is_dir(follow_symlinks=...)` only exists on Python 3.13+, so the
    oldest supported interpreter must stay on the portable path: a symlink is
    inspected as a link, not followed out of the workspace."""
    import pathlib

    original_is_dir = pathlib.Path.is_dir

    def no_follow_kwarg(self, *args, **kwargs):
        if "follow_symlinks" in kwargs:
            raise TypeError(
                "Path.is_dir() got an unexpected keyword argument 'follow_symlinks'"
            )
        return original_is_dir(self)

    monkeypatch.setattr(pathlib.Path, "is_dir", no_follow_kwarg)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)

    listing = list_directory(root, "")
    entry = next(item for item in listing["entries"] if item["name"] == "link")
    assert entry["kind"] == "file"
