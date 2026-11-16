from zenagent.agent.session import Session
from zenagent.app import records


def _goal_record() -> dict:
    return records.goal({"goal_id": "g1", "status": "active", "objective": "fix"})


def test_goal_record_shape():
    record = _goal_record()
    assert record["type"] == records.GOAL
    assert records.is_goal(record)
    assert records.goal(None)["goal"] is None


def test_keep_for_rewrite_keeps_goal_by_default():
    assert records.keep_for_rewrite()({"type": records.GOAL}) is True


def test_keep_for_rewrite_can_drop_goal_for_fork():
    assert records.keep_for_rewrite(keep_goal=False)({"type": records.GOAL}) is False


async def test_fork_drops_goal_but_rewrite_keeps_it(tmp_path):
    from zenagent.types import UserMessage

    source = Session(tmp_path / "source.jsonl")
    source.materialize()
    await source.append(UserMessage(content="hi", timestamp=1))
    source.append_meta(_goal_record())
    rewrite = source.rewrite([], keep=records.keep_for_rewrite(), root=tmp_path)
    rewritten = Session(rewrite)
    rewritten.load()
    assert [r for r in rewritten.records if records.is_goal(r)]

    fork = source.fork_at(
        1, keep=records.keep_for_rewrite(keep_goal=False), root=tmp_path
    )
    forked = Session(fork)
    forked.load()
    assert not [r for r in forked.records if records.is_goal(r)]
