"""Unit tests for researcher.py — deduplication and file writing logic."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from researcher import (
    RawArticle,
    _COUNTRY_LINE_RE,
    build_existing_slugs,
    diversify_sorted,
    extract_domain,
    infer_sentiment,
    interleave_sources,
    is_duplicate,
    load_sources,
    make_slug,
    sanitize_body,
    sanitize_plain_text,
    score_article,
    source_credibility,
    strip_dangerous_html,
    truncate_description,
    write_article,
)


def _make_article(**kwargs) -> RawArticle:
    defaults = dict(
        title="Test Article Title",
        url="https://example.com/test",
        summary="This is a short summary of the test article.",
        source_name="BBC News",
        category="Politics",
        published_at=datetime(2026, 3, 20, 12, 0, 0, tzinfo=UTC),
    )
    defaults.update(kwargs)
    return RawArticle(**defaults)


class TestMakeSlug:
    def test_slug_contains_date(self):
        a = _make_article()
        slug = make_slug(a)
        assert slug.startswith("2026-03-20-")

    def test_slug_is_lowercase(self):
        a = _make_article(title="UPPERCASE HEADLINE")
        slug = make_slug(a)
        assert slug == slug.lower()

    def test_slug_no_special_chars(self):
        a = _make_article(title="Hello, World! This is a test: 100% sure.")
        slug = make_slug(a)
        assert all(c.isalnum() or c == "-" for c in slug)


class TestDeduplication:
    def test_duplicate_detected(self):
        a = _make_article()
        slug = make_slug(a)
        existing = {slug}
        assert is_duplicate(slug, existing) is True

    def test_new_article_not_flagged(self):
        a = _make_article()
        slug = make_slug(a)
        existing: set[str] = set()
        assert is_duplicate(slug, existing) is False

    def test_build_existing_slugs_empty_dir(self, tmp_path: Path):
        import researcher
        original = researcher.CONTENT_DIR
        researcher.CONTENT_DIR = tmp_path
        try:
            slugs = build_existing_slugs()
            assert slugs == set()
        finally:
            researcher.CONTENT_DIR = original

    def test_build_existing_slugs_finds_files(self, tmp_path: Path):
        import researcher
        original = researcher.CONTENT_DIR
        researcher.CONTENT_DIR = tmp_path
        try:
            sub = tmp_path / "politics"
            sub.mkdir(parents=True)
            (sub / "2026-03-20-some-article.md").write_text("test")
            (sub / "2026-03-19-another-article.md").write_text("test")
            slugs = build_existing_slugs()
            assert "2026-03-20-some-article" in slugs
            assert "2026-03-19-another-article" in slugs
        finally:
            researcher.CONTENT_DIR = original


class TestSentiment:
    def test_positive_detection(self):
        assert infer_sentiment("GDP growth surges to record high") == "Positive"

    def test_negative_detection(self):
        assert infer_sentiment("War and conflict kills hundreds in crisis zone") == "Negative"

    def test_neutral_fallback(self):
        assert infer_sentiment("Parliament convenes for regular session") == "Neutral"


class TestTruncateDescription:
    def test_short_text_unchanged(self):
        text = "Short text."
        assert truncate_description(text) == text

    def test_long_text_truncated(self):
        text = " ".join(["word"] * 100)
        result = truncate_description(text, max_chars=50)
        assert len(result) <= 55  # allow for ellipsis
        assert result.endswith("…")

    def test_whitespace_normalised(self):
        text = "  multiple   spaces   here  "
        result = truncate_description(text)
        assert "  " not in result


class TestExtractDomain:
    def test_simple_url(self):
        assert extract_domain("https://www.bbc.com/news/article") == "bbc.com"

    def test_no_www(self):
        assert extract_domain("https://espn.com/sports/story") == "espn.com"

    def test_invalid_url(self):
        assert extract_domain("not-a-url") == ""


class TestSanitization:
    def test_strip_script_tags(self):
        text = 'Hello <script>alert("xss")</script> World'
        assert "<script" not in strip_dangerous_html(text)
        assert "alert" not in strip_dangerous_html(text)
        assert "Hello" in strip_dangerous_html(text)

    def test_strip_script_multiline(self):
        text = 'Before<script type="text/javascript">\nvar x=1;\nalert(x);\n</script>After'
        result = strip_dangerous_html(text)
        assert "<script" not in result
        assert "BeforeAfter" == result

    def test_strip_event_handlers(self):
        text = '<img src="x" onerror="alert(1)">'
        result = strip_dangerous_html(text)
        assert "onerror" not in result

    def test_strip_javascript_protocol(self):
        text = '<a href="javascript:alert(1)">click</a>'
        result = strip_dangerous_html(text)
        assert "javascript:" not in result.lower()

    def test_sanitize_plain_text_strips_anchors(self):
        text = 'Check <a href="http://evil.com">this link</a> out'
        result = sanitize_plain_text(text)
        assert "<a" not in result
        assert "</a>" not in result
        assert "this link" in result
        assert "Check" in result

    def test_sanitize_plain_text_strips_all_tags(self):
        text = "<b>Bold</b> and <em>italic</em> <span>text</span>"
        result = sanitize_plain_text(text)
        assert "<" not in result
        assert "Bold and italic text" == result

    def test_sanitize_plain_text_collapses_whitespace(self):
        text = "  too   many    spaces  "
        assert "too many spaces" == sanitize_plain_text(text)

    def test_sanitize_body_removes_scripts_keeps_markdown(self):
        text = '## Header\n\nSome text <script>bad()</script> and **bold**.'
        result = sanitize_body(text)
        assert "<script" not in result
        assert "## Header" in result
        assert "**bold**" in result

    def test_sanitize_body_strips_anchors(self):
        text = 'Read more at <a href="http://example.com">example</a>.'
        result = sanitize_body(text)
        assert "<a" not in result
        assert "example" in result


class TestInterleaveSources:
    def test_round_robin(self):
        articles = [
            _make_article(title=f"BBC {i}", source_name="BBC News") for i in range(3)
        ] + [
            _make_article(title=f"ESPN {i}", source_name="ESPN") for i in range(3)
        ]
        result = interleave_sources(articles)
        # All articles preserved
        assert len(result) == 6

    def test_empty_list(self):
        assert interleave_sources([]) == []


class TestScoring:
    def test_high_importance_keywords(self):
        a = _make_article(title="Nuclear war threat as president announces sanctions")
        score = score_article(a)
        assert score > 40  # multiple high keywords + BBC source

    def test_low_importance_fluff(self):
        a = _make_article(title="Local bake sale raises money", source_name="Unknown Blog")
        score = score_article(a)
        assert score < 30

    def test_source_credibility_known(self):
        assert source_credibility("Reuters") == 100
        assert source_credibility("BBC News") == 95
        assert source_credibility("ESPN") == 70

    def test_source_credibility_unknown(self):
        assert source_credibility("Random Blog") == 50

    def test_score_between_0_and_100(self):
        a = _make_article(title="Something happened")
        score = score_article(a)
        assert 0 <= score <= 100


class TestDiversifySorted:
    def test_no_more_than_2_consecutive_same_source(self):
        articles = [
            _make_article(title=f"BBC {i}", source_name="BBC News", importance=90 - i)
            for i in range(6)
        ] + [
            _make_article(title=f"ESPN {i}", source_name="ESPN", importance=50 - i)
            for i in range(3)
        ]
        result = diversify_sorted(articles)
        for i in range(len(result) - 2):
            same = (result[i].source_name == result[i+1].source_name == result[i+2].source_name)
            if same:
                # Only acceptable at very end when no alternatives left
                assert all(r.source_name == result[i].source_name for r in result[i:])
        assert len(result) == 9


class TestCountryLineParsing:
    """Verify the COUNTRY: XX regex extracts country codes from LLM output."""

    def test_parses_valid_country(self):
        raw = "COUNTRY: US\n\n## Headline\n\nBody text."
        m = _COUNTRY_LINE_RE.search(raw)
        assert m is not None
        assert m.group(1) == "US"

    def test_no_country_line(self):
        raw = "## Headline\n\nBody text without country."
        m = _COUNTRY_LINE_RE.search(raw)
        assert m is None

    def test_strips_country_line_from_body(self):
        raw = "COUNTRY: GB\n\n## Headline\n\nBody text."
        m = _COUNTRY_LINE_RE.search(raw)
        body = (raw[:m.start()] + raw[m.end():]).strip()
        assert "COUNTRY" not in body
        assert body.startswith("## Headline")


class TestWriteArticle:
    def test_file_is_created(self, tmp_path: Path):
        import researcher
        original = researcher.CONTENT_DIR
        researcher.CONTENT_DIR = tmp_path
        try:
            a = _make_article()
            slug = make_slug(a)
            path = write_article(a, "## Summary\n\nBody text here.", slug)
            assert path.exists()
            assert path.name == f"{slug}.md"
        finally:
            researcher.CONTENT_DIR = original

    def test_flat_category_path(self, tmp_path: Path):
        """Articles should be written to {category}/{slug}.md — no country subdirectory."""
        import researcher
        original = researcher.CONTENT_DIR
        researcher.CONTENT_DIR = tmp_path
        try:
            a = _make_article()
            slug = make_slug(a)
            path = write_article(a, "## Summary\n\nBody.", slug)
            expected_path = tmp_path / "politics" / f"{slug}.md"
            assert path == expected_path
            assert expected_path.exists()
        finally:
            researcher.CONTENT_DIR = original

    def test_frontmatter_fields(self, tmp_path: Path):
        import frontmatter as fm
        import researcher
        original = researcher.CONTENT_DIR
        researcher.CONTENT_DIR = tmp_path
        try:
            a = _make_article()
            slug = make_slug(a)
            write_article(a, "## Summary\n\nBody.", slug)
            expected_path = tmp_path / "politics" / f"{slug}.md"
            post = fm.load(str(expected_path))
            assert post["title"] == a.title
            assert post["category"] == "Politics"
            assert post["source"] == "example.com"
            assert post["importance"] >= 0
            assert post["sentiment"] in ("Positive", "Negative", "Neutral")
            assert "country" not in post.metadata
            assert "countryCode" not in post.metadata
        finally:
            researcher.CONTENT_DIR = original

    def test_frontmatter_country_from_synthesis(self, tmp_path: Path):
        """When country_code is provided, country and countryCode appear in frontmatter."""
        import frontmatter as fm
        import researcher
        original = researcher.CONTENT_DIR
        researcher.CONTENT_DIR = tmp_path
        try:
            a = _make_article()
            slug = make_slug(a)
            write_article(a, "## Summary\n\nBody.", slug, country_code="US")
            expected_path = tmp_path / "politics" / f"{slug}.md"
            post = fm.load(str(expected_path))
            assert post["country"] == "United States"
            assert post["countryCode"] == "US"
        finally:
            researcher.CONTENT_DIR = original

    def test_frontmatter_country_zz_omitted(self, tmp_path: Path):
        """ZZ (global) country code should not produce country fields."""
        import frontmatter as fm
        import researcher
        original = researcher.CONTENT_DIR
        researcher.CONTENT_DIR = tmp_path
        try:
            a = _make_article()
            slug = make_slug(a)
            write_article(a, "## Summary\n\nBody.", slug, country_code="ZZ")
            expected_path = tmp_path / "politics" / f"{slug}.md"
            post = fm.load(str(expected_path))
            assert "country" not in post.metadata
            assert "countryCode" not in post.metadata
        finally:
            researcher.CONTENT_DIR = original


class TestLoadSources:
    """Verify load_sources reads categories from the correct column."""

    def test_categories_not_countries(self, tmp_path: Path):
        """Categories should come from column 4 (Categories), not column 3 (Countries)."""
        md = (
            "| Name | Type | URL | Countries | Categories | active |\n"
            "|------|------|-----|-----------|------------|--------|\n"
            "| ESPN | rss | https://espn.com/rss | global | Sports | yes |\n"
            "| TechCrunch | rss | https://tc.com/feed | global | Tech | yes |\n"
            "| BBC | rss | https://bbc.com/rss | global | Politics,Economy | yes |\n"
        )
        sources_file = tmp_path / "sources.md"
        sources_file.write_text(md)

        import researcher
        original = researcher.SOURCES_FILE
        researcher.SOURCES_FILE = sources_file
        try:
            sources = load_sources()
            by_name = {s.name: s for s in sources}
            assert by_name["ESPN"].categories == ["Sports"]
            assert by_name["TechCrunch"].categories == ["Tech"]
            assert by_name["BBC"].categories == ["Politics", "Economy"]
        finally:
            researcher.SOURCES_FILE = original
