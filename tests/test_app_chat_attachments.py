"""Attachments state object: pending images for the next prompt."""

from __future__ import annotations

from zenagent.app.attachments_state import Attachments
from zenagent.types import ImageContent


def _img(data: str = "aGk=") -> ImageContent:
    return ImageContent(data=data, mime_type="image/png")


def test_take_none_snapshots_then_clears():
    box = Attachments()
    box.add(_img("a"))
    box.add(_img("b"))
    taken = box.take(None)
    assert [block.data for block in taken] == ["a", "b"]
    assert box.all() == []

    box.add(_img("c"))
    taken = box.take([_img("explicit")])
    assert [block.data for block in taken] == ["explicit"]
    # An explicit override must not touch the attached list.
    assert [block.data for block in box.all()] == ["c"]


def test_remove_index_and_clear_report_changes():
    box = Attachments()
    box.add(_img("a"))
    box.add(_img("b"))
    assert box.remove(0)
    assert [block.data for block in box.all()] == ["b"]
    assert not box.remove(5)
    assert box.clear()
    assert not box.clear()


def test_prepend_puts_images_back_at_the_front():
    box = Attachments()
    box.add(_img("queued"))
    box.prepend([_img("returned1"), _img("returned2")])
    assert [block.data for block in box.all()] == ["returned1", "returned2", "queued"]
