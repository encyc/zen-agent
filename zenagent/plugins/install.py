"""Install and uninstall plugins from a directory or a zip.

Two invariants: every check runs before anything lands in ``installed/``, and a
reinstall swaps the whole directory atomically — a half-copied plugin never
appears as installed.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from zenagent.plugins import discovery
from zenagent.plugins.manifest import PluginManifest, parse_manifest
from zenagent.plugins.state import clear_plugin

__all__ = [
    "MAX_PLUGIN_BYTES",
    "MAX_ZIP_MEMBERS",
    "install_plugin",
    "uninstall_plugin",
]

MAX_PLUGIN_BYTES = 10 * 1024 * 1024
MAX_ZIP_MEMBERS = 500

_SYMLINK_MODE = 0o120000
_MANIFEST_NAME = "manifest.json"


def install_plugin(source: Path | str, *, root: Path | None = None) -> PluginManifest:
    base = Path(root) if root is not None else discovery.installed_root()
    source_path = Path(source)
    if not source_path.exists():
        raise ValueError(f"plugin source not found: {source_path}")

    with tempfile.TemporaryDirectory(prefix="zen-plugin-") as scratch:
        scratch_path = Path(scratch)
        if source_path.is_dir():
            plugin_root = source_path
        elif source_path.is_file() and zipfile.is_zipfile(source_path):
            extracted = scratch_path / "extracted"
            extracted.mkdir()
            _extract_zip(source_path, extracted)
            plugin_root = _unwrapped_root(extracted)
        else:
            raise ValueError(
                f"plugin source must be a directory or a zip: {source_path}"
            )

        manifest = parse_manifest(plugin_root)

        target = base / manifest.id
        staging = base / f".install-{uuid.uuid4().hex}"
        try:
            base.mkdir(parents=True, exist_ok=True)
            shutil.copytree(plugin_root, staging)
            shutil.rmtree(target, ignore_errors=True)
            os.replace(staging, target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    return manifest


def uninstall_plugin(plugin_id: str, *, root: Path | None = None) -> None:
    if not plugin_id or plugin_id in (".", "..") or "/" in plugin_id or "\\" in plugin_id:
        raise ValueError(f"invalid plugin id {plugin_id!r}")
    base = Path(root) if root is not None else discovery.installed_root()
    target = base / plugin_id
    if not target.is_dir():
        raise ValueError(f"plugin {plugin_id!r} is not installed")
    shutil.rmtree(target)
    clear_plugin(plugin_id)


def _extract_zip(archive: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as handle:
            members = handle.infolist()
            if len(members) > MAX_ZIP_MEMBERS:
                raise ValueError(
                    f"plugin zip has too many members "
                    f"({len(members)} > {MAX_ZIP_MEMBERS})"
                )
            total = sum(info.file_size for info in members)
            if total > MAX_PLUGIN_BYTES:
                raise ValueError(
                    f"plugin zip is too large ({total} > {MAX_PLUGIN_BYTES} bytes)"
                )
            for info in members:
                _check_member(info)
            handle.extractall(destination)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"cannot read plugin zip {archive}: {exc}") from exc


def _check_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    if (info.external_attr >> 16) & 0o170000 == _SYMLINK_MODE:
        raise ValueError(f"plugin zip contains a symlink: {name}")
    member = PurePosixPath(name)
    if member.is_absolute() or ".." in member.parts:
        raise ValueError(f"plugin zip member escapes the archive: {name}")


def _unwrapped_root(extracted: Path) -> Path:
    """Accept ``manifest.json`` at the root or in a single top-level directory."""
    if (extracted / _MANIFEST_NAME).is_file():
        return extracted
    entries = list(extracted.iterdir())
    if (
        len(entries) == 1
        and entries[0].is_dir()
        and (entries[0] / _MANIFEST_NAME).is_file()
    ):
        return entries[0]
    raise ValueError(
        f"plugin zip must contain {_MANIFEST_NAME} at its root "
        "or inside a single top-level directory"
    )
