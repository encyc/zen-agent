from dataclasses import replace

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


def _router(tmp_path, *, role_ids=None, **kwargs) -> tuple[Router, SwarmStore]:
    store = SwarmStore(tmp_path)
    router = Router(store, role_ids or {"research", "coder"}, **kwargs)
    return router, store


def test_user_mention_creates_task(tmp_path):
    router, store = _router(tmp_path)

    admission = router.admit(_message(mentions=["research"]), inbox_sizes={})

    assert [task.assignee for task in admission.accepted] == ["research"]
    task = admission.accepted[0]
    assert task.state == "queued"
    assert task.chain_depth == 1
    assert task.source_message_id == "m1"
    assert task.created_by_kind == "user"
    assert store.tasks[task.id] == task


def test_unknown_mention_creates_nothing(tmp_path):
    router, store = _router(tmp_path)

    admission = router.admit(_message(mentions=["nobody"]), inbox_sizes={})

    assert admission.accepted == []
    assert admission.rejected == []
    assert store.tasks == {}


def test_chain_depth_cap_rejects(tmp_path):
    router, _ = _router(tmp_path)

    admission = router.admit(
        _message(mentions=["research"], chain_depth=8), inbox_sizes={}
    )

    assert admission.accepted == []
    assert admission.rejected == [("research", "chain_depth")]


def test_chain_cost_cap_rejects(tmp_path):
    router, store = _router(tmp_path)
    source = TaskRecord(
        id="t0",
        assignee="coder",
        state="running",
        source_message_id="m0",
        chain_id="c1",
        chain_cost=5.0,
    )
    store.append_task(replace(source, state="queued"))
    store.append_task(source)

    admission = router.admit(
        _message(mentions=["research"], task_id="t0", chain_depth=1), inbox_sizes={}
    )

    assert admission.accepted == []
    assert admission.rejected == [("research", "chain_cost")]


def test_chain_cost_gate_counts_sibling_branches(tmp_path):
    router, store = _router(tmp_path)
    for task_id in ("t1", "t2"):
        queued = TaskRecord(
            id=task_id,
            assignee="coder",
            state="queued",
            source_message_id="m0",
            chain_id="c1",
            chain_cost=0.0,
        )
        store.append_task(queued)
        store.append_task(replace(queued, state="running"))
        store.append_task(replace(queued, state="done", chain_cost=3.0))

    admission = router.admit(
        _message(mentions=["research"], task_id="t2", chain_depth=1),
        inbox_sizes={},
    )

    assert admission.accepted == []
    assert admission.rejected == [("research", "chain_cost")]


def test_inbox_cap_rejects(tmp_path):
    router, _ = _router(tmp_path)

    admission = router.admit(
        _message(mentions=["research"]), inbox_sizes={"research": 16}
    )

    assert admission.accepted == []
    assert admission.rejected == [("research", "inbox_full")]


def test_tasks_keep_message_order(tmp_path):
    router, store = _router(tmp_path)

    first = router.admit(_message(id="m1", mentions=["coder"]), inbox_sizes={})
    second = router.admit(_message(id="m2", mentions=["research"]), inbox_sizes={})

    assert [task.assignee for task in [*first.accepted, *second.accepted]] == [
        "coder",
        "research",
    ]
    assert list(store.tasks) == [first.accepted[0].id, second.accepted[0].id]


def test_task_carries_work_item(tmp_path):
    store = SwarmStore(tmp_path)
    router = Router(store, {"research"}, work_items=_items(tmp_path))

    admission = router.admit(
        _message(mentions=["research"]), inbox_sizes={}, work_item_id="w1"
    )

    assert admission.accepted[0].work_item_id == "w1"


def _items(root):
    from zenagent.app.swarm.workitems import WorkItemStore

    store = SwarmStore(root)
    items = WorkItemStore(store)
    items.load()
    return items


def test_crossing_work_items_rebases_the_chain(tmp_path):
    store = SwarmStore(tmp_path)
    router = Router(store, {"research", "coder"}, work_items=_items(tmp_path))
    first = router.admit(
        _message(mentions=["research"]), inbox_sizes={}, work_item_id="w1"
    ).accepted[0]
    store.append_task(replace(first, state="running"))
    spent = replace(first, state="done", chain_depth=3, chain_cost=4.5)
    store.append_task(spent)

    follow = _message(id="m2", mentions=["coder"], task_id=first.id)
    task = router.admit(follow, inbox_sizes={}, work_item_id="w2").accepted[0]

    assert task.chain_id != spent.chain_id
    assert task.chain_depth == 1
    assert task.chain_cost == 0.0
    assert task.work_item_id == "w2"


def test_same_work_item_keeps_the_chain(tmp_path):
    store = SwarmStore(tmp_path)
    router = Router(store, {"research", "coder"}, work_items=_items(tmp_path))
    first = router.admit(
        _message(mentions=["research"]), inbox_sizes={}, work_item_id="w1"
    ).accepted[0]
    store.append_task(replace(first, state="running"))
    store.append_task(replace(first, state="done", chain_depth=2, chain_cost=1.0))

    follow = _message(id="m2", mentions=["coder"], task_id=first.id, chain_depth=2)
    task = router.admit(follow, inbox_sizes={}, work_item_id="w1").accepted[0]

    assert task.chain_id == first.chain_id
    assert task.chain_depth == 3
