"""The rpc ops mixins are split by domain (audit P2 Task 30).

ops_config.py had grown into every config-ish wire kind (models, secrets,
skills, mcp, limits, project, commands — 969 lines). One domain per mixin
keeps the next handler landing in the right file; server.py stays the
single dispatch point.
"""

from __future__ import annotations

import inspect

from zenagent.cli.rpc.ops_config import ConfigOps
from zenagent.cli.rpc.ops_secrets import SecretsOps
from zenagent.cli.rpc.ops_skills import SkillsOps
from zenagent.cli.rpc.server import RpcServer


def _methods(cls) -> set[str]:
    return {
        name for name, _ in inspect.getmembers(cls, predicate=inspect.isfunction)
    }


def test_secrets_ops_owns_secrets_and_limits_handlers():
    body = _methods(SecretsOps)
    assert {
        "_model_catalog",
        "_known_secret_envs",
        "_secrets",
        "_secrets_result",
        "_secrets_test",
        "_reload_key",
        "_knob_value",
        "_limits",
        "_limits_result",
        "_adopt_limits",
        "_drop_idle_swarm_rooms",
    } <= body


def test_skills_ops_owns_skills_and_mcp_handlers():
    body = _methods(SkillsOps)
    assert {
        "_discover",
        "_skill_rows",
        "_skills",
        "_skills_result",
        "_mcp",
        "_mcp_result",
        "_mcp_rows",
        "_mcp_layer",
        "_string_map",
        "_effective_mcp",
    } <= body


def test_config_ops_keeps_models_mode_project_and_commands():
    body = _methods(ConfigOps)
    assert {
        "_models_result",
        "_commands_result",
        "_model",
        "_mode",
        "_project",
        "_command",
        "_skill_command",
    } <= body
    # No secrets/skills/mcp/limits handlers left behind on the config mixin.
    assert not body & {"_secrets", "_skills", "_mcp", "_limits"}


def test_rpc_server_composes_all_three():
    assert issubclass(RpcServer, (ConfigOps, SecretsOps, SkillsOps))
