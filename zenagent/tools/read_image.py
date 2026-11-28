"""The ``read_image`` tool: an image file returned as an image block.

``read_file`` is text-only by contract (paging, the read-before-write gate);
images have none of that. This tool hands the bytes to the model the way the
attachment channel does — validated by magic bytes, capped at
``MAX_IMAGE_BYTES`` — and lets the kernel's existing image path do the rest:
the result budget passes image blocks through, the session spills large
payloads to the blob store, and each provider adapter hoists tool images to a
vision model (or writes a placeholder for a text-only one).

The tool never looks at the model: a model without image input gets the
wire projection's placeholder, the same as an MCP result that carries an
image.
"""

from __future__ import annotations

import base64
from pathlib import Path

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.attachments import MAX_IMAGE_BYTES, read_image_argument
from zenagent.prompts import tool_description
from zenagent.tools._result import text_result

__all__ = ["READ_IMAGE"]


async def _read_image(
    tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    target = Path(args["path"]).expanduser()
    try:
        block = read_image_argument(target)
    except (ValueError, OSError) as exc:
        return text_result(str(exc), is_error=True)
    size = len(base64.b64decode(block.data))
    return ToolResultAgent(
        content=[
            text_result(f"{target} ({block.mime_type}, {size} bytes)").content[0],
            block,
        ],
        details={"path": str(target), "mime_type": block.mime_type, "bytes": size},
    )


READ_IMAGE = AgentTool(
    name="read_image",
    description=tool_description(
        "read_image", max_mb=str(MAX_IMAGE_BYTES // (1024 * 1024))
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path of the image to view.",
            }
        },
        "required": ["path"],
    },
    execute=_read_image,
    approval="read",
)
