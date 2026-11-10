"""Strict parsing of ``<plugin>/manifest.json``.

Install time is the strict gate: a broken manifest is rejected immediately,
with a message naming the field. Discovery re-checks defensively and skips
what a hand edit broke (failure isolation, same policy as extensions).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CredentialSpec", "PluginManifest", "parse_manifest"]

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")
_MAX_ID = 64


@dataclass(frozen=True)
class CredentialSpec:
    """One credential the user must supply before the plugin can connect."""

    key: str
    label: str
    secret: bool = False
    help: str = ""
    url: str = ""


@dataclass(frozen=True)
class PluginManifest:
    """A validated plugin; ``mcp_servers`` still holds unresolved ${KEY}s."""

    schema_version: int
    id: str
    name: str
    version: str
    description: str
    mcp_servers: dict[str, dict]
    skills: str | None
    extension: str | None
    credentials: tuple[CredentialSpec, ...]
    setup_guide: str | None
    requires_runtime: str | None
    root: Path


def parse_manifest(root: Path) -> PluginManifest:
    """Parse and validate ``root/manifest.json``; raises ``ValueError``."""
    root = Path(root)
    target = root / "manifest.json"
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{target.name} is missing") from exc
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read manifest.json: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(  # noqa: TRY004 - manifest errors are ValueError by contract
            "manifest.json must contain an object"
        )

    if raw.get("schemaVersion") != 1:
        raise ValueError("schemaVersion must be 1")
    plugin_id = _text(raw, "id")
    if not plugin_id or not _ID_RE.fullmatch(plugin_id) or len(plugin_id) > _MAX_ID:
        raise ValueError(f"invalid plugin id {plugin_id!r}")
    name = _text(raw, "name")
    version = _text(raw, "version")
    description = _text(raw, "description")
    for label, value in (("name", name), ("version", version), ("description", description)):
        if not value:
            raise ValueError(f"manifest {label} must be a non-empty string")

    contributes = raw.get("contributes")
    if not isinstance(contributes, dict):
        raise ValueError(  # noqa: TRY004 - manifest errors are ValueError by contract
            "manifest needs a 'contributes' object"
        )
    servers = contributes.get("mcpServers") or {}
    if not isinstance(servers, dict) or not all(
        isinstance(entry, dict) for entry in servers.values()
    ):
        raise ValueError("contributes.mcpServers must map names to objects")

    skills = _relative(contributes.get("skills"), root, "contributes.skills")
    extension = _relative(contributes.get("extension"), root, "contributes.extension")
    if extension is not None and not (root / extension).is_file():
        raise ValueError(f"contributes.extension {extension!r} does not exist")

    setup = raw.get("setup") or {}
    if not isinstance(setup, dict):
        raise ValueError(  # noqa: TRY004 - manifest errors are ValueError by contract
            "setup must be an object"
        )
    guide = _relative(setup.get("guide"), root, "setup.guide")
    requires_runtime = _optional_text(setup.get("requiresRuntime"), "setup.requiresRuntime")

    credentials = _credentials(raw.get("credentials") or [])
    declared = {spec.key for spec in credentials}
    for placeholder in _placeholders(servers):
        if placeholder not in declared:
            raise ValueError(
                f"undeclared placeholder ${{{placeholder}}}: add it to credentials"
            )

    return PluginManifest(
        schema_version=1,
        id=plugin_id,
        name=name,
        version=version,
        description=description,
        mcp_servers=dict(servers),
        skills=skills,
        extension=extension,
        credentials=credentials,
        setup_guide=guide,
        requires_runtime=requires_runtime,
        root=root,
    )


def _text(raw: dict, key: str) -> str:
    value = raw.get(key)
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(  # noqa: TRY004 - manifest errors are ValueError by contract
            f"{label} must be a string"
        )
    return value.strip() or None


def _relative(value: object, root: Path, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a relative path")
    candidate = value.strip()
    parts = Path(candidate).parts
    if Path(candidate).is_absolute() or ".." in parts:
        raise ValueError(f"{label} must stay inside the plugin directory")
    try:
        inside = (root / candidate).resolve().is_relative_to(root.resolve())
    except OSError:
        inside = False
    if not inside:
        raise ValueError(f"{label} must stay inside the plugin directory")
    return candidate


def _credentials(raw: object) -> tuple[CredentialSpec, ...]:
    if not isinstance(raw, list):
        raise ValueError(  # noqa: TRY004 - manifest errors are ValueError by contract
            "credentials must be a list"
        )
    specs: list[CredentialSpec] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError(  # noqa: TRY004 - manifest errors are ValueError by contract
                "each credential must be an object"
            )
        key = entry.get("key")
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise ValueError(f"invalid credential key {key!r}")
        if key in seen:
            raise ValueError(f"duplicate credential key {key}")
        seen.add(key)
        label = entry.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"credential {key} needs a label")
        secret = entry.get("secret", False)
        if not isinstance(secret, bool):
            raise ValueError(  # noqa: TRY004 - manifest errors are ValueError by contract
                f"credential {key} secret must be a boolean"
            )
        for field in ("help", "url"):
            value = entry.get(field, "")
            if not isinstance(value, str):
                raise ValueError(  # noqa: TRY004 - manifest errors are ValueError by contract
                    f"credential {key} {field} must be a string"
                )
        specs.append(
            CredentialSpec(
                key=key,
                label=label.strip(),
                secret=secret,
                help=str(entry.get("help", "")),
                url=str(entry.get("url", "")),
            )
        )
    return tuple(specs)


def _placeholders(value: object):
    """Every ``${KEY}`` used in the server contribution (recursive)."""
    if isinstance(value, str):
        yield from _PLACEHOLDER_RE.findall(value)
    elif isinstance(value, list):
        for item in value:
            yield from _placeholders(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _placeholders(item)
