#!/usr/bin/env python3
"""Freeze the ``zen`` CLI into the sidecar binary the desktop app ships.

    python3 scripts/build-sidecar.py                  # host triple, smoke-tested
    python3 scripts/build-sidecar.py --target aarch64-apple-darwin
    python3 scripts/build-sidecar.py --skip-smoke

The released GUI carries its own runtime: the Tauri shell in ``desktop/``
spawns ``zen rpc`` from the directory holding its own executable, so an
installer that lacks this binary boots into "no runtime". The binary is named
``zen-<target-triple>`` because that is what ``bundle.externalBin`` requires;
Tauri strips the suffix when it bundles.

PyInstaller cannot cross-compile, so each platform/architecture builds on its
own runner. The build uses an isolated, **non-editable** venv: an editable
install makes PyInstaller's module collection follow ``.pth`` files and drop
submodules it never sees imported at the top level.

The smoke check is the real gate. A frozen ``zen`` can answer ``--version``
and still die on the first prompt when ``catalog.yaml`` or
``zenagent/prompts/blocks`` was not collected. Running ``zen rpc`` with no
provider key set fails inside ``Runtime.bootstrap`` with
``missing API key for <model> (set <ENV>)`` — the model name comes out of
``catalog.yaml``, so that message proves the package data survived. It needs
no network and no key.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = Path(__file__).resolve().parent / "zen_sidecar_entry.py"
DEFAULT_OUT = ROOT / "desktop" / "src-tauri" / "binaries"
BUILD_PYTHON = "3.12"
PYINSTALLER = "pyinstaller>=6.10"

__all__ = [
    "BUILD_PYTHON",
    "DEFAULT_OUT",
    "build",
    "host_triple",
    "main",
    "missing_key_ok",
    "project_version",
    "sidecar_name",
    "smoke",
    "smoke_env",
]

_VERSION_RE = re.compile(r'^version = "([^"]+)"$', re.MULTILINE)
_HOST_RE = re.compile(r"^host: (.+)$", re.MULTILINE)
#: The one error that proves the frozen package is complete.
_MISSING_KEY_RE = re.compile(r"missing API key for .+ \(set [A-Z0-9_]+\)")


def project_version(pyproject: Path = ROOT / "pyproject.toml") -> str:
    """The ``[project]`` version — the single source every artifact must match."""
    text = pyproject.read_text(encoding="utf-8")
    project = text[text.index("[project]") :]
    match = _VERSION_RE.search(project)
    if match is None:
        raise ValueError(f"no [project] version in {pyproject}")
    return match.group(1)


def sidecar_name(triple: str) -> str:
    """``zen-<triple>`` (plus ``.exe`` on Windows) — the name externalBin wants."""
    suffix = ".exe" if "-windows-" in triple else ""
    return f"zen-{triple}{suffix}"


def host_triple(rustc_output: str) -> str:
    """Parse the ``host:`` line out of ``rustc -vV``."""
    match = _HOST_RE.search(rustc_output)
    if match is None:
        raise ValueError("rustc -vV printed no host line")
    return match.group(1).strip()


def smoke_env(base: Mapping[str, str], home: Path) -> dict[str, str]:
    """A clean environment: no provider keys, isolated HOME and update cache."""
    env = {key: value for key, value in base.items() if not key.endswith("_API_KEY")}
    env.pop("ZENAGENT_NO_UPDATE_CHECK", None)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["ZENAGENT_UPDATE_CACHE_PATH"] = str(Path(home) / "update.json")
    return env


def missing_key_ok(stderr: str) -> bool:
    """True when the run reached ``Runtime.bootstrap`` and only lacked a key."""
    return _MISSING_KEY_RE.search(stderr) is not None


def venv_python(venv: Path) -> Path:
    """The interpreter inside a venv, whichever layout the platform uses."""
    candidate = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not candidate.is_file():
        raise SystemExit(f"build-sidecar: no interpreter at {candidate}")
    return candidate


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    printable = " ".join(command)
    print(f"  $ {printable}", flush=True)
    result = subprocess.run(command, cwd=cwd, check=False)
    if result.returncode != 0:
        raise SystemExit(f"build-sidecar: command failed ({result.returncode}): {printable}")


def build(out_dir: Path, triple: str, project_root: Path = ROOT) -> Path:
    """Freeze ``zen`` and drop ``<out_dir>/zen-<triple>``; return that path."""
    work = Path(tempfile.mkdtemp(prefix="zen-sidecar-"))
    try:
        venv = work / ".venv"
        _run(["uv", "venv", "--python", BUILD_PYTHON, str(venv)])
        python = venv_python(venv)
        _run(["uv", "pip", "install", "--python", str(python), str(project_root)])
        _run(["uv", "pip", "install", "--python", str(python), PYINSTALLER])
        _run(
            [
                str(python),
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--onefile",
                "--name",
                "zen",
                "--collect-data",
                "zenagent",
                "--exclude-module",
                "textual",
                "--exclude-module",
                "rich",
                "--exclude-module",
                "prompt_toolkit",
                "--distpath",
                str(work / "dist"),
                "--workpath",
                str(work / "build"),
                "--specpath",
                str(work),
                str(ENTRY),
            ],
            cwd=project_root,
        )
        produced = work / "dist" / ("zen.exe" if os.name == "nt" else "zen")
        if not produced.is_file():
            raise SystemExit(f"build-sidecar: PyInstaller produced no {produced}")
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / sidecar_name(triple)
        shutil.copy2(produced, target)
        target.chmod(0o755)
        print(f"  ok    {target} ({target.stat().st_size / 1_048_576:.1f} MB)")
        return target
    finally:
        shutil.rmtree(work, ignore_errors=True)


def smoke(binary: Path, version: str, home: Path) -> None:
    """Prove the frozen binary runs, reads its data files and reaches bootstrap."""
    env = smoke_env(os.environ, home)

    reported = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, env=env, check=False
    )
    banner = reported.stdout + reported.stderr
    if version not in banner:
        raise SystemExit(
            "build-sidecar: smoke failed — `zen --version` did not report "
            f"{version}: {banner.strip()!r}"
        )

    booted = subprocess.run(
        [str(binary), "rpc", "--no-mcp"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if booted.returncode != 2 or not missing_key_ok(booted.stderr):
        raise SystemExit(
            "build-sidecar: smoke failed — `zen rpc` did not reach bootstrap "
            f"(exit {booted.returncode}): "
            f"{(booted.stderr or booted.stdout).strip()[-800:]!r}"
        )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out-dir", type=Path, default=DEFAULT_OUT, help="where the sidecar lands"
    )
    parser.add_argument(
        "--target",
        default=None,
        help="target triple (defaults to the host); CI passes the matrix target",
    )
    parser.add_argument(
        "--skip-smoke", action="store_true", help="skip the post-build boot check"
    )
    args = parser.parse_args(argv)

    if args.target is not None:
        triple = args.target
    else:
        rustc = subprocess.run(
            ["rustc", "-vV"], capture_output=True, text=True, check=False
        )
        if rustc.returncode != 0:
            raise SystemExit("build-sidecar: rustc -vV failed; pass --target")
        triple = host_triple(rustc.stdout)

    version = project_version()
    binary = build(args.out_dir, triple)
    if not args.skip_smoke:
        with tempfile.TemporaryDirectory(prefix="zen-sidecar-smoke-") as home:
            smoke(binary, version, Path(home))
        print(f"  ok    smoke: {binary.name} answered --version and reached bootstrap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
