"""``pyagent doctor``: check the things that usually break a session.

Read-only and offline: no network probes, no model calls. It answers "did I set
up the environment correctly?" — keys present, catalog loadable, config parseable,
sessions writable, terminal interactive.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass

from pyagent import __version__
from pyagent.agent.session import sessions_dir
from pyagent.config import UserConfig, apply_user_config, config_path, load_user_config
from pyagent.providers.catalog import load_catalog

__all__ = ["Check", "collect_checks", "run_doctor"]


@dataclass
class Check:
    """One diagnostic row."""

    name: str
    ok: bool
    detail: str


def _check_sessions_writable() -> Check:
    directory = sessions_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".doctor-", delete=True):
            pass
    except OSError as exc:
        return Check("sessions", False, f"{directory} is not writable ({exc})")
    return Check("sessions", True, str(directory))


def collect_checks(env: Mapping[str, str] | None = None) -> list[Check]:
    """Run every local check and return the rows in display order."""
    values = os.environ if env is None else env
    checks: list[Check] = [
        Check("python", sys.version_info >= (3, 11), sys.version.split()[0]),
        Check("pyagent", True, __version__),
    ]

    try:
        user_config = load_user_config()
        detail = user_config.default_model or "(no default_model)"
        checks.append(Check("config", True, f"{config_path()} · {detail}"))
    except ValueError as exc:
        user_config = UserConfig()
        checks.append(Check("config", False, str(exc)))

    warnings: list[str] = []
    try:
        catalog = apply_user_config(load_catalog(), user_config, warnings)
        providers = ", ".join(catalog)
        checks.append(Check("catalog", True, f"{len(catalog)} providers: {providers}"))
        for warning in warnings:
            checks.append(Check("catalog", True, warning))
    except Exception as exc:  # noqa: BLE001 - the point is to report, not raise
        catalog = {}
        checks.append(Check("catalog", False, f"cannot load: {exc}"))

    checks.append(_check_sessions_writable())

    for provider in catalog.values():
        present = bool(values.get(provider.api_key_env))
        detail = provider.api_key_env + (" is set" if present else " is missing")
        checks.append(Check(f"key:{provider.id}", present, detail))

    checks.append(
        Check(
            "terminal",
            True,
            "interactive" if sys.stdout.isatty() else "non-interactive (piped)",
        )
    )
    return checks


async def run_doctor(print_fn=print) -> int:
    """Print the checks and return 0 when everything passed, else 1."""
    checks = collect_checks()
    width = max(len(check.name) for check in checks)
    for check in checks:
        mark = "ok  " if check.ok else "FAIL"
        print_fn(f"{mark}  {check.name:<{width}}  {check.detail}")
    failures = [check for check in checks if not check.ok]
    print_fn(f"\n{len(checks) - len(failures)}/{len(checks)} checks passed")
    return 1 if failures else 0
