"""The message budget: intent classification, fan-out and chain-level gates.

These are the structural stops that bound a swarm regardless of what the model
writes. The rules are pure functions of the room's task table, so they are
tested without a manager, a runtime or a model.
"""

from zenagent.app.swarm.gate import MessageGate, SwarmLimits
from zenagent.app.swarm.room import RoomMessage, SwarmStore, TaskRecord
from zenagent.app.swarm.router import Router


def _message(**overrides) -> RoomMessage:
    fields = {
        "id": "m1",
        "ts": 1,
        "author_kind": "user",
        "author_id": "you",
        "text": "hi",
        "mentions": [],
        "task_id": None,
        "chain_depth": 0,
    }
    fields.update(overrides)
    return RoomMessage(**fields)


def _task(
    store: SwarmStore,
    task_id: str,
    *,
    chain_id: str = "c1",
    assignee: str = "coder",
    sender: str = "you",
    sender_kind: str = "user",
    created_at: int = 0,
    result_message_id: str | None = None,
) -> TaskRecord:
    """Append one queued task; the store only accepts a task that starts queued."""
    task = TaskRecord(
        id=task_id,
        assignee=assignee,
        state="queued",
        chain_id=chain_id,
        created_by_kind=sender_kind,
        created_by_id=sender,
        created_at=created_at,
        updated_at=created_at,
        result_message_id=result_message_id,
    )
    store.append_task(task)
    return task


# -- L0: intent --------------------------------------------------------------


def test_a_user_post_is_a_request(tmp_path):
    gate = MessageGate()
    store = SwarmStore(tmp_path)

    assert gate.classify(_message(mentions=["coder"]), store) == "request"


def test_a_task_result_is_a_reply(tmp_path):
    gate = MessageGate()
    store = SwarmStore(tmp_path)
    _task(store, "t1", result_message_id="m9")

    result = _message(
        id="m9", author_kind="agent", author_id="coder", task_id="t1", mentions=["you"]
    )

    assert gate.classify(result, store) == "reply"


def test_a_mid_run_post_is_a_request(tmp_path):
    gate = MessageGate()
    store = SwarmStore(tmp_path)
    _task(store, "t1", result_message_id="m9")

    mid_run = _message(
        id="m8",
        author_kind="agent",
        author_id="coder",
        task_id="t1",
        mentions=["research"],
    )

    assert gate.classify(mid_run, store) == "request"


def test_a_message_without_mentions_is_a_note(tmp_path):
    gate = MessageGate()
    store = SwarmStore(tmp_path)

    assert gate.classify(_message(), store) == "note"


# -- L2: fan-out -------------------------------------------------------------


def test_a_request_may_fan_out_to_four_roles(tmp_path):
    gate = MessageGate(SwarmLimits(max_request_fanout=4))
    store = SwarmStore(tmp_path)

    decision = gate.select(
        _message(mentions=["a", "b", "c", "d", "e", "f"]),
        store,
        "c1",
        role_ids={"a", "b", "c", "d", "e", "f"},
    )

    assert decision.accepted == ["a", "b", "c", "d"]
    assert decision.rejected == [("e", "fanout_cap"), ("f", "fanout_cap")]


def test_a_reply_may_not_broadcast(tmp_path):
    gate = MessageGate(SwarmLimits(max_reply_fanout=1))
    store = SwarmStore(tmp_path)
    _task(store, "t1", result_message_id="m9")

    decision = gate.select(
        _message(
            id="m9",
            author_kind="agent",
            author_id="coder",
            task_id="t1",
            mentions=["a", "b"],
        ),
        store,
        "c1",
        role_ids={"a", "b"},
    )

    assert decision.intent == "reply"
    assert decision.accepted == ["a"]
    assert decision.rejected == [("b", "reply_cap")]


def test_unknown_mentions_do_not_spend_the_fanout_budget(tmp_path):
    gate = MessageGate(SwarmLimits(max_request_fanout=2))
    store = SwarmStore(tmp_path)

    decision = gate.select(
        _message(mentions=["ghost", "a", "b"]), store, "c1", role_ids={"a", "b"}
    )

    assert decision.accepted == ["a", "b"]
    assert decision.rejected == []


# -- L4: chain budget and the storm breaker ----------------------------------


