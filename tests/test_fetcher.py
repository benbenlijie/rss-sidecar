import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from rss_sidecar.fetcher import validate_url, fetch_feed, FeedItem


class TestValidateUrl:

    def test_blocks_loopback(self):
        assert validate_url("http://127.0.0.1/admin") is False

    def test_blocks_link_local(self):
        assert validate_url("http://169.254.169.254/meta-data") is False

    def test_blocks_file_scheme(self):
        assert validate_url("file:///etc/passwd") is False

    def test_blocks_ftp(self):
        assert validate_url("ftp://example.com/file") is False


class TestFetchFeed:

    async def test_returns_none_for_ssrf_url(self):
        title, items = await fetch_feed("http://127.0.0.1:8080/feed.xml")
        assert title is None
        assert items == []

    async def test_parses_valid_feed(self):
        SAMPLE_RSS = """<?xml version="1.0"?>
        <rss version="2.0">
        <channel>
            <title>Test Feed</title>
            <item>
                <title>Article One</title>
                <link>https://example.com/1</link>
                <guid>guid-1</guid>
                <description>Summary one</description>
            </item>
            <item>
                <title>Article Two</title>
                <link>https://example.com/2</link>
                <guid>guid-2</guid>
            </item>
        </channel>
        </rss>"""

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_RSS

        with patch("rss_sidecar.fetcher.httpx.AsyncClient") as mock_client_cls, \
             patch("rss_sidecar.fetcher.validate_url", return_value=True):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            title, items = await fetch_feed("https://example.com/feed.xml")

        assert title == "Test Feed"
        assert len(items) == 2
        assert items[0].title == "Article One"
        assert items[0].url == "https://example.com/1"
        assert items[1].summary is None

    async def test_returns_empty_on_http_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = ""

        with patch("rss_sidecar.fetcher.httpx.AsyncClient") as mock_client_cls, \
             patch("rss_sidecar.fetcher.validate_url", return_value=True):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            title, items = await fetch_feed("https://example.com/feed.xml")

        assert title is None
        assert items == []
