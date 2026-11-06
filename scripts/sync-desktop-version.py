#!/usr/bin/env python3
"""Sync the desktop manifests to the project version in pyproject.toml.

pyproject.toml is the single source of truth. The Tauri shell keeps the same
version in four more places (npm package, Tauri config, Cargo manifest and
lock), so a release bump touches them all:

    python3 scripts/sync-desktop-version.py          # write
    python3 scripts/sync-desktop-version.py --check  # exit 1 on drift (CI)

Only the ``[package]`` version in Cargo.toml and the ``zenagent-desktop``
entry in Cargo.lock are touched; dependency versions are left alone.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE_JSON = ROOT / "desktop" / "package.json"
TAURI_CONF = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
CARGO_TOML = ROOT / "desktop" / "src-tauri" / "Cargo.toml"
CARGO_LOCK = ROOT / "desktop" / "src-tauri" / "Cargo.lock"
CRATE = "zenagent-desktop"

VERSION_RE = re.compile(r'^version = "([^"]+)"$', re.MULTILINE)


def project_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    project = text[text.index("[project]") :]
    match = VERSION_RE.search(project)
    if match is None:
        raise SystemExit(f"sync-desktop-version: no version in [project] of {PYPROJECT}")
    return match.group(1)


def json_version(path: Path) -> str:
    return str(json.loads(path.read_text(encoding="utf-8"))["version"])


def cargo_toml_version(text: str) -> str:
    section = text[text.index("[package]") :]
    match = VERSION_RE.search(section)
    if match is None:
        raise SystemExit("sync-desktop-version: no [package] version in Cargo.toml")
    return match.group(1)


def cargo_lock_version(text: str) -> str:
    for block in text.split("\n[[package]]\n"):
        if re.search(rf'^name = "{re.escape(CRATE)}"$', block, re.MULTILINE):
            match = re.search(r'^version = "([^"]+)"$', block, re.MULTILINE)
            if match is not None:
                return match.group(1)
    raise SystemExit(f"sync-desktop-version: {CRATE} is missing from Cargo.lock")


def sync_json(path: Path, version: str) -> bool:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") == version:
        return False
    data["version"] = version
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def sync_cargo_toml(version: str) -> bool:
    text = CARGO_TOML.read_text(encoding="utf-8")
    head, rest = text.split("[package]", 1)
    match = VERSION_RE.search(rest)
    if match is None:
        raise SystemExit("sync-desktop-version: no [package] version in Cargo.toml")
    if match.group(1) == version:
        return False
    rest = rest[: match.start()] + f'version = "{version}"' + rest[match.end() :]
    CARGO_TOML.write_text(head + "[package]" + rest, encoding="utf-8")
    return True


def sync_cargo_lock(version: str) -> bool:
    text = CARGO_LOCK.read_text(encoding="utf-8")
    prefix, sep, tail = text.partition("\n[[package]]\n")
    blocks = (sep + tail).split("\n[[package]]\n")
    changed = False
    for index, block in enumerate(blocks):
        if not re.search(rf'^name = "{re.escape(CRATE)}"$', block, re.MULTILINE):
            continue
        match = re.search(r'^version = "([^"]+)"$', block, re.MULTILINE)
        if match is None or match.group(1) == version:
            continue
        blocks[index] = (
            block[: match.start()] + f'version = "{version}"' + block[match.end() :]
        )
        changed = True
    if changed:
        CARGO_LOCK.write_text(prefix + "\n[[package]]\n".join(blocks), encoding="utf-8")
    return changed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="report drift instead of writing"
    )
    args = parser.parse_args(argv)

    version = project_version()
    current = (
        (PACKAGE_JSON, json_version(PACKAGE_JSON)),
        (TAURI_CONF, json_version(TAURI_CONF)),
        (CARGO_TOML, cargo_toml_version(CARGO_TOML.read_text(encoding="utf-8"))),
        (CARGO_LOCK, cargo_lock_version(CARGO_LOCK.read_text(encoding="utf-8"))),
    )
    drift = [(path.relative_to(ROOT), found) for path, found in current if found != version]

    if args.check:
        if drift:
            for path, found in drift:
                print(
                    f"  drift {path}: {found} (pyproject.toml says {version})",
                    file=sys.stderr,
                )
            print(
                "sync-desktop-version: run `python3 scripts/sync-desktop-version.py`",
                file=sys.stderr,
            )
            return 1
        return 0

    changed = [
        path.relative_to(ROOT)
        for path, updated in (
            (PACKAGE_JSON, sync_json(PACKAGE_JSON, version)),
            (TAURI_CONF, sync_json(TAURI_CONF, version)),
            (CARGO_TOML, sync_cargo_toml(version)),
            (CARGO_LOCK, sync_cargo_lock(version)),
        )
        if updated
    ]
    if changed:
        for path in changed:
            print(f"  set {path} to {version}")
    else:
        print(f"  desktop manifests already at {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
