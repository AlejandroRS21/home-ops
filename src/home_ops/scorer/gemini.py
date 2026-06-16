"""Gemini Vision-based listing scorer using photo analysis.

The ``GeminiVisionScorer`` analyses listing photos via Gemini's multi-modal
capabilities (using LiteLLM behind the scenes) and returns a desirability
score.

⚠️  **v0.1 placeholder** — ``score_photos()`` returns an empty dict.  The
actual Gemini call will be wired when the scoring prompt and photo format
are finalised.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)


class GeminiVisionScorer:
    """Scores a listing's photos using Gemini Vision via LiteLLM.

    The scorer enforces a configurable rate limit (requests per minute)
    to stay within Gemini's free-tier quota.

    Usage::

        scorer = GeminiVisionScorer()
        result = scorer.score_photos(["https://…/photo1.jpg"])
        # → {"score": 85.0, "summary": "Well-lit interior, modern finishes"}
    """

    RPM_LIMIT: int = 15

    def __init__(self, api_key: str | None = None, rpm_limit: int | None = None) -> None:
        """Initialise the scorer and load the Gemini API key from the environment.

        Args:
            api_key: Gemini API key.  Falls back to the ``GEMINI_API_KEY``
                     environment variable when ``None``.
            rpm_limit: Maximum requests per minute (default: 15).
                       Use ``-1`` to disable rate limiting.
        """
        key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not key:
            logger.warning(
                "GEMINI_API_KEY not set — GeminiVisionScorer will fail at inference time."
            )
        self.api_key = key
        self.rpm_limit = rpm_limit if rpm_limit is not None else self.RPM_LIMIT
        self._last_request_time: float = 0.0

    def score_photos(self, image_urls: list[str]) -> dict[str, object]:
        """Score a list of listing photo URLs using Gemini Vision.

        Args:
            image_urls: List of publicly accessible image URLs.

        Returns:
            A dict with scoring results.  Currently an empty dict — the
            parsed Gemini response will be returned once the integration
            is wired.
        """
        if not image_urls:
            logger.info("No photos to score — returning empty result.")
            return {}

        self._rate_limit_guard()

        # TODO: Wire LiteLLM Gemini call here.
        #   response = litellm.completion(
        #       model="gemini/gemini-2.0-flash-001",
        #       api_key=self.api_key,
        #       messages=[...],
        #   )
        #
        # The current implementation is a placeholder that returns an
        # empty dict without making an external API call.
        return {}

    def _rate_limit_guard(self) -> None:
        """Sleep if needed to stay within the configured RPM limit.

        If ``rpm_limit`` is -1 or 0 the guard is a no-op (no throttling).
        """
        if self.rpm_limit <= 0:
            return

        min_interval = 60.0 / self.rpm_limit
        elapsed = time.time() - self._last_request_time
        if elapsed < min_interval:
            sleep_for = min_interval - elapsed
            logger.debug("Rate-limit guard: sleeping %.2fs", sleep_for)
            time.sleep(sleep_for)

        self._last_request_time = time.time()
