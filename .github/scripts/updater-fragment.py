#!/usr/bin/env python3
"""Write one updater fragment for this release leg.

Each matrix leg builds its own architecture, so each leg knows exactly one
platform key for `latest.json`. Letting every leg write `latest.json` itself
(tauri-action's `includeUpdaterJson`) races: the last writer can drop the other
platforms, and a user on a dropped platform never sees an update again. Instead
each leg emits a small fragment, and one job merges them.

    python3 .github/scripts/updater-fragment.py \
        --target aarch64-apple-darwin --tag v0.9.0 \
        --bundle-dir desktop/src-tauri/target/aarch64-apple-darwin/release/bundle \
        --repo encyc/zen-agent --out fragment.json

Writes nothing and exits 3 when the leg produced no updater archive (an unsigned
build), so the workflow can skip the release body note without failing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["asset_url", "main", "pick_artifact", "platform_key", "write_fragment"]

#: What the updater actually downloads, per platform, best match first.
#: Tauri v2 points macOS at the `.app.tar.gz` and Linux/Windows at the
#: `.AppImage` / NSIS `-setup.exe` themselves, each with a sibling `.sig`.
UPDATER_ARTIFACTS = (".app.tar.gz", ".AppImage", "-setup.exe")
#: Matched against the target triple.
ARCH_KEYS = {
    ("aarch64", "apple-darwin"): "darwin-aarch64",
    ("x86_64", "apple-darwin"): "darwin-x86_64",
    ("aarch64", "linux"): "linux-aarch64",
    ("x86_64", "linux"): "linux-x86_64",
    ("aarch64", "windows"): "windows-aarch64",
    ("x86_64", "windows"): "windows-x86_64",
}


def platform_key(triple: str) -> str:
    """The `latest.json` platform key a target triple feeds."""
    arch, _, rest = triple.partition("-")
    for (wanted_arch, wanted_os), key in ARCH_KEYS.items():
        if arch == wanted_arch and wanted_os in rest:
            return key
    raise SystemExit(f"updater-fragment: unknown target triple {triple!r}")


def pick_artifact(names: list[str]) -> str | None:
    """The file the updater downloads, from the bundle's file names."""
    for suffix in UPDATER_ARTIFACTS:
        for name in names:
            if name.endswith(suffix):
                return name
    return None


def find_artifact(bundle_dir: Path) -> Path | None:
    """The same choice, as a path, so the `.sig` is read from beside it."""
    for suffix in UPDATER_ARTIFACTS:
        for path in sorted(bundle_dir.rglob("*")):
            if path.is_file() and path.name.endswith(suffix):
                return path
    return None


def asset_url(repo: str, tag: str, name: str) -> str:
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


def uploaded_name(name: str, triple: str) -> str:
    """The release-asset name tauri-action uploads, for the bundle file name.

    tauri-action renames macOS updater archives on upload to keep two arch
    legs from colliding on one release: ``ZenAgent.app.tar.gz`` lands as
    ``ZenAgent_aarch64.app.tar.gz``. Every other platform keeps its local
    name. The fragment must point at the asset name, not the local one, or
    macOS users download a 404 (v0.9.0 shipped exactly that bug).
    """
    if name.endswith(".app.tar.gz"):
        arch, _, _rest = triple.partition("-")
        return name.replace(".app.tar.gz", f"_{arch}.app.tar.gz")
    return name


def write_fragment(
    *, target: str, tag: str, bundle_dir: Path, repo: str, out: Path
) -> bool:
    """Write the fragment; False when this leg has no signed updater archive."""
    artifact = find_artifact(bundle_dir)
    if artifact is None:
        return False
    signature = artifact.with_name(f"{artifact.name}.sig")
    if not signature.is_file():
        return False
    payload = {
        "platform": platform_key(target),
        "signature": signature.read_text(encoding="utf-8").strip(),
        "url": asset_url(repo, tag, uploaded_name(artifact.name, target)),
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if not args.bundle_dir.is_dir():
        print(f"updater-fragment: no bundle dir {args.bundle_dir}", file=sys.stderr)
        return 3
    if not write_fragment(
        target=args.target,
        tag=args.tag,
        bundle_dir=args.bundle_dir,
        repo=args.repo,
        out=args.out,
    ):
        print("updater-fragment: no signed updater archive in this bundle", file=sys.stderr)
        return 3
    print(f"  ok    {args.out} ({platform_key(args.target)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
