"""Query syntax: parsing, per-engine formatting and lenient constraint filtering."""

import pytest

from zenagent.tools.web import net, registry
from zenagent.tools.web.query import (
    GOOGLE_SYNTAX,
    NATURAL_SYNTAX,
    apply_constraints,
    format_query,
    format_scraper_query,
    parse_query,
)
from zenagent.tools.web.tools import run_search
from zenagent.tools.web.types import SearchResult


def test_quotes_become_phrases_and_stay_quoted():
    parsed = parse_query('"release schedule" python')
    assert parsed.terms[0].phrase is True
    assert format_query(parsed, GOOGLE_SYNTAX) == '"release schedule" python'


def test_directives_are_extracted_not_searched():
    parsed = parse_query(
        'recall site:github.com -site:reddit.com filetype:pdf lang:en after:2025-01-01 python'
    )
    assert parsed.sites == ["github.com"]
    assert parsed.excluded_sites == ["reddit.com"]
    assert parsed.filetypes == ["pdf"]
    assert parsed.lang == "en"
    assert parsed.after == "2025-01-01"
    assert "site:" not in format_query(parsed, NATURAL_SYNTAX)
    assert "python" in format_query(parsed, NATURAL_SYNTAX)


def test_scheme_and_path_in_site_directives_are_normalised():
    parsed = parse_query("site:https://Docs.Python.org/3/ release")
    assert parsed.sites == ["docs.python.org/3"]


def test_or_groups_survive_for_engines_that_parse_them():
    parsed = parse_query("sdk OR library site:github.com")
    assert "OR" in format_query(parsed, GOOGLE_SYNTAX)


def test_negation_is_dropped_for_natural_language_engines():
    parsed = parse_query("httpx -requests")
    assert "-requests" in format_query(parsed, GOOGLE_SYNTAX)
    assert "-requests" not in format_query(parsed, NATURAL_SYNTAX)


def test_unknown_directives_fall_back_to_keywords():
    parsed = parse_query("issn:1234-5678 paper")
    assert parsed.has_directives is False
    assert "issn:1234-5678" in format_query(parsed, GOOGLE_SYNTAX)


def test_bad_dates_stay_keywords():
    parsed = parse_query("after:yesterday news")
    assert parsed.after is None
    assert "after:yesterday" in format_query(parsed, GOOGLE_SYNTAX)


def test_scraper_query_demotes_path_sites_and_inurl():
    parsed = parse_query("site:github.com/anthropics inurl:issues claude")
    formatted = format_scraper_query(parsed, GOOGLE_SYNTAX)
    assert "site:github.com/anthropics" not in formatted
    assert "inurl:" not in formatted
    assert "github.com/anthropics" in formatted and "issues" in formatted


def test_scaper_keeps_bare_domain_site_filters():
    parsed = parse_query("site:arxiv.org pretraining")
    assert "site:arxiv.org" in format_scraper_query(parsed, GOOGLE_SYNTAX)


def test_directive_only_query_still_produces_something():
    parsed = parse_query("site:arxiv.org filetype:pdf")
    assert format_query(parsed, NATURAL_SYNTAX) == "arxiv.org pdf"


def test_constraint_filter_drops_violating_rows():
    parsed = parse_query("site:python.org releases")
    rows = [
        SearchResult(title="a", url="https://python.org/1", provider="x"),
        SearchResult(title="b", url="https://blog.example/2", provider="x"),
    ]
    kept, relaxed = apply_constraints(rows, parsed)
    assert [row.url for row in kept] == ["https://python.org/1"]
    assert relaxed == []


def test_a_voiding_constraint_is_relaxed_not_enforced():
    parsed = parse_query("site:python.org releases")
    rows = [SearchResult(title="a", url="https://blog.example/2", provider="x")]
    kept, relaxed = apply_constraints(rows, parsed)
    assert kept == rows, "an impossible constraint must not return nothing"
    assert relaxed == ["site:python.org"]


def test_date_bounds_keep_unknown_dates():
    parsed = parse_query("news after:2025-06-01")
    rows = [
        SearchResult(title="dated old", url="https://a/1", provider="x", published="2024-01-01"),
        SearchResult(title="unknown", url="https://a/2", provider="x"),
    ]
    kept, _relaxed = apply_constraints(rows, parsed)
    assert [row.title for row in kept] == ["unknown"]


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for name in ("DEEPSEEK_API_KEY", "TAVILY_API_KEY", "BRAVE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ZENAGENT_SEARCH_PROVIDERS", "tavily")
    registry._default_chain = None


async def test_tavily_maps_domains_to_api_parameters(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    seen: dict = {}

    async def fake_post(url, **kwargs):
        seen.update(kwargs["json"])
        return httpx_response({"results": [{"title": "t", "url": "https://a/1", "content": "c"}]})

    monkeypatch.setattr(net, "post", fake_post)
    await run_search("docs site:python.org -site:reddit.com", limit=3)
    assert seen["include_domains"] == ["python.org"]
    assert seen["exclude_domains"] == ["reddit.com"]
    assert "site:" not in seen["query"]


async def test_chain_records_a_relaxed_constraint(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")

    async def fake_post(url, **kwargs):
        return httpx_response(
            {"results": [{"title": "t", "url": "https://other.example/1", "content": "c"}]}
        )

    monkeypatch.setattr(net, "post", fake_post)
    result = await run_search("x site:python.org", limit=3)
    assert result.ok, "a relaxed constraint still returns rows"
    assert any("relaxed site:python.org" in attempt for attempt in result.attempts)


def httpx_response(payload):
    import httpx

    return httpx.Response(200, json=payload, request=httpx.Request("POST", "https://x"))
