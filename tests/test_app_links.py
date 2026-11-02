"""Link extraction: verbatim URLs from raw markdown, and `/urls` indexes."""

from zenagent.app.links import extract_links, parse_index

OAUTH = (
    "https://metabase.example.cn/oauth/authorize?response_type=code"
    "&redirect_uri=http%3A%2F%2F127.0.0.1%3A8971%2Fcallback&state=abc"
)


def test_extracts_a_url_from_a_fenced_code_block():
    text = f"打开这个链接：\n\n```\n{OAUTH}\n```\n"
    assert extract_links(text) == [OAUTH]


def test_extracts_markdown_link_targets_and_bare_urls():
    text = "见 [文档](https://example.com/docs) 和 https://example.org/bare 。"
    assert extract_links(text) == ["https://example.com/docs", "https://example.org/bare"]


def test_strips_trailing_punctuation_and_chinese_period():
    text = "打开 (https://example.com/a)。另外 https://example.com/b, 也行"
    assert extract_links(text) == ["https://example.com/a", "https://example.com/b"]


def test_keeps_balanced_parentheses_in_a_url():
    text = "https://en.wikipedia.org/wiki/Foo_(bar)"
    assert extract_links(text) == ["https://en.wikipedia.org/wiki/Foo_(bar)"]


def test_keeps_percent_encoding_verbatim():
    assert extract_links(OAUTH)[0].endswith("state=abc")
    assert "%3A%2F%2F" in extract_links(OAUTH)[0]


def test_deduplicates_keeping_first_order():
    text = "https://a.example/1 https://b.example/2 https://a.example/1"
    assert extract_links(text) == ["https://a.example/1", "https://b.example/2"]


def test_ignores_non_http_schemes_and_relative_paths():
    assert extract_links("ftp://example.com/x 见 docs/guide.md") == []


def test_empty_text_yields_no_links():
    assert extract_links("") == []


def test_parse_index_accepts_digits_and_hash():
    assert parse_index("2") == 2
    assert parse_index(" #12 ") == 12
    assert parse_index("0") == 0


def test_parse_index_rejects_text_and_negative_numbers():
    assert parse_index("hello") is None
    assert parse_index("") is None
    assert parse_index("-1") is None


def test_parse_index_rejects_unicode_digits():
    """`"²".isdigit()` is True but `int("²")` raises — that must not escape."""
    assert parse_index("²") is None


def test_parse_index_survives_a_number_beyond_the_int_digit_limit():
    assert parse_index("9" * 5000) is None


def test_strips_markdown_emphasis_around_a_url():
    assert extract_links("**https://example.com/a**") == ["https://example.com/a"]
    assert extract_links("_https://example.com/b_") == ["https://example.com/b"]


def test_keeps_apostrophes_inside_a_url():
    assert extract_links("https://en.wikipedia.org/wiki/O'Brien") == [
        "https://en.wikipedia.org/wiki/O'Brien"
    ]


def test_strips_a_trailing_table_pipe():
    assert extract_links("|https://example.com/a|") == ["https://example.com/a"]
