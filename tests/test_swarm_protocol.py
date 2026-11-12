from zenagent.app.swarm.protocol import injected_prompt
from zenagent.app.swarm.room import RoomMessage, RoomRef, TaskRecord


def _task() -> TaskRecord:
    return TaskRecord(
        id="t1", assignee="research", state="running", chain_id="c1", chain_depth=1
    )


def _message(**overrides) -> RoomMessage:
    fields = {
        "id": "m1",
        "ts": 1,
        "author_kind": "user",
        "author_id": "you",
        "text": "@research 复核一下",
    }
    fields.update(overrides)
    return RoomMessage(**fields)


def test_prompt_is_unchanged_without_a_ref_or_images():
    prompt = injected_prompt(_message(), _task())

    assert prompt.endswith("Message:\n@research 复核一下")
    assert "Quoted" not in prompt
    assert "Attached images" not in prompt


def test_prompt_renders_the_quote_before_the_message():
    prompt = injected_prompt(
        _message(
            ref=RoomRef(
                id="m0", author_kind="agent", author_id="research", text="结论是 Z\n第二行"
            )
        ),
        _task(),
    )

    assert "Quoted agent:research (m0):\n> 结论是 Z\n> 第二行\n\nMessage:\n@research 复核一下" in prompt


def test_prompt_lists_attachment_names_not_absolute_paths():
    prompt = injected_prompt(_message(images=["/tmp/a/b.png"]), _task())

    assert "Attached images:\n- b.png\n" in prompt
    assert "/tmp/a/b.png" not in prompt
