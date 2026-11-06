"""The sidecar builder: the parts a machine can check without freezing.

The build itself needs PyInstaller and a real interpreter, so it runs on the
runner (and in the release workflow's smoke step); these tests pin the naming,
the version source and the environment the smoke check needs.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def builder():
    script = Path(__file__).resolve().parents[1] / "scripts" / "build-sidecar.py"
    spec = importlib.util.spec_from_file_location("zenagent_sidecar_builder", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sidecar_name_carries_the_triple_and_the_windows_suffix():
    assert builder().sidecar_name("aarch64-apple-darwin") == "zen-aarch64-apple-darwin"
    assert builder().sidecar_name("x86_64-pc-windows-msvc") == "zen-x86_64-pc-windows-msvc.exe"


def test_host_triple_reads_the_rustc_host_line():
    output = "rustc 1.85.0\ntarget: x86_64-unknown-linux-gnu\nhost: x86_64-unknown-linux-gnu\n"
    assert builder().host_triple(output) == "x86_64-unknown-linux-gnu"


def test_host_triple_rejects_output_without_a_host_line():
    with pytest.raises(ValueError):
        builder().host_triple("rustc 1.85.0\n")


def test_smoke_env_drops_provider_keys_and_pins_home(tmp_path):
    env = builder().smoke_env({"DEEPSEEK_API_KEY": "x", "PATH": "/bin"}, tmp_path)
    assert "DEEPSEEK_API_KEY" not in env
    assert env["PATH"] == "/bin"
    assert env["HOME"] == str(tmp_path)


def test_missing_key_ok_accepts_the_bootstrap_error_only():
    assert builder().missing_key_ok(
        "zenagent: missing API key for deepseek-flash (set DEEPSEEK_API_KEY)\n"
    )
    assert not builder().missing_key_ok(
        "Traceback (most recent call last):\nModuleNotFoundError: rich\n"
    )
    assert not builder().missing_key_ok("")
