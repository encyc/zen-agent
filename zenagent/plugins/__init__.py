"""Declarative capability packs: a directory plus ``manifest.json``.

The package is a base-cluster extension surface. It discovers, validates and
stores plugins, and emits raw contribution dicts; wiring them into the MCP
client, skills and extensions is the app layer's job. That keeps this package
free of imports from ``mcp/``, ``app/`` and ``cli/``.
"""

from __future__ import annotations

__all__: list[str] = []
