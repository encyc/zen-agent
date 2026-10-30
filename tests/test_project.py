"""Project identity: root discovery, keys, layout and metadata."""

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
