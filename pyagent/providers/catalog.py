r"""Packaged model catalog: providers, models, pricing, and compat flags.

``catalog.yaml`` ships inside the package and is read via
``importlib.resources``, so wheel installs work without a source
checkout. The file uses a strict, anchor-free subset of YAML parsed by
the small purpose-built reader below — the kernel keeps ``httpx`` as its
only dependency, so PyYAML is deliberately out. The accepted grammar is
exactly: nested space-indented mappings, scalar strings/ints/floats/
bools, inline ``{key: value}`` flow maps for ``cost``, and ``#``
comments; anchors, aliases, tags, lists, tabs, quoted strings, and
null/nan/inf tokens are rejected with line-numbered errors.

Compat rule: catalog entries never sniff their base_url. A model's
explicit ``compat`` block — or its absence — is final; ``sniff_compat``
is only the fallback for endpoints not in the catalog.
"""

import re
from dataclasses import dataclass
from importlib import resources

from pyagent.providers.compat import CompatFlags
from pyagent.types import Cost

__all__ = [
    "Model",
    "ProviderConfig",
    "find_model",
    "load_catalog",
]

_PROVIDER_KEYS = frozenset({"name", "base_url", "api_key_env", "adapter", "models"})
_MODEL_KEYS = frozenset(
    {
        "adapter",
        "base_url",
        "context_window",
        "max_tokens",
        "reasoning",
        "cost",
        "compat",
        "default",
    }
)
_COMPAT_KEYS = frozenset(
    {
        "thinking_format",
        "max_tokens_field",
        "supports_developer_role",
        "supports_reasoning_effort",
        "zai_tool_stream",
        "requires_reasoning_content_on_assistant",
        "supports_usage_in_streaming",
    }
)
_COST_KEYS = frozenset({"input", "output"})
_INT_PATTERN = re.compile(r"-?\d+\Z")
#: Tokens that YAML/float() would accept but the catalog grammar forbids:
#: no nulls, no NaN/Infinity — a cost or string slot must never hold them.
_FORBIDDEN_SCALARS = frozenset({"null", "~"})
_NAN_INF = frozenset({"nan", "inf", "infinity"})


@dataclass
class ProviderConfig:
    """One provider entry from the catalog; ``models`` keeps file order.

    ``adapter`` is empty for multi-protocol gateways (e.g. opencode-go)
    where every model carries its own adapter.
    """

    id: str
    name: str
    base_url: str
    api_key_env: str
    adapter: str
    models: list[Model]


@dataclass
class Model:
    """One model entry; ``base_url``/``api`` fall back to the provider."""

    id: str
    provider: str
    api: str
    base_url: str
    context_window: int
    max_tokens: int
    reasoning: bool
    cost: Cost
    compat: CompatFlags
    default: bool = False


# --------------------------------------------------------------------------
# Minimal YAML-subset parser. Records are (indent, key, value-or-None, line).
# --------------------------------------------------------------------------


def _strip_comment(raw: str) -> str:
    """Drop a '#' comment: at line start or preceded by whitespace."""
    for i, ch in enumerate(raw):
        if ch == "#" and (i == 0 or raw[i - 1] in " \t"):
            return raw[:i]
    return raw


