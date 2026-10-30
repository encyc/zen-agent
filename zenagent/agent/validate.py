"""Minimal JSON Schema validation for tool arguments.

The loop validates arguments *before* ``before_tool_call`` so permission hooks
and tools only ever see well-formed input (pi-agent-core does the same with a
full typebox schema; the kernel keeps zero runtime dependencies, so this is a
hand-written subset).

Deliberately narrow — only what a hand-written tool schema actually needs:

* the value must be an object when the schema says ``type: "object"``;
* every name listed in ``required`` must be present;
* every present property whose schema declares a top-level ``type`` must match
  one of ``string``/``number``/``integer``/``boolean``/``object``/``array``.

No recursion into nested schemas, no ``anyOf``/``oneOf``, no ``format``, no
default filling, no extra-property rejection: tools defend their own internals.
``bool`` is rejected for ``integer``/``number`` (it is an ``int`` subclass in
Python, which would otherwise silently pass).
"""

from __future__ import annotations

from typing import Any

__all__ = ["validate_arguments"]

#: JSON Schema scalar/container names this validator understands.
_TYPE_CHECKS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def _describe(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return type(value).__name__


def validate_arguments(schema: dict, args: dict) -> str | None:
    """Check ``args`` against the hand-written ``schema``.

    Returns ``None`` when the arguments are acceptable, otherwise a
    human-readable error naming the offending argument. Unknown schema
    keywords and unknown types are ignored rather than rejected — a tool may
    use richer schemas than this validator understands.
    """
    if not isinstance(schema, dict):
        return None
    if schema.get("type") == "object" and not isinstance(args, dict):
        return f"expected an object, got {_describe(args)}"
    if not isinstance(args, dict):
        return None

    required = schema.get("required")
    if isinstance(required, list):
        for name in required:
            if isinstance(name, str) and name not in args:
                return f"missing required argument {name!r}"

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None

    for name, value in args.items():
        prop = properties.get(name)
        if not isinstance(prop, dict):
            continue  # undeclared argument: tool decides what to do with it
        expected = prop.get("type")
        if not isinstance(expected, str):
            continue  # no declared type or a union/nullable form: skip
        allowed = _TYPE_CHECKS.get(expected)
        if allowed is None:
            continue  # unknown type name: skip rather than reject
        if expected in ("integer", "number") and isinstance(value, bool):
            return f"argument {name!r} must be a {expected}, got bool"
        if not isinstance(value, allowed):
            return f"argument {name!r} must be a {expected}, got {_describe(value)}"
    return None
