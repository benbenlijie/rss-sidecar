import pytest
from rss_sidecar.rss_output import generate_stable_feed, generate_bilingual_feed


SAMPLE_ARTICLES = [
    {
        "original_url": "https://example.com/article-1",
        "title_orig": "Original Title",
        "title_trans": "翻译标题",
        "content_orig": "Paragraph one.\n\nParagraph two.",
        "content_trans": "段落一。\n\n段落二。",
        "content_version": 1,
        "published_at": 1720000000,
    },
]


class TestStableFeed:

    def test_unchanging_guid(self):
        xml = generate_stable_feed(SAMPLE_ARTICLES, "Test Feed", "http://localhost:8000")
        assert "https://example.com/article-1</guid>" in xml

    def test_pure_translation_content(self):
        xml = generate_stable_feed(SAMPLE_ARTICLES, "Test Feed", "http://localhost:8000")
        assert "段落一。" in xml
        assert "Paragraph one." not in xml

    def test_skips_untranslated(self):
        articles = [{"original_url": "https://x.com/1", "content_trans": None}]
        xml = generate_stable_feed(articles, "Feed", "http://localhost")
        assert "<item>" not in xml


class TestBilingualFeed:

    def test_versioned_guid(self):
        xml = generate_bilingual_feed(SAMPLE_ARTICLES, "Test Feed", "http://localhost:8000")
        assert "https://example.com/article-1#v1</guid>" in xml

    def test_versioned_guid_updates(self):
        articles = [{**SAMPLE_ARTICLES[0], "content_version": 3}]
        xml = generate_bilingual_feed(articles, "Feed", "http://localhost")
        assert "#v3</guid>" in xml

    def test_contains_both_languages(self):
        xml = generate_bilingual_feed(SAMPLE_ARTICLES, "Feed", "http://localhost")
        assert "段落一。" in xml
        assert "Paragraph one." in xml

    def test_bilingual_html_blocks(self):
        xml = generate_bilingual_feed(SAMPLE_ARTICLES, "Feed", "http://localhost")
        assert 'class="bilingual-block"' in xml
        assert 'class="original"' in xml
        assert 'class="translated"' in xml

    def test_skips_untranslated(self):
        articles = [{"original_url": "https://x.com/1", "content_trans": None}]
        xml = generate_bilingual_feed(articles, "Feed", "http://localhost")
        assert "<item>" not in xml
