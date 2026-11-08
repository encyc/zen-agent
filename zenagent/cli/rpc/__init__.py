"""JSONL RPC mode (package): codec + server + cli, re-exported flat.

The pre-split ``zenagent/cli/rpc.py`` module is now a package; every import
path keeps working (tests import ``zenagent.cli.rpc.main`` and friends).
"""

from zenagent.cli.rpc.cli import main
from zenagent.cli.rpc.codec import PROTOCOL_VERSION, serialize_event
from zenagent.cli.rpc.server import RpcServer

__all__ = ["PROTOCOL_VERSION", "RpcServer", "main", "serialize_event"]
