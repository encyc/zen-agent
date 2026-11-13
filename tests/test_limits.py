"""The ``[limits]`` / ``[task]`` knob registry, validation and parsing.

The knob set is the contract between the settings page, ``zen doctor`` and the
config parser, so these tests are about the *shape* of that contract: ranges,
global-only, and the surgical writer that keeps a hand-edited file intact.
"""

from pathlib import Path

import pytest

from zenagent.config import UserConfig, load_user_config
from zenagent.limits import (
    ALL_KNOBS,
    KNOB_BY_KEY,
    KNOBS_BY_TABLE,
    Limits,
    coerce_value,
    describe,
    parse_limits,
    read_managed,
    render_limits_block,
    write_limits,
    write_managed,
)


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_every_knob_has_a_unique_key_and_attr():
    keys = [knob.key for knob in ALL_KNOBS]
    attrs = [knob.attr for knob in ALL_KNOBS]
    assert len(keys) == len(set(keys))
    assert len(attrs) == len(set(attrs))


def test_knobs_are_grouped_by_the_sections_the_pages_show():
    sections = {knob.section for knob in ALL_KNOBS}
    assert sections == {
        "skills",
        "context",
        "agent",
        "subagents",
        "tools",
        "zen",
        "swarm",
    }
    # Every table the writer manages has at least one knob and vice versa.
    assert set(KNOBS_BY_TABLE) == {knob.table for knob in ALL_KNOBS}


def test_defaults_parse_to_the_registry_defaults(tmp_path):
    limits = load_user_config(write_config(tmp_path, "")).limits
    assert limits == Limits()
    assert limits.explicit == frozenset()


def test_values_are_parsed_and_marked_explicit(tmp_path):
    path = write_config(
        tmp_path,
        "[limits.skills]\nmax_index_bytes = 16384\n\n"
        "[limits.context]\ncompact_threshold = 0.9\n",
    )
    limits = load_user_config(path).limits
    assert limits.max_index_bytes == 16384
    assert limits.compact_threshold == 0.9
    assert limits.explicit == frozenset({"max_index_bytes", "compact_threshold"})


@pytest.mark.parametrize(
    "text, message",
    [
        ("[limits.skills]\nmax_index_bytes = 5\n", "must be between"),
        ("[limits.skills]\nmax_index_bytes = 'big'\n", "must be a integer"),
        ("[limits.context]\ncompact_threshold = 2.0\n", "must be between"),
        ("[limits.agent]\nnope = 1\n", "unknown keys"),
        ("[limits.bogus]\nmax_turns = 1\n", "unknown tables"),
        ("limits = 3\n", "must be a table"),
    ],
)
def test_bad_values_fail_loudly_with_file_and_key(tmp_path, text, message):
    path = write_config(tmp_path, text)
    with pytest.raises(ValueError) as excinfo:
        load_user_config(path)
    assert str(path) in str(excinfo.value)
    assert message in str(excinfo.value)


def test_limits_are_global_only(tmp_path, monkeypatch):
    """A committed project layer may not widen the machine's budgets."""
    global_file = write_config(tmp_path, "")
    project_file = tmp_path / "repo" / ".zenagent" / "config.toml"
    project_file.parent.mkdir(parents=True)
    project_file.write_text("[limits.agent]\nmax_turns = 9999\n", encoding="utf-8")
    monkeypatch.setattr("zenagent.config.config_path", lambda: global_file)
    with pytest.raises(ValueError, match="global-only"):
        load_user_config(cwd=tmp_path / "repo")


def test_task_knobs_share_the_same_registry(tmp_path):
    path = write_config(tmp_path, "[task]\nmax_turns = 12\nmax_cost = 0.5\n")
    config = load_user_config(path)
    assert config.task_max_turns == 12
    assert config.task_max_cost == 0.5
    knob = KNOB_BY_KEY["task.max_turns"]
    assert knob.attr == "task_max_turns"
    assert getattr(config, knob.attr) == 12


def test_coerce_rejects_booleans_and_floats_for_integer_knobs():
    knob = KNOB_BY_KEY["limits.skills.max_index_bytes"]
    for bad in (True, 1.5, "x", None):
        with pytest.raises(ValueError):
            coerce_value(knob, bad, Path("/tmp/config.toml"))
    assert coerce_value(knob, 4096, Path("/tmp/config.toml")) == 4096
    assert coerce_value(knob, 4096.0, Path("/tmp/config.toml")) == 4096


def test_nullable_knob_accepts_none():
    knob = KNOB_BY_KEY["task.max_cost"]
    assert coerce_value(knob, None, Path("/tmp/config.toml")) is None
    assert coerce_value(knob, 2.5, Path("/tmp/config.toml")) == 2.5


def test_parse_limits_rejects_unknown_section_and_key(tmp_path):
    with pytest.raises(ValueError, match="unknown tables"):
        parse_limits({"nope": {}}, tmp_path / "config.toml")
    with pytest.raises(ValueError, match="unknown keys"):
        parse_limits({"agent": {"nope": 1}}, tmp_path / "config.toml")


def test_describe_lists_only_explicit_knobs():
    limits = Limits().with_value("max_index_bytes", 16384)
    assert describe(limits) == ["limits.skills.max_index_bytes = 16384"]
    assert describe(Limits()) == []


def test_render_block_is_empty_without_explicit_values():
    assert render_limits_block(Limits()) == ""
    block = render_limits_block(Limits().with_value("max_turns", 40))
    assert "[limits.agent]" in block and "max_turns = 40" in block


