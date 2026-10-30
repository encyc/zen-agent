"""Compatibility import: permission logic lives in the app layer now."""

from zenagent.app.permissions import (
    READ_ONLY_TOOLS,
    PermissionGate,
    dangerous_command_reason,
    matches_any,
)

__all__ = [
    "READ_ONLY_TOOLS",
    "PermissionGate",
    "dangerous_command_reason",
    "matches_any",
]