def _tokenize(text: str) -> list[tuple[int, str, str | None, int]]:
    records: list[tuple[int, str, str | None, int]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw:
            raise ValueError(f"catalog line {lineno}: tab indentation is not allowed")
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        if body.startswith("- "):
            raise ValueError(f"catalog line {lineno}: lists are not supported")
        key, sep, rest = body.partition(":")
        if not sep or not key:
            raise ValueError(f"catalog line {lineno}: expected 'key: value', got {body!r}")
        records.append((indent, key.strip(), rest.strip() or None, lineno))
    if not records:
        raise ValueError("catalog is empty")
    if records[0][0] != 0:
        raise ValueError(
            f"catalog line {records[0][3]}: top-level keys must not be indented"
        )
    return records


def _parse_scalar(token: str, lineno: int) -> bool | int | float | str:
    lowered = token.lower()
    if lowered in _FORBIDDEN_SCALARS or lowered.lstrip("+-") in _NAN_INF:
        raise ValueError(
            f"catalog line {lineno}: {token!r} is not a valid catalog scalar "
            f"(null/nan/inf are rejected)"
        )
    if token == "true":
        return True
    if token == "false":
        return False
    if _INT_PATTERN.fullmatch(token):
        return int(token)
    try:
        return float(token)
    except ValueError:
        return token


def _parse_flow_map(token: str, lineno: int) -> dict:
    if not (token.startswith("{") and token.endswith("}")):
        raise ValueError(f"catalog line {lineno}: malformed flow map {token!r}")
    inner = token[1:-1].strip()
    if not inner:
        return {}
    result: dict = {}
    for part in inner.split(","):
        key, sep, value = part.partition(":")
        key, value = key.strip(), value.strip()
        if not sep or not key or not value:
            raise ValueError(
                f"catalog line {lineno}: malformed flow map entry {part.strip()!r}"
            )
        if key in result:
            raise ValueError(f"catalog line {lineno}: duplicate key {key!r} in flow map")
        result[key] = _parse_scalar(value, lineno)
    return result


def _parse_value(token: str, lineno: int):
    if token[0] in "&*!|>[\"'":
        raise ValueError(
            f"catalog line {lineno}: anchors, aliases, tags, block scalars, "
            f"quoted strings, and flow sequences are not supported ({token!r})"
        )
    if token.startswith("{"):
        return _parse_flow_map(token, lineno)
    return _parse_scalar(token, lineno)


def _parse_block(
    records: list[tuple[int, str, str | None, int]], start: int, indent: int
) -> tuple[dict, int]:
    """Parse sibling ``key: …`` lines at ``indent``; return (mapping, next index)."""
    result: dict = {}
    i = start
    while i < len(records):
        rec_indent, key, value, lineno = records[i]
        if rec_indent < indent:
            break
        if rec_indent > indent:
            raise ValueError(f"catalog line {lineno}: unexpected indentation")
        if key in result:
            raise ValueError(f"catalog line {lineno}: duplicate key {key!r}")
        if value is None:
            if i + 1 >= len(records) or records[i + 1][0] <= indent:
                raise ValueError(
                    f"catalog line {lineno}: {key!r} must contain a nested mapping"
                )
            result[key], i = _parse_block(records, i + 1, records[i + 1][0])
            continue
        result[key] = _parse_value(value, lineno)
        i += 1
    return result, i


# --------------------------------------------------------------------------
# Mapping the parsed document onto ProviderConfig/Model.
# --------------------------------------------------------------------------


def _reject_unknown_keys(raw: dict, allowed: frozenset[str], ctx: str) -> None:
    for key in raw:
        if key not in allowed:
            raise ValueError(f"{ctx} has unknown key {key!r}")


def _expect_str(value, ctx: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{ctx} must be a non-empty string, got {value!r}")
    return value


# Note: all malformed-catalog errors deliberately raise ValueError (not
# TypeError) — one error contract for the whole loader, mirroring types.py.

def _expect_int(value, ctx: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{ctx} must be an int, got {value!r}")  # noqa: TRY004 - contract
    return value


def _expect_bool(value, ctx: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{ctx} must be a bool, got {value!r}")  # noqa: TRY004 - contract
    return value


def _expect_float(value, ctx: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{ctx} must be a number, got {value!r}")  # noqa: TRY004 - contract
    return float(value)


def _compat_from(raw: object, ctx: str) -> CompatFlags:
    """Build CompatFlags from an explicit (possibly empty) compat block.

    Missing keys fall through to the dataclass defaults — the block, or
    its absence, is final; the base_url is never sniffed.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"{ctx} compat must be a mapping, got {raw!r}")  # noqa: TRY004
    _reject_unknown_keys(raw, _COMPAT_KEYS, f"{ctx} compat")
    for key in _COMPAT_KEYS & raw.keys():
        value = raw[key]
        if key == "thinking_format":
            _expect_str(value, f"{ctx} compat.thinking_format")
        elif key == "max_tokens_field":
            _expect_str(value, f"{ctx} compat.max_tokens_field")
        else:
            _expect_bool(value, f"{ctx} compat.{key}")
    return CompatFlags(**raw)


def _cost_from(raw: object, ctx: str) -> Cost:
    """Unit prices per 1M tokens; ``total`` stays 0 until usage is computed."""
    if not isinstance(raw, dict):
        raise ValueError(f"{ctx} cost must be a flow map, got {raw!r}")  # noqa: TRY004
    _reject_unknown_keys(raw, _COST_KEYS, f"{ctx} cost")
    return Cost(
        input=_expect_float(raw.get("input", 0.0), f"{ctx} cost.input"),
        output=_expect_float(raw.get("output", 0.0), f"{ctx} cost.output"),
    )


def _model_from(
    provider_id: str,
    provider_base_url: str,
    provider_adapter: str,
    model_id: str,
    raw: object,
) -> Model:
    ctx = f"model {provider_id}/{model_id}"
    if not isinstance(raw, dict):
        raise ValueError(f"{ctx} must be a mapping, got {raw!r}")  # noqa: TRY004 - contract
    _reject_unknown_keys(raw, _MODEL_KEYS, ctx)
    for required in ("context_window", "max_tokens", "reasoning"):
        if required not in raw:
            raise ValueError(f"{ctx} is missing required key {required!r}")
    api = raw.get("adapter", provider_adapter)
    if not api:
        raise ValueError(f"{ctx} has no adapter (neither its own nor the provider's)")
    return Model(
        id=model_id,
        provider=provider_id,
        api=_expect_str(api, f"{ctx} adapter"),
        base_url=_expect_str(raw.get("base_url", provider_base_url), f"{ctx} base_url"),
        context_window=_expect_int(raw["context_window"], f"{ctx} context_window"),
        max_tokens=_expect_int(raw["max_tokens"], f"{ctx} max_tokens"),
        reasoning=_expect_bool(raw["reasoning"], f"{ctx} reasoning"),
        cost=_cost_from(raw.get("cost", {}), ctx),
        compat=_compat_from(raw.get("compat", {}), ctx),
        default=_expect_bool(raw.get("default", False), f"{ctx} default"),
    )


def _provider_from(provider_id: str, raw: object) -> ProviderConfig:
    ctx = f"provider {provider_id!r}"
    if not isinstance(raw, dict):
        raise ValueError(f"{ctx} must be a mapping, got {raw!r}")  # noqa: TRY004 - contract
    _reject_unknown_keys(raw, _PROVIDER_KEYS, ctx)
    for required in ("name", "base_url", "api_key_env"):
        if required not in raw:
            raise ValueError(f"{ctx} is missing required key {required!r}")
    models_raw = raw.get("models")
    if not isinstance(models_raw, dict) or not models_raw:
        raise ValueError(f"{ctx} must define at least one model")
    base_url = _expect_str(raw["base_url"], f"{ctx} base_url")
    adapter = raw.get("adapter", "")  # empty: multi-protocol gateway, per-model adapters
    if adapter:
        _expect_str(adapter, f"{ctx} adapter")
    models = [
        _model_from(provider_id, base_url, adapter, model_id, model_raw)
        for model_id, model_raw in models_raw.items()
    ]
    return ProviderConfig(
        id=provider_id,
        name=_expect_str(raw["name"], f"{ctx} name"),
        base_url=base_url,
        api_key_env=_expect_str(raw["api_key_env"], f"{ctx} api_key_env"),
        adapter=adapter,
        models=models,
    )


def _build_catalog(text: str) -> dict[str, ProviderConfig]:
    """Parse catalog text and build ProviderConfig objects (test entry point)."""
    records = _tokenize(text)
    document, _ = _parse_block(records, 0, 0)
    _reject_unknown_keys(document, frozenset({"providers"}), "catalog")
    providers_raw = document.get("providers")
    if not isinstance(providers_raw, dict):
        raise ValueError("catalog must define a 'providers' mapping")  # noqa: TRY004
    return {pid: _provider_from(pid, raw) for pid, raw in providers_raw.items()}


def load_catalog() -> dict[str, ProviderConfig]:
    """Load the packaged ``pyagent/catalog.yaml`` keyed by provider id."""
    text = resources.files("pyagent").joinpath("catalog.yaml").read_text(encoding="utf-8")
    return _build_catalog(text)


def find_model(catalog: dict[str, ProviderConfig], model_id: str) -> Model:
    """Find a model by id across all providers, in file order.

    The first provider defining a duplicated id wins. Raises KeyError
    naming the model id when no provider defines it.
    """
    for provider in catalog.values():
        for model in provider.models:
            if model.id == model_id:
                return model
    raise KeyError(f"model {model_id!r} is not in the catalog")
