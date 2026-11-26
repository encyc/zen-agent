# tests/test_automations_store.py
import json

import pytest

from zenagent.app.automations import (
    AutomationStore,
    Job,
    JobState,
    Schedule,
    StateStore,
    resolve_session,
)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path / "proj"


def job(name="daily-report", **extra):
    return Job(
        id="",
        name=name,
        prompt="go",
        schedule=Schedule.parse("daily 09:00"),
        session="s1.jsonl",
        **extra,
    )


def test_missing_file_is_an_empty_store_and_is_not_created(root):
    store = AutomationStore(root)
    assert store.load() == []
    assert not store.path.exists()


def test_add_assigns_id_and_persists_atomically(root):
    store = AutomationStore(root)
    saved = store.add(job())
    assert saved.id.startswith("a_")
    raw = json.loads(store.path.read_text())
    assert raw["version"] == 1
    assert raw["jobs"][0]["name"] == "daily-report"
    assert AutomationStore(root).load()[0].id == saved.id


def test_duplicate_name_is_refused(root):
    store = AutomationStore(root)
    store.add(job())
    with pytest.raises(ValueError):
        store.add(job())


def test_update_only_accepts_known_fields(root):
    store = AutomationStore(root)
    store.add(job())
    assert store.update("daily-report", enabled=False).enabled is False
    with pytest.raises(ValueError):
        store.update("daily-report", cwd="/etc")


def test_remove(root):
    store = AutomationStore(root)
    store.add(job())
    assert store.remove("daily-report") is True
    assert store.remove("daily-report") is False
    assert store.load() == []


def test_corrupt_file_keeps_the_last_good_snapshot(root):
    store = AutomationStore(root)
    store.add(job("keep"))                       # good snapshot in memory
    store.path.write_text('{"version": 1, "jobs": [{"na')   # torn write on disk
    assert [j.name for j in store.load()] == ["keep"]
    assert store.warnings


def test_a_store_that_never_loaded_good_data_returns_empty(root):
    store = AutomationStore(root)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json")
    assert store.load() == []
    assert store.warnings


def test_state_round_trip_and_missing_default(root):
    store = StateStore(root)
    states = {"a_1": JobState(session="s1.jsonl", last_run_at=5.0, run_count=2)}
    store.save(states)
    assert store.load()["a_1"].run_count == 2


def test_resolve_session_under_project_dir(root):
    path = resolve_session(root, "s1.jsonl")
    assert path.name == "s1.jsonl"
    assert path.parent.name == "sessions"


def test_one_bad_entry_does_not_disable_every_job(root):
    """A single malformed job must be skipped, not take the file down.

    The daemon builds a fresh store every tick, so "keep the last good
    snapshot" cannot help there; per-entry degradation is the only rule that
    survives a restart.
    """
    store = AutomationStore(root)
    store.add(job("keep"))
    raw = json.loads(store.path.read_text())
    raw["jobs"].append({"name": "broken", "prompt": "p", "session": "s.jsonl",
                        "schedule": {"kind": "nope"}})
    store.path.write_text(json.dumps(raw))
    fresh = AutomationStore(root)
    assert [j.name for j in fresh.load()] == ["keep"]
    assert any("broken" in warning for warning in fresh.warnings)


def test_state_store_warns_on_a_corrupt_file(root):
    store = StateStore(root)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{torn")
    assert store.load() == {}
    assert store.warnings


def test_timeout_must_be_positive(root):
    store = AutomationStore(root)
    store.load()
    with pytest.raises(ValueError):
        store.add(job(timeout_s=0))


def test_get_and_add_load_lazily(root):
    """A fresh store must not silently see an empty file (and then overwrite it).

    The automations tool builds a store per call, so requiring a prior `load()`
    would make `add` wipe every other job and `get` report nothing.
    """
    seeded = AutomationStore(root)
    seeded.add(job("existing"))

    fresh = AutomationStore(root)
    assert fresh.get("existing") is not None          # no explicit load() first
    fresh.add(job("second"))
    assert sorted(j.name for j in AutomationStore(root).load()) == ["existing", "second"]
