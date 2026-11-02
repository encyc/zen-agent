"""The README is the landing page: short, four parts, no dead links on PyPI."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
TEXT = README.read_text(encoding="utf-8")
SECTIONS = ["## Install", "## What this is, and why", "## Features", "## Contributing"]
BLOB = "https://github.com/encyc/zen-agent/blob/main/"


def test_readme_is_a_landing_page_with_four_parts():
    assert len(TEXT.splitlines()) <= 130
    headings = re.findall(r"^## .+$", TEXT, re.MULTILINE)
    assert headings == SECTIONS


def test_readme_links_are_absolute_and_resolve():
    relative = re.findall(r"\]\((?!https?://)([^)#]+)", TEXT)
    assert not relative, f"relative links break on PyPI: {relative}"
    for target in re.findall(re.escape(BLOB) + r"([^)\s]+)", TEXT):
        path = target.split("#")[0]
        assert (ROOT / path).exists(), f"README links a missing file: {path}"


def test_chinese_readme_mirrors_the_landing_page():
    """README.zh-CN.md is a translation, not a second manual."""
    zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert len(zh.splitlines()) <= 130
    assert len(re.findall(r"^## .+$", zh, re.MULTILINE)) == len(SECTIONS)
    assert not re.findall(r"\]\((?!https?://)[^)]", zh)
    for target in re.findall(re.escape(BLOB) + r"([^)\s]+)", zh):
        path = target.split("#")[0]
        assert (ROOT / path).exists(), f"README.zh-CN.md links a missing file: {path}"


def test_the_two_readmes_link_to_each_other():
    zh = f"{BLOB}README.zh-CN.md"
    assert zh in TEXT, "the English README does not link to README.zh-CN.md"
    assert f"{BLOB}README.md" in (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")


def test_readme_python_floor_matches_the_package():
    requires = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "requires-python"
    ]
    floor = requires.removeprefix(">=")
    # Derive from the package, don't hard-code: the README must advertise the
    # floor the wheel actually declares, and must not advertise a higher one.
    assert f"Python **{floor}+**" in TEXT, f"README does not state Python {floor}+"
    assert "3.15+" not in TEXT
