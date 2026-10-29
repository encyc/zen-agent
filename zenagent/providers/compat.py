"""Provider compatibility flags and the base_url sniffing fallback.

``CompatFlags`` records the per-dialect request/response quirks that
adapters must honor (which field carries the output-token limit, whether
``developer`` role messages are accepted, how thinking is formatted, …).
Defaults describe a plain OpenAI-compatible API.

``sniff_compat`` is ONLY the fallback for endpoints that are not in the
packaged catalog: catalog entries always set compat explicitly (or omit
the block entirely and get these same dataclass defaults) and are never
sniffed.
"""

from dataclasses import dataclass

#: User-level thinking settings that mean "reasoning on" (``"off"`` means off).
THINKING_LEVELS = frozenset({"low", "medium", "high", "max"})


def normalize_thinking(thinking: str | None) -> str | None:
    """Map a user-level thinking setting onto an enabled level or ``None``.

    ``None`` and ``"off"`` both mean "no thinking" and must produce identical
    request bodies; any other string is a programming error, not something to
    ship upstream, so it raises ``ValueError``.
    """
    if thinking is None or thinking == "off":
        return None
    if thinking not in THINKING_LEVELS:
        raise ValueError(
            f"invalid thinking level {thinking!r}; expected one of "
            "None, 'off', 'low', 'medium', 'high', 'max'"
        )
    return thinking

__all__ = ["THINKING_LEVELS", "CompatFlags", "normalize_thinking", "sniff_compat"]


@dataclass
class CompatFlags:
    """Per-model dialect quirks, with plain-OpenAI defaults."""

    #: Thinking output dialect (``"zai"``, ``"deepseek"``) or None.
    thinking_format: str | None = None
    #: Request field that caps output tokens ("max_tokens" or "max_output_tokens").
    max_tokens_field: str = "max_tokens"
    #: Whether the API accepts the OpenAI ``developer`` role.
    supports_developer_role: bool = True
    #: Whether the API accepts the ``reasoning_effort`` parameter.
    supports_reasoning_effort: bool = False
    #: Z.AI streaming tool-call frames need special handling.
    zai_tool_stream: bool = False
    #: DeepSeek reasoner: assistant turns must echo ``reasoning_content`` back.
    requires_reasoning_content_on_assistant: bool = False
    #: Whether streamed responses include usage (OpenAI ``stream_options``).
    supports_usage_in_streaming: bool = True
    #: Anthropic: use the newer ``thinking: {type: adaptive}`` + ``output_config.effort``
    #: shape instead of ``thinking: {type: enabled, budget_tokens}``. Some gateway
    #: models reject the classic shape ("thinking.type.enabled is not supported").
    anthropic_adaptive_thinking: bool = False


def sniff_compat(base_url: str) -> CompatFlags:
    """Guess dialect defaults from a base_url unknown to the catalog.

    Known hosts get their dialect's compat defaults; every other endpoint
    gets the all-default flags. This never applies to catalog entries —
    their explicit compat (or its absence) is final.
    """
    lowered = base_url.lower()
    if "bigmodel.cn" in lowered:
        # Aligned with the catalog's zai compat blocks (see zai-coding-cn in
        # catalog.yaml) — one source of truth for the zai dialect.
        return CompatFlags(
            thinking_format="zai",
            zai_tool_stream=True,
            max_tokens_field="max_tokens",
            supports_developer_role=False,
            supports_reasoning_effort=True,
        )
    if "deepseek.com" in lowered:
        return CompatFlags(
            thinking_format="deepseek",
            requires_reasoning_content_on_assistant=True,
            max_tokens_field="max_tokens",
            supports_developer_role=False,
        )
    return CompatFlags()
