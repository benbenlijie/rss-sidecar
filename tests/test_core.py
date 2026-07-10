import pytest
import asyncio
from rss_sidecar.fetcher import validate_url


class TestSSRFProtection:
    def test_blocks_localhost(self):
        assert validate_url("http://127.0.0.1:8080/admin") is False

    def test_blocks_private_ip(self):
        assert validate_url("http://192.168.1.1") is False
        assert validate_url("http://10.0.0.1") is False

    def test_blocks_169_254(self):
        assert validate_url("http://169.254.169.254/latest/meta-data/") is False

    def test_allows_public_url(self):
        assert validate_url("https://example.com/feed.xml") is True

    def test_rejects_non_http(self):
        assert validate_url("file:///etc/passwd") is False
        assert validate_url("ftp://example.com") is False


class TestCostEstimation:
    def test_gpt4o_mini_cost(self):
        from rss_sidecar.translator import estimate_cost
        cost = estimate_cost("gpt-4o-mini", 1000, 1000)
        assert 0.0007 < cost < 0.0008

    def test_unknown_model_uses_default(self):
        from rss_sidecar.translator import estimate_cost
        cost = estimate_cost("unknown-model", 1000, 1000)
        assert cost > 0
