"""Role manifest edits must survive a crash and never silently drop fields.

write_role merges into the on-disk manifest ("skills, plugins, MCP, MCP,
inheritProject must survive an edit" — its own comment), so a corrupt file and
a non-atomic write both destroy exactly what the merge promises to keep.
"""

import json

import pytest


def _role_dir(tmp_path, role_id="writer"):
    role_dir = tmp_path / ".zenagent" / "agents" / role_id
    role_dir.mkdir(parents=True)
    return role_dir


def test_write_survives_a_failed_replace(tmp_path, monkeypatch):
    """A crash between tmp-write and rename leaves the old manifest intact."""
    from zenagent.app.swarm import roster as roster_mod

    role_dir = _role_dir(tmp_path)
    manifest_path = role_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps({"id": "writer", "name": "Writer", "mcp": {"grep": {}}}),
        encoding="utf-8",
    )

    def exploding_replace(src, dst):
        raise OSError("power cut mid-rename")

    # atomic_write_text lives in app.support; that os.replace call is the seam.
    import zenagent.app.support as support_mod

    monkeypatch.setattr(support_mod.os, "replace", exploding_replace)
    with pytest.raises(OSError):
        roster_mod.write_role("writer", {"name": "New"}, root=tmp_path)
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["mcp"] == {"grep": {}}


def test_corrupt_manifest_refuses_merge(tmp_path):
    """A half-written manifest must not become an empty merge base."""
    from zenagent.app.swarm import roster as roster_mod

    role_dir = _role_dir(tmp_path)
    (role_dir / "manifest.json").write_text('{"id": "wri', encoding="utf-8")
    with pytest.raises(ValueError):
        roster_mod.write_role("writer", {"name": "New"}, root=tmp_path)