def test_the_chain_task_budget_refuses_everything_past_the_cap(tmp_path):
    gate = MessageGate(SwarmLimits(max_chain_tasks=3))
    store = SwarmStore(tmp_path)
    for index in range(3):
        _task(store, f"t{index}")

    decision = gate.select(
        _message(mentions=["a", "b"]), store, "c1", role_ids={"a", "b"}
    )

    assert decision.accepted == []
    assert decision.rejected == [("a", "chain_tasks"), ("b", "chain_tasks")]
    assert decision.chain_reason == "chain_tasks"


def test_the_burst_breaker_trips_on_a_rate_of_new_tasks(tmp_path):
    gate = MessageGate(SwarmLimits(storm_window_s=60.0, storm_burst=4))
    store = SwarmStore(tmp_path)
    for index in range(4):
        _task(store, f"t{index}", created_at=2_000_000_000)

    decision = gate.select(
        _message(mentions=["a"]),
        store,
        "c1",
        role_ids={"a"},
        now=2_000_000_010,
    )

    assert decision.accepted == []
    assert decision.rejected == [("a", "chain_burst")]
    assert decision.chain_reason == "chain_burst"


def test_tasks_outside_the_window_do_not_trip_the_breaker(tmp_path):
    gate = MessageGate(SwarmLimits(storm_window_s=60.0, storm_burst=4))
    store = SwarmStore(tmp_path)
    for index in range(4):
        _task(store, f"t{index}", created_at=1_000)

    decision = gate.select(
        _message(mentions=["a"]),
        store,
        "c1",
        role_ids={"a"},
        now=2_000_000_010,
    )

    assert decision.accepted == ["a"]
    assert decision.rejected == []


# -- Router integration ------------------------------------------------------


def _router(tmp_path, **kwargs) -> tuple[Router, SwarmStore]:
    store = SwarmStore(tmp_path)
    return Router(store, {"a", "b", "c"}, **kwargs), store


def test_the_router_does_not_admit_a_third_edge_between_the_same_pair(tmp_path):
    router, store = _router(tmp_path, max_chain_depth=32)
    first = router.admit(_message(mentions=["a"]), inbox_sizes={}).accepted[0]
    for index in range(2):
        _task(
            store,
            f"e{index}",
            chain_id=first.chain_id,
            assignee="b",
            sender="a",
            sender_kind="agent",
        )
    message = _message(
        id="m2", author_kind="agent", author_id="a", task_id=first.id, mentions=["b"]
    )

    admission = router.admit(message, inbox_sizes={})

    assert admission.accepted == []
    assert admission.rejected == [("b", "edge_repeat")]


def test_the_router_keeps_the_first_request_edge(tmp_path):
    router, _ = _router(tmp_path)

    admission = router.admit(_message(mentions=["a"]), inbox_sizes={})

    assert [task.assignee for task in admission.accepted] == ["a"]


def test_the_router_caps_a_request_fanout(tmp_path):
    router, _ = _router(tmp_path, gate=MessageGate(SwarmLimits(max_request_fanout=2)))

    admission = router.admit(_message(mentions=["a", "b", "c"]), inbox_sizes={})

    assert [task.assignee for task in admission.accepted] == ["a", "b"]
    assert admission.rejected == [("c", "fanout_cap")]


def test_the_router_stops_a_chain_at_the_task_budget(tmp_path):
    gate = MessageGate(
        SwarmLimits(
            max_request_fanout=4,
            max_edge_repeats=99,
            storm_burst=99,
        )
    )
    router, _ = _router(tmp_path, max_chain_depth=32, gate=gate)
    budget = gate.limits.max_chain_tasks
    source = None
    created = 0
    rejected: list[tuple[str, str]] = []
    for index in range(budget):
        admission = router.admit(
            _message(
                id=f"m{index}",
                author_kind="agent" if source else "user",
                author_id="a" if source else "you",
                task_id=source.id if source else None,
                mentions=["b", "c"],
            ),
            inbox_sizes={},
        )
        created += len(admission.accepted)
        rejected = admission.rejected
        source = admission.accepted[0] if admission.accepted else None
        if not admission.accepted:
            break

    assert created == budget
    assert rejected
    assert {reason for _, reason in rejected} == {"chain_tasks"}
