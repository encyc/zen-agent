"""Google-style query parsing, per-engine re-formatting and lenient filtering.

Agents write `site:`, `after:`, `inurl:`, `filetype:`, quoted phrases and
`-exclusions` regardless of what the backing engine supports. Parsing them
once lets each provider do the right three things:

1. map what it can onto native parameters (Tavily's ``include_domains`` …);
2. re-emit only the syntax its engine actually understands;
3. filter the returned rows leniently — a constraint that would eliminate
   every result is relaxed and reported rather than returning nothing.

Semantics follow omp's ``web/search/query.ts``; the operator set here is the
subset our providers can honour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

__all__ = [
    "BRAVE_SYNTAX",
    "DEEPSEEK_SYNTAX",
    "GOOGLE_SYNTAX",
    "NATURAL_SYNTAX",
    "QuerySyntax",
    "StructuredQuery",
    "apply_constraints",
    "format_query",
    "format_scraper_query",
    "parse_query",
]

DIRECTIVE = re.compile(r"^([+-]?)([a-z][a-z-]*):(.*)$", re.IGNORECASE)
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_YEAR = re.compile(r"^(\d{4})$")


@dataclass(frozen=True)
class Term:
    """One free-text token: a word, a quoted phrase, or a negation."""

    text: str
    phrase: bool = False
    negated: bool = False


@dataclass
class StructuredQuery:
    raw: str
    terms: list[Term] = field(default_factory=list)
    sites: list[str] = field(default_factory=list)
    excluded_sites: list[str] = field(default_factory=list)
    in_url: list[str] = field(default_factory=list)
    excluded_in_url: list[str] = field(default_factory=list)
    in_title: list[str] = field(default_factory=list)
    excluded_in_title: list[str] = field(default_factory=list)
    in_text: list[str] = field(default_factory=list)
    filetypes: list[str] = field(default_factory=list)
    excluded_filetypes: list[str] = field(default_factory=list)
    after: str | None = None
    before: str | None = None
    lang: str | None = None
    has_directives: bool = False

    @property
    def has_constraints(self) -> bool:
        return bool(
            self.sites
            or self.excluded_sites
            or self.in_url
            or self.in_title
            or self.filetypes
            or self.after
            or self.before
        )


@dataclass(frozen=True)
class QuerySyntax:
    """What a target engine parses; everything defaults to off."""

    phrases: bool = False
    negation: bool = False
    or_groups: bool = False
    site: bool = False
    in_url: bool = False
    in_title: bool = False
    in_text: bool = False
    filetype: bool = False
    date_range: bool = False


GOOGLE_SYNTAX = QuerySyntax(
    phrases=True,
    negation=True,
    or_groups=True,
    site=True,
    in_url=True,
    in_title=True,
    in_text=True,
    filetype=True,
    date_range=True,
)
BRAVE_SYNTAX = QuerySyntax(
    phrases=True, negation=True, or_groups=True, site=True, filetype=True
)
DEEPSEEK_SYNTAX = QuerySyntax(
    phrases=True, negation=True, or_groups=True, site=True, date_range=True
)
NATURAL_SYNTAX = QuerySyntax(phrases=True)


# -- parsing -----------------------------------------------------------------


def _is_quote(char: str) -> bool:
    return char in "\"“”"


def _tokenize(raw: str) -> list[tuple[str, bool, bool]]:
    """``(text, quoted, quoted_value)`` per whitespace token, honouring quotes."""
    tokens: list[tuple[str, bool, bool]] = []
    index, length = 0, len(raw)
    while index < length:
        if raw[index].isspace():
            index += 1
            continue
        if _is_quote(raw[index]):
            end = index + 1
            buffer = ""
            while end < length and not _is_quote(raw[end]):
                buffer += raw[end]
                end += 1
            if buffer.strip():
                tokens.append((buffer.strip(), True, False))
            index = end + 1
            continue
        buffer = ""
        quoted_value = False
        while index < length and not raw[index].isspace():
            char = raw[index]
            if _is_quote(char) and buffer.endswith(":"):
                end = index + 1
                while end < length and not _is_quote(raw[end]):
                    buffer += raw[end]
                    end += 1
                quoted_value = True
                index = end + 1
                continue
            if _is_quote(char):
                break
            buffer += char
            index += 1
        if buffer:
            tokens.append((buffer, False, quoted_value))
    return tokens


def _normalize_site(value: str) -> str:
    site = value.strip().lower()
    for prefix in ("https://", "http://"):
        site = site.removeprefix(prefix)
    return site.rstrip("/")


def _normalize_date(value: str) -> str | None:
    text = value.strip()
    if match := _ISO_DATE.match(text):
        try:
            date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
        return text
    if match := _YEAR.match(text):
        return f"{match.group(1)}-01-01"
    return None


def parse_query(raw: str) -> StructuredQuery:
    """Decompose a raw query; unrecognised directives stay plain terms."""
    parsed = StructuredQuery(raw=raw)
    tokens = _tokenize(raw)
    index = 0
    while index < len(tokens):
        text, quoted, _ = tokens[index]
        if quoted:
            parsed.terms.append(Term(text=text, phrase=True))
            index += 1
            continue
        if text.upper() == "OR" and parsed.terms:
            parsed.terms.append(Term(text="__OR__"))
            index += 1
            continue
        if text.upper() == "NOT" and index + 1 < len(tokens):
            parsed.terms.append(Term(text=tokens[index + 1][0], negated=True))
            index += 2
            continue
        match = DIRECTIVE.match(text)
        if match is None:
            negated = text.startswith("-") and len(text) > 1
            parsed.terms.append(
                Term(text=text[1:] if negated else text, negated=negated)
            )
            index += 1
            continue
        sign, name, value = match.groups()
        name = name.lower()
        negated = sign == "-"
        if _handle_directive(parsed, name, value, negated):
            parsed.has_directives = True
        else:
            parsed.terms.append(Term(text=text, negated=negated))
        index += 1
    _collapse_or_groups(parsed)
    return parsed


def _handle_directive(parsed: StructuredQuery, name: str, value: str, negated: bool) -> bool:
    value = value.strip()
    if not value:
        return False
    if name in ("site", "domain", "host"):
        target = _normalize_site(value)
        (parsed.excluded_sites if negated else parsed.sites).append(target)
        return True
    if name in ("inurl", "url", "allinurl"):
        (parsed.excluded_in_url if negated else parsed.in_url).append(value)
        return True
    if name in ("intitle", "title", "allintitle"):
        (parsed.excluded_in_title if negated else parsed.in_title).append(value)
        return True
    if name in ("intext", "inbody", "inanchor", "allintext"):
        if not negated:
            parsed.in_text.append(value)
        return True
    if name in ("filetype", "ext"):
        extension = value.lower().lstrip(".")
        (parsed.excluded_filetypes if negated else parsed.filetypes).append(extension)
        return True
    if name in ("after", "since") and not negated:
        parsed.after = _normalize_date(value)
        return parsed.after is not None
    if name in ("before", "until") and not negated:
        parsed.before = _normalize_date(value)
        return parsed.before is not None
    if name in ("lang", "language") and not negated:
        parsed.lang = value.lower()
        return True
    return False


def _collapse_or_groups(parsed: StructuredQuery) -> None:
    """Fold ``a OR b`` into one phrase-joined term so formatters stay simple."""
    collapsed: list[Term] = []
    index = 0
    terms = parsed.terms
    while index < len(terms):
        term = terms[index]
        if (
            term.text == "__OR__"
            and collapsed
            and index + 1 < len(terms)
        ):
            left = collapsed.pop()
            right = terms[index + 1]
            collapsed.append(Term(text=f"{left.text} OR {right.text}", phrase=False))
            index += 2
            continue
        if term.text == "__OR__":
            index += 1
            continue
        collapsed.append(term)
        index += 1
    parsed.terms = collapsed


# -- formatting --------------------------------------------------------------


def _quote(value: str) -> str:
    return f'"{value}"' if " " in value else value


def format_query(parsed: StructuredQuery, syntax: QuerySyntax = GOOGLE_SYNTAX) -> str:
    """Re-emit the query using only the operators ``syntax`` declares."""
    parts: list[str] = []
    terms: list[str] = []
    for term in parsed.terms:
        if term.negated:
            if syntax.negation:
                terms.append(f"-{term.text}")
            continue
        terms.append(f'"{term.text}"' if term.phrase and syntax.phrases else term.text)
    if terms:
        parts.append(" ".join(terms))

    if syntax.site:
        if parsed.sites:
            if len(parsed.sites) > 1 and syntax.or_groups:
                parts.append("(" + " OR ".join(f"site:{s}" for s in parsed.sites) + ")")
            else:
                parts.extend(f"site:{site}" for site in parsed.sites)
        parts.extend(f"-site:{site}" for site in parsed.excluded_sites)
    if syntax.in_url:
        parts.extend(f"inurl:{_quote(v)}" for v in parsed.in_url)
        parts.extend(f"-inurl:{_quote(v)}" for v in parsed.excluded_in_url)
    if syntax.in_title:
        parts.extend(f"intitle:{_quote(v)}" for v in parsed.in_title)
        parts.extend(f"-intitle:{_quote(v)}" for v in parsed.excluded_in_title)
    if syntax.in_text:
        parts.extend(f"intext:{_quote(v)}" for v in parsed.in_text)
    if syntax.filetype:
        parts.extend(f"filetype:{ext}" for ext in parsed.filetypes)
        parts.extend(f"-filetype:{ext}" for ext in parsed.excluded_filetypes)
    if syntax.date_range:
        if parsed.after:
            parts.append(f"after:{parsed.after}")
        if parsed.before:
            parts.append(f"before:{parsed.before}")
    if parsed.lang and syntax.site:  # lang: rides the keyword dialects
        parts.append(f"lang:{parsed.lang.split('-')[0]}")

    result = " ".join(parts).strip()
    if not result:
        fallback = (
            parsed.sites
            + parsed.in_title
            + parsed.in_url
            + parsed.in_text
            + parsed.filetypes
        )
        result = " ".join(fallback).strip()
    return result or parsed.raw.strip()


def format_scraper_query(parsed: StructuredQuery, syntax: QuerySyntax = GOOGLE_SYNTAX) -> str:
    """Keyless HTML engines only match bare-domain ``site:`` and ignore ``inurl:``.

    Path-carrying ``site:`` and every ``inurl:`` value are demoted to plain
    keywords before formatting; the negative forms pass through untouched
    (demoting an exclusion would search for it instead).
    """
    demoted = [site for site in parsed.sites if "/" in site]
    if not demoted and not parsed.in_url and not parsed.in_title and not parsed.in_text:
        return format_query(parsed, syntax)

    adjusted = StructuredQuery(
        raw=parsed.raw,
        terms=list(parsed.terms)
        + [Term(text=value) for value in (*demoted, *parsed.in_url)],
        sites=[site for site in parsed.sites if "/" not in site],
        excluded_sites=list(parsed.excluded_sites),
        excluded_in_url=list(parsed.excluded_in_url),
        excluded_in_title=list(parsed.excluded_in_title),
        in_title=list(parsed.in_title),
        filetypes=list(parsed.filetypes),
        excluded_filetypes=list(parsed.excluded_filetypes),
        after=parsed.after,
        before=parsed.before,
        lang=parsed.lang,
        has_directives=True,
    )
    return format_query(adjusted, syntax)


# -- post-filtering ----------------------------------------------------------


def apply_constraints(
    results: list, parsed: StructuredQuery
) -> tuple[list, list[str]]:
    """Drop rows that violate a constraint; relax a dimension that voids them all.

    Returns the surviving rows and the directive renderings that had to be
    relaxed (empty when everything matched).
    """
    if not parsed.has_constraints or not results:
        return results, []

    checks = []
    if parsed.sites:
        checks.append(
            (lambda item: any(site in item.url.lower() for site in parsed.sites), f"site:{parsed.sites[0]}")
        )
    if parsed.excluded_sites:
        checks.append(
            (
                lambda item: not any(site in item.url.lower() for site in parsed.excluded_sites),
                f"-site:{parsed.excluded_sites[0]}",
            )
        )
    if parsed.excluded_filetypes:
        checks.append(
            (
                lambda item: not any(
                    item.url.lower().split("?")[0].endswith("." + ext)
                    for ext in parsed.excluded_filetypes
                ),
                f"-filetype:{parsed.excluded_filetypes[0]}",
            )
        )
    if parsed.filetypes:
        checks.append(
            (
                lambda item: any(
                    item.url.lower().split("?")[0].endswith("." + ext)
                    for ext in parsed.filetypes
                ),
                f"filetype:{parsed.filetypes[0]}",
            )
        )
    if parsed.in_title:
        checks.append(
            (
                lambda item: all(word.lower() in item.title.lower() for word in parsed.in_title),
                f"intitle:{parsed.in_title[0]}",
            )
        )
    if parsed.after or parsed.before:
        def in_range(item) -> bool:
            published = (item.published or "")[:10]
            if not published:
                return True  # unknown date: keep, never void the set
            if parsed.after and published < parsed.after:
                return False
            return not (parsed.before and published >= parsed.before)

        bound = f"after:{parsed.after}" if parsed.after else f"before:{parsed.before}"
        checks.append((in_range, bound))

    kept = results
    dropped: list[str] = []
    for predicate, label in checks:
        candidate = [item for item in kept if predicate(item)]
        if candidate:
            kept = candidate
        else:
            dropped.append(label)
    return kept, dropped
