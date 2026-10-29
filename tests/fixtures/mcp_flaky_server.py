#!/usr/bin/env python
"""An MCP server that only starts once its sentinel file exists.

Used to reproduce the real-world case: the server is down at session start and
becomes available later.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sentinel = os.environ.get("MCP_READY_FILE")
if sentinel and not Path(sentinel).exists():
    sys.stderr.write("flaky server not ready yet\n")
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_echo_server import main

if __name__ == "__main__":
    main()
