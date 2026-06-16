"""Apify fallback scraper (stub for future implementation).

The ``ApifyFallback`` class provides the same interface as the main
scraper lifecycle (``cold_start`` / ``subsequent_run``) but relies on
Apify's Idealista scraper actor instead of direct HTTP fetching.

⚠️  **v0.1 placeholder** — all methods raise ``NotImplementedError``.
Actual Apify integration is planned for v1.x.
"""

from __future__ import annotations


class ApifyFallback:
    """Scraper that delegates to Apify's Idealista actor.

    This is the secondary scraping strategy: when the primary Scrapling-based
    scraper fails (or when the user explicitly chooses the Apify path), the
    pipeline will use this class to obtain listing data via the Apify API.

    Future interface (planned for v1.x)::

        fallback = ApifyFallback(api_token="...")
        listings = fallback.fetch("https://www.idealista.com/..."

    Attributes:
        api_token: Apify API token (loaded from ``APIFY_API_TOKEN`` env var).
    """

    def __init__(self, api_token: str | None = None) -> None:
        """Initialise the Apify fallback.

        Args:
            api_token: Apify API token.  When ``None`` the token is expected
                       to be configured elsewhere (e.g. via environment
                       variables in the pipeline runner).
        """
        self.api_token = api_token

    def fetch(self, url: str) -> None:
        """Fetch listings from an Idealista search URL via Apify.

        Args:
            url: The Idealista search URL to scrape.

        Raises:
            NotImplementedError: Always — this is a v0.1 placeholder.
        """
        raise NotImplementedError(
            "Apify fallback not implemented in v0.1. "
            "Coming in v1.x."
        )
