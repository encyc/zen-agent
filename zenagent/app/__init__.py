"""Application layer: UI-agnostic services shared by every frontend.

Nothing in this package may import argparse, textual, prompt_toolkit or rich —
REPL, TUI and RPC are all just clients of these objects.

Re-exports are lazy (PEP 562): importing ``zenagent.app`` must not pull in
chat, runtime or sessions transitively, or the package's own modules re-enter
it while partially initialized — the anchor of the 15-node cycle the
2026-11-14 audit found (docs/reviews/2026-11-14-architecture-audit.md, P0-2).
"""

import importlib

#: Re-exported API surface: public name -> the module that defines it.
_LAZY = {
    "ChatController": "zenagent.app.chat",
    "CommandRegistry": "zenagent.app.commands",
    "ModelManager": "zenagent.app.models",
    "PermissionBroker": "zenagent.app.permissions",
    "Runtime": "zenagent.app.runtime",
    "RuntimeOptions": "zenagent.app.runtime",
    "SessionManager": "zenagent.app.sessions",
    "DividerView": "zenagent.app.views",
    "MessageView": "zenagent.app.views",
    "RunStatus": "zenagent.app.views",
    "ToolCallView": "zenagent.app.views",
    "TranscriptView": "zenagent.app.views",
    "messages_to_views": "zenagent.app.views",
    "usage_total": "zenagent.app.views",
}

__all__ = [
    "ChatController",
    "CommandRegistry",
    "DividerView",
    "MessageView",
    "ModelManager",
    "PermissionBroker",
    "RunStatus",
    "Runtime",
    "RuntimeOptions",
    "SessionManager",
    "ToolCallView",
    "TranscriptView",
    "messages_to_views",
    "usage_total",
]
assert set(__all__) == set(_LAZY), "the lazy map and __all__ must stay in sync"


def __getattr__(name: str):
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value  # cache: later lookups skip the hook
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
