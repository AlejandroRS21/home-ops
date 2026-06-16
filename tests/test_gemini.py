"""Tests for the Gemini Vision scorer module."""

import os

from home_ops.scorer.gemini import GeminiVisionScorer


class TestGeminiVisionScorer:
    """GeminiVisionScorer tests."""

    def test_init_without_api_key_warns(self) -> None:
        """GIVEN no GEMINI_API_KEY env WHEN init THEN logs warning (no crash)."""
        if "GEMINI_API_KEY" in os.environ:
            del os.environ["GEMINI_API_KEY"]
        scorer = GeminiVisionScorer(api_key=None)
        assert scorer.api_key == ""

    def test_init_with_api_key(self) -> None:
        """GIVEN explicit api_key WHEN init THEN key stored."""
        scorer = GeminiVisionScorer(api_key="test-key-123")
        assert scorer.api_key == "test-key-123"

    def test_default_rpm_limit(self) -> None:
        """GIVEN default init WHEN accessed THEN RPM_LIMIT is 15."""
        scorer = GeminiVisionScorer(api_key="test")
        assert scorer.rpm_limit == 15

    def test_custom_rpm_limit(self) -> None:
        """GIVEN custom rpm_limit WHEN init THEN limit applied."""
        scorer = GeminiVisionScorer(api_key="test", rpm_limit=5)
        assert scorer.rpm_limit == 5

    def test_score_photos_returns_empty_dict(self) -> None:
        """GIVEN any image urls WHEN score_photos called THEN returns {} (stub)."""
        scorer = GeminiVisionScorer(api_key="test")
        result = scorer.score_photos(["https://example.com/photo.jpg"])
        assert result == {}

    def test_score_photos_empty_list(self) -> None:
        """GIVEN empty image list WHEN score_photos called THEN returns {}."""
        scorer = GeminiVisionScorer(api_key="test")
        result = scorer.score_photos([])
        assert result == {}

    def test_rate_limit_guard_disabled(self) -> None:
        """GIVEN rpm_limit=-1 WHEN _rate_limit_guard THEN no-op."""
        scorer = GeminiVisionScorer(api_key="test", rpm_limit=-1)
        scorer._rate_limit_guard()  # should not raise

    def test_rate_limit_guard_zero(self) -> None:
        """GIVEN rpm_limit=0 WHEN _rate_limit_guard THEN no-op."""
        scorer = GeminiVisionScorer(api_key="test", rpm_limit=0)
        scorer._rate_limit_guard()  # should not raise
