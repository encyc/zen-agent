"""Minimal tool-argument validator tests (Task 9A)."""

from pyagent.agent.validate import validate_arguments

SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "count": {"type": "integer"},
        "ratio": {"type": "number"},
        "flag": {"type": "boolean"},
        "items": {"type": "array"},
        "meta": {"type": "object"},
    },
    "required": ["path", "count"],
}


def test_accepts_well_typed_arguments():
    args = {
        "path": "a.txt",
        "count": 2,
        "ratio": 1.5,
        "flag": True,
        "items": [1],
        "meta": {"k": "v"},
    }
    assert validate_arguments(SCHEMA, args) is None


def test_missing_required_argument_is_named():
    problem = validate_arguments(SCHEMA, {"path": "a.txt"})
    assert problem is not None and "count" in problem and "required" in problem


def test_wrong_top_level_type_is_named():
    problem = validate_arguments(SCHEMA, {"path": 7, "count": 1})
    assert problem is not None
    assert "'path'" in problem and "string" in problem and "int" in problem


def test_int_satisfies_number_but_not_vice_versa():
    assert validate_arguments(SCHEMA, {"path": "a", "count": 1, "ratio": 3}) is None
    problem = validate_arguments(SCHEMA, {"path": "a", "count": 1.5})
    assert problem is not None and "'count'" in problem and "integer" in problem


def test_bool_is_rejected_for_numeric_types():
    assert validate_arguments(SCHEMA, {"path": "a", "count": True}) is not None
    assert validate_arguments(SCHEMA, {"path": "a", "count": 1, "ratio": False}) is not None


def test_undeclared_argument_is_allowed():
    assert validate_arguments(SCHEMA, {"path": "a", "count": 1, "extra": object()}) is None


def test_unknown_declared_type_is_ignored():
    schema = {"type": "object", "properties": {"x": {"type": "colour"}}}
    assert validate_arguments(schema, {"x": "anything"}) is None


def test_property_without_type_is_ignored():
    schema = {"type": "object", "properties": {"x": {"description": "no type"}}}
    assert validate_arguments(schema, {"x": object()}) is None


def test_missing_properties_and_required_are_fine():
    assert validate_arguments({"type": "object"}, {"anything": 1}) is None


def test_non_object_args_rejected_when_schema_says_object():
    problem = validate_arguments(SCHEMA, ["not", "a", "dict"])
    assert problem is not None and "expected an object" in problem


def test_non_object_schema_is_not_enforced():
    assert validate_arguments({"type": "string"}, "whatever") is None
    assert validate_arguments(None, {"a": 1}) is None
