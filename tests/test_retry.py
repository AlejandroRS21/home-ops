"""Tests for the retry decorator module."""

import pytest

from home_ops.scraper.retry import retry_with_backoff


class TestRetryWithBackoff:
    """Retry decorator tests."""

    def test_success_on_first_attempt(self) -> None:
        """GIVEN function succeeds immediately WHEN called THEN returns value."""
        call_count = 0

        @retry_with_backoff(max_attempts=3, base_delay=0.01)
        def succeed() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        result = succeed()
        assert result == "ok"
        assert call_count == 1

    def test_retries_and_succeeds(self) -> None:
        """GIVEN function fails twice THEN retries before succeeding."""
        call_count = 0

        @retry_with_backoff(max_attempts=5, base_delay=0.01)
        def eventually_succeeds() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError(f"Attempt {call_count} failed")
            return "ok"

        result = eventually_succeeds()
        assert result == "ok"
        assert call_count == 3

    def test_exhausts_retries_and_raises(self) -> None:
        """GIVEN function always fails WHEN all attempts exhausted THEN raises."""
        call_count = 0

        @retry_with_backoff(max_attempts=3, base_delay=0.01)
        def always_fails() -> str:
            nonlocal call_count
            call_count += 1
            raise ValueError("Always fails")

        with pytest.raises(ValueError, match="Always fails"):
            always_fails()
        assert call_count == 3

    def test_single_attempt_no_retry(self) -> None:
        """GIVEN max_attempts=1 WHEN function fails THEN raises immediately."""
        call_count = 0

        @retry_with_backoff(max_attempts=1, base_delay=0.01)
        def fail_once() -> str:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("Fail")

        with pytest.raises(RuntimeError):
            fail_once()
        assert call_count == 1
