"""The wire registry's frontier and integrity: server.py's dispatch surface
must equal OPS ∪ PARTIAL exactly, and the declared models must keep the
handler behaviors they replaced (tests/test_rpc_v2.py covers the live wire;
this file pins the registry's own rules)."""

import re
from pathlib import Path

from zenagent.cli.rpc import protocol

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "zenagent" / "cli" / "rpc" / "server.py"


def _dispatch_kinds() -> set[str]:
    """Every kind literal the server.py dispatch compares against."""
    source = SERVER.read_text(encoding="utf-8")
    kinds = set(re.findall(r'kind == "([a-z_.]+)"', source))
    for group in re.findall(r"kind in \(([^)]+)\)", source):
        kinds |= set(re.findall(r'"([a-z_.]+)"', group))
    return kinds


def test_registry_is_structurally_sound():
    assert protocol.validate_registry() == []


def test_dispatch_surface_is_frozen():
    """Every dispatched kind is declared (OPS) or stubbed with a reason
    (PARTIAL); nothing is declared that the dispatch does not handle."""
    dispatched = _dispatch_kinds()
    declared = set(protocol.OPS) | set(protocol.PARTIAL)
    assert dispatched - declared == set(), "new kinds must register in protocol.py"
    assert declared - dispatched == set(), "dead registry entries must go"


def test_partial_stubs_carry_reasons():
    assert all(reason.strip() for reason in protocol.PARTIAL.values())


def test_ops_never_shrink_silently():
    """A regression guard in the other direction: kinds already fully
    declared must not quietly demote to stubs."""
    demoted = set(protocol.PARTIAL) & set(protocol.OPS)
    assert demoted == set()


def test_git_log_accepts_the_coercion_surface():
    req = protocol.GitLogRequest.from_payload({"limit": "5", "skip": 2})
    assert (req.limit, req.skip) == ("5", 2)
    req = protocol.GitLogRequest.from_payload({})
    assert (req.limit, req.skip) == (None, None)


def test_git_log_refuses_bool_and_structures():
    for junk in ({"limit": True}, {"limit": [5]}, {"skip": {"n": 1}}):
        try:
            protocol.GitLogRequest.from_payload(junk)
        except protocol.WireError:
            pass
        else:  # pragma: no cover - the failure message names the junk
            raise AssertionError(f"git_log accepted {junk}")


def test_git_diff_staged_is_lenient_like_the_handler():
    req = protocol.GitDiffRequest.from_payload({"path": "a.py", "staged": 5})
    assert req.staged is not True  # the handler's `is True` semantics
    req = protocol.GitDiffRequest.from_payload({"path": "a.py", "staged": True})
    assert req.staged is True


def test_required_fields_refuse_absence_and_junk():
    for junk in ({}, {"commit": 5}, {"commit": None}):
        try:
            protocol.GitShowRequest.from_payload(junk)
        except protocol.WireError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"git_show accepted {junk}")


def test_fs_path_absent_and_null_differ():
    """`.get("path", "")` semantics: absent passes as None (the handler's
    `or ""`), JSON null passes as None too — the handler distinguishes by
    key presence, so the model must preserve both as None. Junk typing is
    refused by the model; the handler maps it to the canonical message."""
    absent = protocol.FsPathRequest.from_payload({})
    null = protocol.FsPathRequest.from_payload({"path": None})
    assert absent.path is None and null.path is None
    for junk in ({"path": ["a"]}, {"path": 5}):
        try:
            protocol.FsPathRequest.from_payload(junk)
        except protocol.WireError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"fs path accepted {junk}")


def test_memory_sub_ops_declare_their_field_sets():
    memory = protocol.OPS["memory"]
    assert set(memory.subs or {}) == {"list", "create", "update", "delete"}
    update = (memory.subs or {})["update"]
    for field in ("entry_id", "title", "body"):
        assert update.request.FIELDS[field].required


def test_shapes_resolve():
    for name, entry in protocol.SHAPES.items():
        assert entry["fields"], f"shape {name} declares no fields"
        for field in entry["fields"].values():
            ty = field.ty
            if isinstance(ty, protocol.WireType) and ty.kind == "ref":
                assert ty.arg in protocol.SHAPES, f"{name}: dangling ref {ty.arg}"


def test_events_declare_the_serializer():
    """Every event type literal is a real AgentEvent.type and the flat field
    table covers what codec.serialize_event can emit (its body only — the
    nested `_message_projection` keys are a different payload)."""
    from zenagent.events import AgentEvent

    literal = AgentEvent.__dataclass_fields__["type"].type
    for name in protocol.EVENT_TYPES:
        assert f"'{name}'" in literal or f'"{name}"' in literal
    codec = (REPO / "zenagent" / "cli" / "rpc" / "codec.py").read_text(encoding="utf-8")
    body = codec.split("def serialize_event")[1].split("\ndef ")[0]
    for key in re.findall(r'payload\["([a-z_]+)"\]', body):
        assert key in protocol.EVENT_FIELDS, (
            f"serialize_event emits {key}; declare it in EVENT_FIELDS"
        )
