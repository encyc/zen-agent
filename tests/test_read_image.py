"""The read_image tool: an image file comes back as an image block."""

import base64

import pytest

from zenagent.tools.read_image import READ_IMAGE

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 8


async def call(**args):
    return await READ_IMAGE.execute("call_1", args, None)


def test_declares_the_read_tier_and_one_path_argument():
    assert READ_IMAGE.approval == "read"
    assert READ_IMAGE.parameters == {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path of the image to view."}
        },
        "required": ["path"],
    }


@pytest.mark.parametrize(
    "payload, mime",
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp")],
)
async def test_returns_the_image_itself(tmp_path, payload, mime):
    path = tmp_path / "shot.bin"  # the extension is irrelevant: magic bytes decide
    path.write_bytes(payload)

    result = await call(path=str(path))

    assert not result.is_error
    [text, image] = result.content
    assert base64.b64decode(image.data) == payload
    assert image.mime_type == mime
    assert text.text == f"{path} ({mime}, {len(payload)} bytes)"
    assert result.details == {"path": str(path), "mime_type": mime, "bytes": len(payload)}


@pytest.mark.parametrize(
    "payload",
    [b"definitely not an image", b"BM" + b"\x00" * 30],  # text; a BMP header
)
async def test_refuses_content_that_is_not_a_supported_image(tmp_path, payload):
    path = tmp_path / "fake.png"
    path.write_bytes(payload)

    result = await call(path=str(path))

    assert result.is_error
    assert "does not look like" in result.content[0].text
    assert len(result.content) == 1


async def test_refuses_an_image_over_the_limit(tmp_path):
    path = tmp_path / "big.png"
    path.write_bytes(PNG + b"\x00" * (5 * 1024 * 1024))

    result = await call(path=str(path))

    assert result.is_error
    assert "the limit is 5242880" in result.content[0].text


@pytest.mark.parametrize("kind", ["missing", "directory"])
async def test_missing_file_and_directory_are_errors_not_exceptions(tmp_path, kind):
    path = tmp_path / "nope.png" if kind == "missing" else tmp_path

    result = await call(path=str(path))

    assert result.is_error
    assert len(result.content) == 1


async def test_a_text_only_model_gets_a_placeholder_not_the_bytes(tmp_path):
    """The tool never looks at the model: the wire projection does the downgrade."""
    from zenagent.providers.compat import CompatFlags
    from zenagent.providers.openai_completions import to_request_messages
    from zenagent.types import ToolResultMessage

    path = tmp_path / "shot.png"
    path.write_bytes(PNG)
    result = await call(path=str(path))
    message = ToolResultMessage(
        tool_call_id="c1", tool_name="read_image", content=result.content,
        is_error=False, timestamp=0,
    )

    downgraded = to_request_messages([message], CompatFlags(), supports_vision=False)
    assert downgraded == [{
        "role": "tool",
        "tool_call_id": "c1",
        "content": (
            f"{path} (image/png, {len(PNG)} bytes)\n"
            "(tool image omitted: model does not support images)"
        ),
    }]

    kept = to_request_messages([message], CompatFlags(), supports_vision=True)
    assert [m["role"] for m in kept] == ["tool", "user"]
    url = kept[1]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG


async def test_the_transcript_projection_names_the_image_without_its_bytes(tmp_path):
    from zenagent.app.views import _text_of

    path = tmp_path / "shot.png"
    path.write_bytes(PNG)
    result = await call(path=str(path))

    text = _text_of(result.content)

    assert text == f"{path} (image/png, {len(PNG)} bytes)\n[image image/png]"
    assert result.content[1].data not in text
