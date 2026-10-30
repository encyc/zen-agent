"""Application layer: UI-agnostic services shared by every frontend.

Nothing in this package may import argparse, textual, prompt_toolkit or rich —
REPL, TUI and RPC are all just clients of these objects.
"""

from zenagent.app.chat import ChatController
from zenagent.app.commands import CommandRegistry
from zenagent.app.models import ModelManager
from zenagent.app.permissions import PermissionBroker
from zenagent.app.runtime import Runtime, RuntimeOptions
from zenagent.app.sessions import SessionManager
from zenagent.app.views import (
    MessageView,
    RunStatus,
    ToolCallView,
    messages_to_views,
    usage_total,
)

__all__ = [
    "ChatController",
    "CommandRegistry",
    "MessageView",
    "ModelManager",
    "PermissionBroker",
    "RunStatus",
    "Runtime",
    "RuntimeOptions",
    "SessionManager",
    "ToolCallView",
    "messages_to_views",
    "usage_total",
]
