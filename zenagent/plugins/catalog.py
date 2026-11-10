"""The builtin catalog shipped inside the package.

Catalog entries are the connectors zen-agent ships itself: each is a normal
plugin directory under ``zenagent/plugins/data/``, parsed with the same strict
rules as a user install. A broken builtin manifest is a packaging defect, so it
raises instead of degrading to a warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from zenagent.plugins.install import install_plugin
from zenagent.plugins.manifest import PluginManifest, parse_manifest

__all__ = ["CatalogEntry", "builtin_catalog", "catalog_dir", "install_builtin"]


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    name: str
    description: str
    requires_runtime: str | None


def catalog_dir(plugin_id: str | None = None) -> Path:
    """The on-disk directory holding the builtin plugin sources."""
    base = Path(str(resources.files("zenagent.plugins").joinpath("data")))
    return base / plugin_id if plugin_id is not None else base


def _resource(plugin_id: str):
    return resources.files("zenagent.plugins").joinpath("data", plugin_id)


def builtin_catalog() -> list[CatalogEntry]:
    base = catalog_dir()
    entries: list[CatalogEntry] = []
    if not base.is_dir():
        return entries
    for directory in sorted(path for path in base.iterdir() if path.is_dir()):
        try:
            manifest = parse_manifest(directory)
        except ValueError as exc:
            raise RuntimeError(
                f"builtin catalog corrupt: {directory.name}: {exc}"
            ) from exc
        entries.append(
            CatalogEntry(
                id=manifest.id,
                name=manifest.name,
                description=manifest.description,
                requires_runtime=manifest.requires_runtime,
            )
        )
    return entries


def install_builtin(plugin_id: str, *, root: Path | None = None) -> PluginManifest:
    if plugin_id not in {entry.id for entry in builtin_catalog()}:
        raise ValueError(f"unknown builtin plugin {plugin_id!r}")
    with resources.as_file(_resource(plugin_id)) as source:
        return install_plugin(source, root=root)
