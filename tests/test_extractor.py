import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from rss_sidecar.extractor import extract_full_content, _fallback, ExtractionResult


class TestFallback:

    def test_uses_rss_content_when_available(self):
        long_content = "A" * 101
        result = _fallback(long_content, "short summary")
        assert result.method == "rss_content"
        assert len(result.text) == 101

    def test_uses_rss_summary_when_no_content(self):
        result = _fallback("", "A summary from RSS feed")
        assert result.method == "rss_summary"
        assert result.text == "A summary from RSS feed"

    def test_returns_none_when_all_empty(self):
        result = _fallback("", "")
        assert result.text is None
        assert result.method == "failed"

    def test_prefers_content_over_summary(self):
        long_content = "B" * 150
        result = _fallback(long_content, "Just a summary")
        assert result.method == "rss_content"

    def test_short_content_falls_through_to_summary(self):
        result = _fallback("ab", "A meaningful summary text")
        assert result.method == "rss_summary"


class TestExtractFullContent:

    async def test_returns_summary_on_invalid_url(self):
        result = await extract_full_content(
            "http://127.0.0.1:8080/admin",
            rss_summary="Fallback summary",
        )
        assert result.method in ("rss_summary", "failed")

    async def test_returns_rss_content_on_http_error(self):
        with patch("rss_sidecar.extractor.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=MagicMock(status_code=403, text=""))
            mock_client_cls.return_value = mock_client

            result = await extract_full_content(
                "https://example.com/article",
                rss_summary="RSS summary fallback",
            )
            assert result.method == "rss_summary"

    async def test_returns_trafilatura_on_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><p>" + "A" * 300 + "</p></body></html>"

        with patch("rss_sidecar.extractor.httpx.AsyncClient") as mock_client_cls, \
             patch("rss_sidecar.extractor.trafilatura.extract") as mock_extract:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            mock_extract.return_value = "A" * 300

            result = await extract_full_content("https://example.com/article")
            assert result.method == "trafilatura"
            assert len(result.text) == 300
