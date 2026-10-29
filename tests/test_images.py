"""Image attachments: magic-byte detection, size limit, vision gating."""

import base64

import pytest

from pyagent.cli.images import MAX_IMAGE_BYTES, read_image_argument

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 8


@pytest.mark.parametrize(
    "payload, mime",
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp")],
)
def test_sniffs_supported_formats(tmp_path, payload, mime):
    path = tmp_path / "x.bin"  # extension is irrelevant
    path.write_bytes(payload)
    block = read_image_argument(path)
    assert block.mime_type == mime
    assert base64.b64decode(block.data) == payload


def test_rejects_non_image_content(tmp_path):
    path = tmp_path / "fake.png"
    path.write_text("definitely not an image")
    with pytest.raises(ValueError, match="does not look like"):
        read_image_argument(path)


def test_rejects_oversized_images(tmp_path):
    path = tmp_path / "big.png"
    path.write_bytes(PNG + b"\x00" * 32)
    with pytest.raises(ValueError, match="limit"):
        read_image_argument(path, max_bytes=10)


def test_rejects_missing_files(tmp_path):
    with pytest.raises(ValueError, match="cannot read image"):
        read_image_argument(tmp_path / "nope.png")


def test_default_limit_is_five_megabytes():
    assert MAX_IMAGE_BYTES == 5 * 1024 * 1024