def test_write_limits_leaves_the_rest_of_the_file_alone(tmp_path):
    path = write_config(
        tmp_path,
        "# my own comments\n"
        'default_model = "deepseek-flash"\n'
        "\n"
        "[tools]\n"
        'allow = ["read_*"]  # trailing note\n',
    )
    write_limits(path, Limits().with_value("max_index_bytes", 16384))
    text = path.read_text(encoding="utf-8")
    assert "# my own comments" in text
    assert 'allow = ["read_*"]  # trailing note' in text
    assert "max_index_bytes = 16384" in text
    assert load_user_config(path).limits.max_index_bytes == 16384
    # The block sits after the user's tables, not in the middle of one.
    assert text.index("[tools]") < text.index("[limits.skills]")


def test_reset_deletes_the_key_so_defaults_are_inherited(tmp_path):
    path = write_config(tmp_path, "[limits.agent]\nmax_turns = 40\n")
    write_limits(path, Limits())
    assert "max_turns" not in path.read_text(encoding="utf-8")
    assert load_user_config(path).limits.max_turns == Limits().max_turns


def test_rewrite_replaces_in_place_and_keeps_one_block(tmp_path):
    path = write_config(tmp_path, 'default_model = "x"\n')
    write_limits(path, Limits().with_value("max_turns", 40))
    write_limits(path, Limits().with_value("max_turns", 41))
    text = path.read_text(encoding="utf-8")
    assert text.count("[limits.agent]") == 1
    assert "max_turns = 41" in text
    assert text.count("Managed by zen") == 1


def test_managed_write_keeps_task_and_limits_apart(tmp_path):
    path = write_config(tmp_path, "[task]\nmax_turns = 250\nkeep_transcripts = 5\n")
    tables = read_managed(path)
    tables.setdefault("limits.skills", {})["max_index_bytes"] = 16384
    write_managed(path, tables)
    config = load_user_config(path)
    assert config.task_max_turns == 250
    assert config.task_keep_transcripts == 5
    assert config.limits.max_index_bytes == 16384

    # Dropping the limits half must not touch [task].
    tables.pop("limits.skills")
    write_managed(path, tables)
    config = load_user_config(path)
    assert config.task_max_turns == 250
    assert config.limits.explicit == frozenset()


def test_writer_refuses_unknown_keys_instead_of_deleting_them(tmp_path):
    path = write_config(tmp_path, "[limits.agent]\nfuture_knob = 3\n")
    from zenagent.limits import LimitsWriteError

    with pytest.raises(LimitsWriteError, match="unknown keys"):
        write_limits(path, Limits().with_value("max_turns", 10))
    assert "future_knob = 3" in path.read_text(encoding="utf-8")


def test_writer_creates_missing_file_and_parent(tmp_path):
    path = tmp_path / "nested" / "config.toml"
    write_limits(path, Limits().with_value("shell_max_output_bytes", 5000))
    assert path.is_file()
    assert load_user_config(path).limits.shell_max_output_bytes == 5000


def test_writer_preserves_mode(tmp_path):
    import stat

    path = write_config(tmp_path, 'default_model = "x"\n')
    path.chmod(0o600)
    write_limits(path, Limits().with_value("max_turns", 7))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_string_knob_only_accepts_its_choices():
    knob = KNOB_BY_KEY["zen.worker_mode"]
    assert coerce_value(knob, "auto", Path("/tmp/config.toml")) == "auto"
    for bad in ("nope", True, 1, None):
        with pytest.raises(ValueError, match="zen.worker_mode"):
            coerce_value(knob, bad, Path("/tmp/config.toml"))


def test_zen_and_swarm_tables_round_trip_through_the_writer(tmp_path):
    path = write_config(tmp_path, "# keep me\ndefault_model = \"x\"\n")
    tables = read_managed(path)
    tables["zen"] = {
        "max_concurrent": 5,
        "batch_window_s": 1.25,
        "report_limit": 1200,
        "worker_mode": "ask",
    }
    tables["swarm"] = {"max_concurrent": 4, "max_chain_depth": 12, "max_chain_cost": 2.5}
    write_managed(path, tables)

    text = path.read_text(encoding="utf-8")
    assert "# keep me" in text
    assert 'worker_mode = "ask"' in text
    config = load_user_config(path)
    assert config.zen_max_concurrent == 5
    assert config.zen_batch_window_s == 1.25
    assert config.zen_report_limit == 1200
    assert config.zen_worker_mode == "ask"
    assert config.swarm_max_concurrent == 4
    assert config.swarm_max_chain_depth == 12
    assert config.swarm_max_chain_cost == 2.5


def test_registry_defaults_match_the_config_defaults():
    from zenagent import config as user_config

    assert KNOB_BY_KEY["zen.max_concurrent"].default == user_config.DEFAULT_ZEN_MAX_CONCURRENT
    assert KNOB_BY_KEY["zen.batch_window_s"].default == user_config.DEFAULT_ZEN_BATCH_WINDOW_S
    assert KNOB_BY_KEY["zen.report_limit"].default == user_config.DEFAULT_ZEN_REPORT_LIMIT
    assert KNOB_BY_KEY["zen.worker_mode"].default == user_config.DEFAULT_ZEN_WORKER_MODE
    assert (
        KNOB_BY_KEY["swarm.max_concurrent"].default
        == user_config.DEFAULT_SWARM_MAX_CONCURRENT
    )
    assert (
        KNOB_BY_KEY["swarm.max_chain_depth"].default
        == user_config.DEFAULT_SWARM_MAX_CHAIN_DEPTH
    )
    assert (
        KNOB_BY_KEY["swarm.max_chain_cost"].default
        == user_config.DEFAULT_SWARM_MAX_CHAIN_COST
    )


def test_user_config_defaults_do_not_share_the_limits_object():
    """A mutable default would leak one runtime's edit into the next."""
    first, second = UserConfig(), UserConfig()
    first.limits = Limits().with_value("max_turns", 3)
    assert second.limits == Limits()
