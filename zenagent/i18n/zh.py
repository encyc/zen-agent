"""简体中文 catalog.

本文件与 ``en.py`` 的 key 一一对应（``tests/test_i18n.py`` 会校验 key 集与占位符）。
措辞力求短、动宾清楚，和 docs 里的中文术语一致；命令名 / flag / tool 名 / 代码块不翻译。
"""

from __future__ import annotations

__all__ = ["CATALOG"]

CATALOG: dict[str, str] = {
    "test.greeting": "你好 {name}",
    "test.apples.one": "{n} 个苹果",
    "test.apples.other": "{n} 个苹果",
}
