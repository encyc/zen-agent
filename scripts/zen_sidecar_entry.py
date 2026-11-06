"""PyInstaller entry point for the bundled ``zen`` runtime.

The desktop shell spawns ``zen rpc``; the released app ships a frozen copy of
this CLI next to its own executable. The console script in ``pyproject.toml``
points at the same function, so the frozen binary and the installed one answer
identically.
"""

from __future__ import annotations

from zenagent.cli.main import main

raise SystemExit(main())
