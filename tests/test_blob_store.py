"""Content-addressed blob offload: large image payloads leave the JSONL.

The transcript line keeps a ``{"blob": "<sha256>"}`` ref instead of the base64
bytes; the payload lives in ``<sessions dir>/_blobs/<sha>.b64``. Loading
hydrates the data back — unconditionally, so bare ``Session`` readers
(export, listings) stay lossless — and degrades to an empty image with a
warning when a blob file is missing.
"""

import json

from zenagent.agent.blob_store import (
    BLOB_MIN_CHARS,
    BlobStore,
    blob_root_for,
)
from zenagent.agent.session import Session
from zenagent.app import records
from zenagent.types import ImageContent, UserMessage


def big_image(mime: str = "image/png") -> ImageContent:
    # Well past BLOB_MIN_CHARS: base64 of ~6 KiB of bytes.
    return ImageContent(data="A" * 8192, mime_type=mime)


def user_with_image(image: ImageContent, text: str = "look") -> UserMessage:
    return UserMessage(
        content=[ImageContent(data=image.data, mime_type=image.mime_type)],
        timestamp=1,
    )


def test_roundtrip_offloads_and_hydrates(tmp_path):
    path = tmp_path / "s.jsonl"
    image = big_image()
    session = Session(path, blob_store=BlobStore(tmp_path / "_blobs"))

    session._append_sync(user_with_image(image))

    line = json.loads(path.read_text(encoding="utf-8").splitlines()[1])
    block = line["content"][0]
    assert "data" not in block
    assert set(block["blob"]) <= set("0123456789abcdef") and len(block["blob"]) == 64
    blob_file = tmp_path / "_blobs" / f"{block['blob']}.b64"
    assert blob_file.read_text(encoding="utf-8") == image.data

    # A bare Session (no store injected) hydrates from the default root.
    messages = Session(path).load()
    assert messages[0].content[0].data == image.data
    assert messages[0].content[0].mime_type == image.mime_type
    assert not Session(path).warnings


def test_inline_images_load_unchanged_and_no_dir_created(tmp_path):
    path = tmp_path / "legacy.jsonl"
    inline = {
        "role": "user",
        "content": [{"type": "image", "data": "Q" * 8192, "mime_type": "image/png"}],
        "timestamp": 1,
    }
    path.write_text(json.dumps(inline) + "\n", encoding="utf-8")

    messages = Session(path).load()

    assert messages[0].content[0].data == "Q" * 8192
    assert not (tmp_path / "_blobs").exists()


def test_missing_blob_degrades_to_empty_data_with_warning(tmp_path):
    path = tmp_path / "gone.jsonl"
    ref = "e" * 64
    line = {
        "role": "user",
        "content": [{"type": "image", "blob": ref, "mime_type": "image/png"}],
        "timestamp": 1,
    }
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")

    session = Session(path)
    messages = session.load()

    assert messages[0].content[0].data == ""
    assert any(ref in warning for warning in session.warnings)
    assert any("missing image blob" in warning for warning in session.warnings)


def test_small_images_stay_inline(tmp_path):
    path = tmp_path / "small.jsonl"
    session = Session(path, blob_store=BlobStore(tmp_path / "_blobs"))

    session._append_sync(
        UserMessage(
            content=[ImageContent(data="B" * (BLOB_MIN_CHARS - 1), mime_type="image/png")],
            timestamp=1,
        )
    )

    line = json.loads(path.read_text(encoding="utf-8").splitlines()[1])
    assert line["content"][0]["data"] == "B" * (BLOB_MIN_CHARS - 1)
    assert "blob" not in line["content"][0]
    assert not (tmp_path / "_blobs").exists()


def test_dedup_across_appends(tmp_path):
    image = big_image()
    one = Session(tmp_path / "one.jsonl", blob_store=BlobStore(tmp_path / "_blobs"))
    two = Session(tmp_path / "two.jsonl", blob_store=BlobStore(tmp_path / "_blobs"))

    one._append_sync(user_with_image(image))
    two._append_sync(user_with_image(image))

    assert list((tmp_path / "_blobs").glob("*.b64")) == [
        tmp_path / "_blobs" / f"{__import__('hashlib').sha256(image.data.encode()).hexdigest()}.b64"
    ]


def test_open_session_wires_the_store(tmp_path):
    path = tmp_path / "wired.jsonl"

    session = records.open_session(path)
    session._append_sync(user_with_image(big_image()))

    line = json.loads(path.read_text(encoding="utf-8").splitlines()[1])
    assert "blob" in line["content"][0]
    assert "data" not in line["content"][0]


def test_offload_failure_falls_back_inline(tmp_path, monkeypatch):
    path = tmp_path / "fallback.jsonl"
    session = Session(path, blob_store=BlobStore(tmp_path / "_blobs"))

    import zenagent.agent.blob_store as blob_module

    def broken_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(blob_module.os, "replace", broken_replace)
    image = big_image()

    session._append_sync(user_with_image(image))

    line = json.loads(path.read_text(encoding="utf-8").splitlines()[1])
    assert line["content"][0]["data"] == image.data
    assert "blob" not in line["content"][0]


def test_blob_root_is_siblings(tmp_path):
    assert blob_root_for(tmp_path / "x.jsonl") == tmp_path / "_blobs"
