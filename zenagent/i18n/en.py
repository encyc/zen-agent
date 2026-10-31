"""English catalog — the authoritative text for every user-facing string.

Keep the values byte-identical to the literals they replace: the default locale
is English, so the existing test suite doubles as the regression gate for this
file. Every key must also exist in :mod:`zenagent.i18n.zh`.
"""

from __future__ import annotations

__all__ = ["CATALOG"]

CATALOG: dict[str, str] = {
    "test.greeting": "hello {name}",
    "test.apples.one": "{n} apple",
    "test.apples.other": "{n} apples",
}
