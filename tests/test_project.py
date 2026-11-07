"""Project identity: root discovery, keys, layout and metadata."""

import json
from pathlib import Path

import pytest

from zenagent import project


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(project, "zenagent_home", lambda: tmp_path / "home")
    return tmp_path


def make_repo(path: Path) -> Path:
    (path / ".git").mkdir(parents=True, exist_ok=True)
    return path


def test_project_root_is_the_nearest_git_ancestor(tmp_path):
    repo = make_repo(tmp_path / "repo")
    nested = repo / "src" / "deep"
    nested.mkdir(parents=True)
    assert project.find_project_root(nested) == repo.resolve()


def test_project_root_falls_back_to_cwd_without_git(tmp_path):
    plain = tmp_path / "plain" / "dir"
    plain.mkdir(parents=True)
    assert project.find_project_root(plain) == plain.resolve()


def test_git_file_counts_for_worktrees(tmp_path):
    worktree = tmp_path / "repo" / "worktree"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: /elsewhere\n")
    assert project.find_project_root(worktree) == worktree.resolve()


def test_project_key_is_readable_and_collision_resistant(tmp_path):
    one = make_repo(tmp_path / "app")
    two = make_repo(tmp_path / "vendor" / "app")
    key_one = project.project_key(one)
    key_two = project.project_key(two)
    assert key_one.startswith("app-")
    assert len(key_one.rsplit("-", 1)[1]) == 8
    assert key_one != key_two


def test_project_dirs_live_under_the_projects_root(tmp_path):
    repo = make_repo(tmp_path / "app")
    key = project.project_key(repo)
    assert project.projects_root() == tmp_path / "home" / "projects"
    assert project.project_dir(root=repo) == project.projects_root() / key
    assert (
        project.project_sessions_dir(root=repo)
        == project.projects_root() / key / "sessions"
    )
    assert (
        project.legacy_sessions_dir()
        == tmp_path / "home" / "sessions"
    )


def test_project_config_and_mcp_paths_are_in_the_repo(tmp_path):
    repo = make_repo(tmp_path / "app")
    assert project.project_config_path(cwd=repo) == repo / ".zenagent" / "config.toml"
    assert project.project_mcp_path(cwd=repo) == repo / ".zenagent" / "mcp.json"


def test_meta_round_trip(tmp_path):
    repo = make_repo(tmp_path / "app")
    assert project.load_project_meta(root=repo) is None
    project.write_project_meta(root=repo)
    meta = project.load_project_meta(root=repo)
    assert meta is not None
    assert Path(meta.root) == repo.resolve()
    assert meta.name == "app"
    assert meta.last_used


def test_all_session_dirs_lists_legacy_then_projects(tmp_path):
    repo = make_repo(tmp_path / "app")
    project.project_sessions_dir(root=repo).mkdir(parents=True)
    project.legacy_sessions_dir().mkdir(parents=True)
    dirs = project.all_session_dirs()
    assert dirs[0] == project.legacy_sessions_dir()
    assert project.project_sessions_dir(root=repo) in dirs


def test_session_dir_classification(tmp_path):
    repo = make_repo(tmp_path / "app")
    sessions = project.project_sessions_dir(root=repo)
    sessions.mkdir(parents=True)
    session = sessions / "20260101-000000.jsonl"
    session.write_text("")
    assert project.project_key_for_session(session) == project.project_key(repo)
    assert project.project_key_for_session(project.legacy_sessions_dir() / "x.jsonl") is None


def test_find_project_root_accepts_a_file(tmp_path):
    repo = make_repo(tmp_path / "app")
    target = repo / "noted.md"
    target.write_text("x")
    assert project.find_project_root(target) == repo.resolve()


def test_project_root_for_a_sessions_dir(tmp_path):
    repo = make_repo(tmp_path / "app")
    project.write_project_meta(root=repo)
    sessions = project.project_sessions_dir(root=repo)
    assert project.project_root_for_sessions_dir(sessions) == str(repo.resolve())
    # The legacy flat directory has no meta.json: no recorded root.
    assert project.project_root_for_sessions_dir(project.legacy_sessions_dir()) == ""


