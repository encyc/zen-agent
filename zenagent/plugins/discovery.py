"""Plugin discovery: scan a directory for subdirectories with a manifest.

Discovery is defensive on purpose — a plugin installed by an older build, or a
directory a user copied in by hand, must never take the whole list down. A
broken manifest becomes a warning and that plugin is skipped.
"""

from __future__ import annotations

from pathlib import Path

from zenagent.plugins.manifest import PluginManifest, parse_manifest

__all__ = ["discover_plugins", "installed_root"]


def installed_root() -> Path:
    return Path.home() / ".zenagent" / "plugins" / "installed"


def discover_plugins(
    root: Path | None = None,
) -> tuple[list[PluginManifest], list[str]]:
    base = Path(root) if root is not None else installed_root()
    manifests: list[PluginManifest] = []
    warnings: list[str] = []
    if not base.is_dir():
        return manifests, warnings
    for entry in sorted(base.iterdir()):
        if entry.name.startswith("."):
            continue
        if not entry.is_dir() or not (entry / "manifest.json").is_file():
            continue
        try:
            manifests.append(parse_manifest(entry))
        except ValueError as exc:
            warnings.append(f"plugin {entry.name}: {exc}")
    manifests.sort(key=lambda manifest: manifest.id)
    return manifests, warnings
