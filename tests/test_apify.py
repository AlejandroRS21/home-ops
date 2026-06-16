"""Tests for the Apify fallback scraper module."""

import pytest

from home_ops.scraper.apify import ApifyFallback


class TestApifyFallback:
    """ApifyFallback stub tests."""

    def test_fetch_raises_not_implemented(self) -> None:
        """GIVEN ApifyFallback instance WHEN fetch called THEN raises NotImplementedError."""
        fallback = ApifyFallback()
        with pytest.raises(NotImplementedError) as exc_info:
            fallback.fetch("https://www.idealista.com/")
        assert "v0.1" in str(exc_info.value)
        assert "v1.x" in str(exc_info.value)

    def test_init_with_token(self) -> None:
        """GIVEN api_token passed to constructor WHEN used THEN token stored."""
        fallback = ApifyFallback(api_token="test_token_123")
        assert fallback.api_token == "test_token_123"

    def test_init_without_token(self) -> None:
        """GIVEN no api_token WHEN constructed THEN token is None."""
        fallback = ApifyFallback()
        assert fallback.api_token is None