def test_project_root_for_a_sessions_dir_ignores_a_broken_meta(tmp_path):
    repo = make_repo(tmp_path / "app")
    meta = project.project_dir(root=repo) / "meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text("{not json", encoding="utf-8")
    sessions = project.project_sessions_dir(root=repo)
    assert project.project_root_for_sessions_dir(sessions) == ""


def seed_project(root: Path, sessions: int = 2) -> Path:
    project.write_project_meta(root=root)
    directory = project.project_sessions_dir(root=root)
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(sessions):
        (directory / f"2026010{index}-000000.jsonl").write_text("{}\n")
    return project.project_dir(root=root)


def test_relocate_moves_state_and_rewrites_meta(tmp_path):
    old = make_repo(tmp_path / "old-name")
    new = make_repo(tmp_path / "new-name")
    old_dir = seed_project(old)
    meta_path = old_dir / "meta.json"
    payload = json.loads(meta_path.read_text())
    payload["last_used"] = "2026-01-01T00:00:00+00:00"
    meta_path.write_text(json.dumps(payload))

    result = project.relocate_project(old, new)

    new_dir = project.project_dir(root=new)
    assert result.moved is True
    assert result.sessions == 2
    assert result.old_key == project.project_key(old)
    assert result.new_key == project.project_key(new)
    assert not old_dir.exists()
    assert (new_dir / "meta.json").is_file()
    moved_meta = project.load_project_meta(root=new)
    assert moved_meta is not None
    assert Path(moved_meta.root) == new.resolve()
    assert moved_meta.name == "new-name"
    assert moved_meta.last_used == "2026-01-01T00:00:00+00:00"
    assert len(list((new_dir / "sessions").glob("*.jsonl"))) == 2


def test_relocate_to_the_same_root_is_a_noop(tmp_path):
    repo = make_repo(tmp_path / "app")
    directory = seed_project(repo)

    result = project.relocate_project(repo, repo)

    assert result.moved is False
    assert result.old_key == result.new_key
    assert result.sessions == 2
    assert directory.is_dir()
    assert len(list((directory / "sessions").glob("*.jsonl"))) == 2


def test_relocate_resolves_the_git_root_of_the_target(tmp_path):
    old = make_repo(tmp_path / "old-name")
    seed_project(old)
    new_repo = make_repo(tmp_path / "new-name")
    nested = new_repo / "src"
    nested.mkdir()

    result = project.relocate_project(old, nested)

    assert result.new_root == str(new_repo.resolve())
    assert (project.project_dir(root=new_repo) / "sessions").is_dir()
    moved_meta = project.load_project_meta(root=new_repo)
    assert moved_meta is not None
    assert Path(moved_meta.root) == new_repo.resolve()


def test_relocate_refuses_a_target_whose_state_already_exists(tmp_path):
    old = make_repo(tmp_path / "old-name")
    new = make_repo(tmp_path / "new-name")
    old_dir = seed_project(old)
    seed_project(new, sessions=1)

    with pytest.raises(ValueError, match="already"):
        project.relocate_project(old, new)

    assert len(list((old_dir / "sessions").glob("*.jsonl"))) == 2
    assert len(list((project.project_sessions_dir(root=new)).glob("*.jsonl"))) == 1


def test_relocate_requires_existing_state_for_the_old_root(tmp_path):
    old = tmp_path / "gone"
    old.mkdir()
    new = make_repo(tmp_path / "new-name")
    with pytest.raises(ValueError, match="no project state"):
        project.relocate_project(old, new)


def test_relocate_requires_a_directory_target(tmp_path):
    old = make_repo(tmp_path / "old-name")
    seed_project(old)
    target = tmp_path / "not-a-folder"
    target.write_text("x")
    with pytest.raises(ValueError, match="not a directory"):
        project.relocate_project(old, target)
    assert project.project_dir(root=old).is_dir()
