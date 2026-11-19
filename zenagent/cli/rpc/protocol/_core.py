"""Wire registry vocabulary and machinery: field types, request models.

The declarations live in `_chat.py` (chat surface) and `_host.py` (host
surface); `__init__.py` assembles them and owns the integrity check. The
split exists because the single module is 90% declarations and outgrew the
LOC budget's coupling cap; every consumer import
(`from zenagent.cli.rpc import protocol`) keeps working unchanged.
"""

from __future__ import annotations

from typing import Any, ClassVar, NamedTuple

#: Scalars. INT rejects bool (JSON true is not a number); FLOAT accepts both.
#: BOOL_LAX is the declared-as-boolean, server-tolerates-junk case (today's
#: ``value is True`` guards): the generator emits ``boolean`` with a note.
STRING = "string"
INT = "int"
FLOAT = "float"
BOOL = "bool"
BOOL_LAX = "bool_lax"
#: Declared-as-string, server-tolerates-junk (``str(value)``/``==`` guards).
STRING_LAX = "string_lax"
#: int | float | str — the handler's ``int(x)`` coercion surface; bool refused.
NUMBER_LAX = "number_lax"
ANY = "any"


class WireType(NamedTuple):
    """A composite field type: list/record/ref/enum with its item/value/name."""

    kind: str
    arg: object


#: WireType discriminators. Named constants rather than inline literals: the
#: two-sided contract test greps this package for dispatch comparisons
#: (``kind`` against a quoted word) to pin the surface; these are field-type
#: tags, not kinds, so they are named constants rather than literals.
KIND_LIST = "list"
KIND_RECORD = "record"
KIND_REF = "ref"
KIND_ENUM = "enum"
KIND_REFS = "refs"


def list_of(item: object) -> WireType:
    return WireType(KIND_LIST, item)


def record(value: object) -> WireType:
    """A JSON object with unknown keys, all of one value type."""
    return WireType(KIND_RECORD, value)


def one_of(*values: str) -> WireType:
    """A string restricted to a closed set; TS renders the literal union."""
    return WireType(KIND_ENUM, values)


def refs(*names: str) -> WireType:
    """A union of named shapes (a transcript row, say); TS renders ``A | B``."""
    return WireType(KIND_REFS, names)


def ref(name: str) -> WireType:
    """A named shape from :data:`SHAPES` (a declared interface, not inline)."""
    return WireType(KIND_REF, name)


class Field(NamedTuple):
    """One wire field: its type, and how presence works.

    Request side: ``required`` fields must be present and type-correct; the
    rest may be absent or JSON-null (handlers read both as ``None`` today, so
    the model does too). Result side: ``optional`` marks keys that
    conditionally appear in the payload (absent, never null); ``nullable``
    marks keys that are always present but carry JSON null for the empty
    case — the two are different wire shapes and generate different TS.
    """

    ty: object
    required: bool = False
    optional: bool = False
    nullable: bool = False


class WireError(ValueError):
    """A request failed shape validation; the handler owns the wire message.

    ``field`` names the offending key so a handler can render its canonical
    refusal text (``f"{op} needs {exc.field}"``) instead of the model's
    generic one.
    """

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class WireRequest:
    """Declarative request model: ``from_payload`` validates, handlers use it.

    Subclass and declare ``FIELDS``; the parsed values arrive as attributes.
    """

    FIELDS: ClassVar[dict[str, Field]] = {}

    def __init__(self, values: dict[str, Any]) -> None:
        for name, value in values.items():
            setattr(self, name, value)

    def __getattr__(self, name: str) -> Any:
        """Parsed field access (``req.worktree``): values are set in
        ``__init__`` from ``FIELDS``, so static analysis sees this hook.
        Undeclared names still raise AttributeError at runtime."""
        raise AttributeError(name)

    @classmethod
    def from_payload(cls, payload: dict) -> WireRequest:
        values: dict[str, Any] = {}
        for name, field in cls.FIELDS.items():
            value = payload.get(name)
            if value is None:
                if field.required:
                    raise WireError(f"{name} is required", field=name)
                values[name] = None
                continue
            values[name] = _check(name, value, field.ty)
        return cls(values)


class OpSpec(NamedTuple):
    """One wire op: request model plus the result payload's declared keys."""

    kind: str
    request: type[WireRequest]
    result: dict[str, Field]
    #: memory/session-style sub-operations: `op` value → its own spec. When
    #: present, the base spec's result is ignored and each sub carries its own.
    subs: dict[str, OpSpec] | None = None
    #: True when the handler sends ``ok: false`` as a normal outcome (model
    #: refusals, failed prompts); the generator then emits ``ok: boolean``
    #: instead of the literal-true envelope.
    ok_mutable: bool = False


def shape(name: str, fields: dict[str, Field], *, ts: str | None = None, doc: str = "") -> tuple[str, dict]:
    return name, {"ts": ts or name, "doc": doc, "fields": fields}


class NoFields(WireRequest):
    """An op whose request carries no fields beyond the envelope."""

    FIELDS: ClassVar[dict[str, Field]] = {}


def _check(name: str, value: Any, ty: object) -> Any:
    if isinstance(ty, WireType):
        if ty.kind == KIND_LIST:
            if not isinstance(value, list):
                raise WireError(f"{name} must be a list", field=name)
            return [_check(f"{name}[]", item, ty.arg) for item in value]
        if ty.kind == KIND_RECORD:
            if not isinstance(value, dict):
                raise WireError(f"{name} must be an object", field=name)
            return {
                key: _check(f"{name}.{key}", item, ty.arg)
                for key, item in value.items()
            }
        if ty.kind == KIND_REF:
            if not isinstance(value, dict):
                raise WireError(f"{name} must be an object", field=name)
            return value
        if ty.kind == KIND_ENUM:
            allowed = ty.arg if isinstance(ty.arg, tuple) else ()
            if not isinstance(value, str) or value not in allowed:
                raise WireError(f"{name} must be one of {', '.join(allowed)}", field=name)
            return value
        if ty.kind == KIND_REFS:
            if not isinstance(value, dict):
                raise WireError(f"{name} must be an object")
            return value
        raise WireError(f"{name}: unknown field type {ty.kind}")  # pragma: no cover
    if ty == BOOL_LAX:
        return value  # handler semantics (`is True`); junk tolerated, as today
    if ty == STRING_LAX:
        return value  # handler semantics (`or default` / `==`); junk tolerated
    if ty == NUMBER_LAX:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise WireError(f"{name} must be a number", field=name)
        return value
    if ty == ANY:
        return value
    if not isinstance(ty, str):
        raise WireError(f"{name}: unknown field type {ty!r}", field=name)  # pragma: no cover
    _scalars: dict[str, type | tuple[type, ...]] = {
        STRING: str,
        INT: int,
        FLOAT: (int, float),
        BOOL: bool,
    }
    scalar = _scalars.get(ty)
    if scalar is None:
        raise WireError(f"{name}: unknown field type {ty!r}", field=name)  # pragma: no cover
    if ty in (INT, FLOAT) and isinstance(value, bool):
        raise WireError(f"{name} must be a number", field=name)
    if not isinstance(value, scalar):
        raise WireError(f"{name} has the wrong type", field=name)
    return value


