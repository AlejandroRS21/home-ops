"""Tests for the scraper lifecycle module.

NOTE: The ``cold_start`` function imports ``StealthyFetcher`` lazily via
``_get_fetcher``.  Tests patch ``home_ops.scraper.lifecycle._get_fetcher``
to avoid requiring ``curl_cffi`` at test time.
"""

import sys
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Mock scrapling at sys.modules level BEFORE importing lifecycle module.
# The installed scrapling package requires curl_cffi which is not installed,
# so we prevent the real import chain from running.
_mock_scrapling = MagicMock()
_mock_stealthy_fetcher = MagicMock()
_mock_parser = MagicMock()
_mock_scrapling.StealthyFetcher = lambda: _mock_stealthy_fetcher
_mock_scrapling.parser = _mock_parser
# scrapling.parser submodule needs its own sys.modules entry for direct imports
sys.modules["scrapling"] = _mock_scrapling
sys.modules["scrapling.parser"] = _mock_scrapling.parser

from home_ops.models.data_storage import DuckDBConnection  # noqa: E402
from home_ops.scraper.lifecycle import (  # noqa: E402
    SNAPSHOT_DIR,
    invalidate_snapshots,
)


class TestSnapshotDir:
    """Snapshot directory management tests."""

    def test_invalidate_snapshots_removes_dir(self, tmp_path: Path) -> None:
        """GIVEN existing snapshot dir WHEN invalidate_snapshots THEN dir removed."""
        import home_ops.scraper.lifecycle as lifecycle_mod
        original = lifecycle_mod.SNAPSHOT_DIR
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir(parents=True)
        (snap_dir / "test.snap").write_text("data")
        lifecycle_mod.SNAPSHOT_DIR = snap_dir
        try:
            invalidate_snapshots()
            assert not snap_dir.exists()
        finally:
            lifecycle_mod.SNAPSHOT_DIR = original

    def test_invalidate_nonexistent_does_not_raise(self) -> None:
        """GIVEN no snapshot dir WHEN invalidate_snapshots THEN no error."""
        invalidate_snapshots()  # should not raise


class TestColdStart:
    """Cold start scraper tests."""

    @patch("home_ops.scraper.lifecycle._get_fetcher")
    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    def test_cold_start_raises_on_fetch_failure(
        self, mock_fetch: MagicMock, mock_get_fetcher: MagicMock
    ) -> None:
        """GIVEN _fetch_page_text fails WHEN cold_start THEN returns empty gracefully."""
        from home_ops.scraper.lifecycle import cold_start

        mock_fetch.side_effect = RuntimeError("Fetch failed")
        result = cold_start("https://example.com")
        assert result == []

    @patch("home_ops.scraper.lifecycle._get_fetcher")
    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_listings")
    def test_cold_start_delegates_to_parse_listings(
        self, mock_parse: MagicMock, mock_fetch: MagicMock, mock_get_fetcher: MagicMock
    ) -> None:
        """GIVEN cold_start WHEN called THEN delegates to parse_listings."""
        from home_ops.scraper.lifecycle import cold_start

        mock_fetch.return_value = "<html>mock</html>"
        mock_parse.return_value = [
            {"external_id": "1", "url": "/x", "address": "addr",
             "price": None, "m2": None, "rooms": None, "floor": None,
             "description": "", "portal": "idealista",
             "price_includes_garage": False, "garage_price": None,
             "certificado_energetico_present": None},
        ]

        result = cold_start("https://www.idealista.com/test", max_pages=1)
        assert len(result) == 1
        assert result[0].external_id == "1"
        mock_parse.assert_called_once_with("<html>mock</html>")

    @patch("home_ops.scraper.lifecycle._get_fetcher")
    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_listings")
    def test_cold_start_pagination_multi_page(
        self, mock_parse: MagicMock, mock_fetch: MagicMock, mock_get_fetcher: MagicMock
    ) -> None:
        """GIVEN max_pages=3 WHEN cold_start THEN fetches ?pagina=2 and ?pagina=3."""
        from home_ops.scraper.lifecycle import cold_start

        mock_fetch.return_value = "<html>mock</html>"
        mock_parse.side_effect = [
            [{"external_id": "1", "url": "/1", "address": "a",
              "price": None, "m2": None, "rooms": None, "floor": None,
              "description": "", "portal": "idealista",
              "price_includes_garage": False, "garage_price": None,
              "certificado_energetico_present": None}],
            [{"external_id": "2", "url": "/2", "address": "b",
              "price": None, "m2": None, "rooms": None, "floor": None,
              "description": "", "portal": "idealista",
              "price_includes_garage": False, "garage_price": None,
              "certificado_energetico_present": None}],
            [],
        ]

        result = cold_start("https://www.idealista.com/test", max_pages=3)
        assert len(result) == 2
        assert result[0].external_id == "1"
        assert result[1].external_id == "2"
        # Should have fetched page 1 (base URL), page 2, page 3
        assert mock_fetch.call_count == 3

    @patch("home_ops.scraper.lifecycle._get_fetcher")
    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_listings")
    def test_cold_start_early_stop_on_empty(
        self, mock_parse: MagicMock, mock_fetch: MagicMock, mock_get_fetcher: MagicMock
    ) -> None:
        """GIVEN max_pages=5 but page 3 returns 0 WHEN cold_start THEN stops early."""
        from home_ops.scraper.lifecycle import cold_start

        mock_fetch.return_value = "<html>mock</html>"
        mock_parse.side_effect = [
            [{"external_id": "1", "url": "/1", "address": "a",
              "price": None, "m2": None, "rooms": None, "floor": None,
              "description": "", "portal": "idealista",
              "price_includes_garage": False, "garage_price": None,
              "certificado_energetico_present": None}],
            [{"external_id": "2", "url": "/2", "address": "b",
              "price": None, "m2": None, "rooms": None, "floor": None,
              "description": "", "portal": "idealista",
              "price_includes_garage": False, "garage_price": None,
              "certificado_energetico_present": None}],
            [],  # page 3 empty — stop
        ]

        result = cold_start("https://www.idealista.com/test", max_pages=5)
        assert len(result) == 2
        assert mock_fetch.call_count == 3  # only 3 pages fetched, not 5

    @patch("home_ops.scraper.lifecycle._get_fetcher")
    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_listings")
    def test_cold_start_logs_sponsored(
        self, mock_parse: MagicMock, mock_fetch: MagicMock, mock_get_fetcher: MagicMock
    ) -> None:
        """GIVEN parse returns listings WHEN cold_start THEN page progress logged."""
        import logging

        from home_ops.scraper.lifecycle import cold_start

        mock_fetch.return_value = "<html>mock</html>"
        mock_parse.return_value = [
            {"external_id": "1", "url": "/1", "address": "a",
             "price": None, "m2": None, "rooms": None, "floor": None,
             "description": "", "portal": "idealista",
             "price_includes_garage": False, "garage_price": None,
             "certificado_energetico_present": None},
        ]

        with patch.object(logging.getLogger("home_ops.scraper.lifecycle"), "info") as mock_log:
            cold_start("https://www.idealista.com/test", max_pages=1)
            # Should log page progress
            assert any("Page" in str(c) for c in mock_log.call_args_list)


# ---------------------------------------------------------------------------
# Shared test data for subsequent_run tests
# ---------------------------------------------------------------------------

_PAGE1_MIXED = [
    {"content_hash": "ab9dac0f73922ba2", "url": "https://ex.com/1",
     "address": "Addr 1", "m2": 100.0},
    {"content_hash": "0093fe2318355e2f", "url": "https://ex.com/2",
     "address": "Addr 2", "m2": 200.0},
    {"content_hash": "07e82d979e4fc0bf", "url": "https://ex.com/3",
     "address": "Addr 3", "m2": 300.0},
]
_PAGE2_ALL_KNOWN = [
    {"content_hash": "e554ef4ae1ba05bf", "url": "https://ex.com/4",
     "address": "Addr 4", "m2": 400.0},
    {"content_hash": "ef4fcdde01a91a8a", "url": "https://ex.com/5",
     "address": "Addr 5", "m2": 500.0},
]
_PAGE1_ALL_KNOWN = [
    {"content_hash": "ab9dac0f73922ba2", "url": "https://ex.com/1",
     "address": "Addr 1", "m2": 100.0},
    {"content_hash": "0093fe2318355e2f", "url": "https://ex.com/2",
     "address": "Addr 2", "m2": 200.0},
]
_KNOWN_SET = {
    "ab9dac0f73922ba2", "0093fe2318355e2f",
    "e554ef4ae1ba05bf", "ef4fcdde01a91a8a",
}

BASE_URL = "https://example.com/search"
PAGE2_URL = "https://example.com/search?pagina=2"
PAGE3_URL = "https://example.com/search?pagina=3"
HTML_P1 = "<html>page1</html>"
HTML_P2 = "<html>page2</html>"
HTML_P3 = "<html>page3</html>"


class TestSubsequentRun:
    """Subsequent run (incremental scrape) tests."""

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _fetch_map(
        responses: dict[str, str],
    ) -> Callable[[object, str], str]:
        """Build a side_effect for _fetch_page_text."""
        return lambda fetcher, url: responses[url]

    @staticmethod
    def _dup_for(known: set[str]) -> Callable[[object, list[str]], set[str]]:
        """Build a side_effect for batch_known_hashes."""
        return lambda conn, hashes: {h for h in hashes if h in known}

    # -- tests -------------------------------------------------------------

    @patch("home_ops.scraper.lifecycle._get_fetcher")
    @patch("home_ops.scraper.lifecycle._save_snapshot")
    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_listings")
    @patch("home_ops.scraper.lifecycle.batch_known_hashes")
    def test_empty_page_returns_empty(
        self,
        mock_batch: MagicMock,
        mock_parse: MagicMock,
        mock_fetch: MagicMock,
        mock_snap: MagicMock,
        mock_get_fetcher: MagicMock,
    ) -> None:
        """GIVEN empty page WHEN subsequent_run THEN returns empty list."""
        from home_ops.scraper.lifecycle import subsequent_run

        mock_fetch.side_effect = self._fetch_map({BASE_URL: HTML_P1})
        mock_parse.return_value = []
        result = subsequent_run(BASE_URL, MagicMock())
        assert result == []
        mock_snap.assert_called_once()

    @patch("home_ops.scraper.lifecycle._get_fetcher")
    @patch("home_ops.scraper.lifecycle._save_snapshot")
    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_listings")
    @patch("home_ops.scraper.lifecycle.batch_known_hashes")
    def test_all_known_page1_early_stop(
        self,
        mock_batch: MagicMock,
        mock_parse: MagicMock,
        mock_fetch: MagicMock,
        mock_snap: MagicMock,
        mock_get_fetcher: MagicMock,
    ) -> None:
        """GIVEN all known on page 1 WHEN subsequent_run THEN returns [] (early stop)."""
        from home_ops.scraper.lifecycle import subsequent_run

        mock_fetch.side_effect = self._fetch_map({BASE_URL: HTML_P1})
        mock_parse.return_value = _PAGE1_ALL_KNOWN
        mock_batch.side_effect = self._dup_for(_KNOWN_SET)

        result = subsequent_run(BASE_URL, MagicMock())
        assert result == []
        # Only page 1 fetched (page 2 should NOT be fetched)
        mock_fetch.assert_called_once()

    @patch("home_ops.scraper.lifecycle._get_fetcher")
    @patch("home_ops.scraper.lifecycle._save_snapshot")
    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_listings")
    @patch("home_ops.scraper.lifecycle.batch_known_hashes")
    def test_mixed_page1_returns_only_new(
        self,
        mock_batch: MagicMock,
        mock_parse: MagicMock,
        mock_fetch: MagicMock,
        mock_snap: MagicMock,
        mock_get_fetcher: MagicMock,
    ) -> None:
        """GIVEN mixed known/new on page 1 WHEN subsequent_run THEN returns only new."""
        from home_ops.scraper.lifecycle import subsequent_run

        mock_fetch.side_effect = self._fetch_map({BASE_URL: HTML_P1})
        mock_parse.return_value = _PAGE1_MIXED
        mock_batch.side_effect = self._dup_for(_KNOWN_SET)

        result = subsequent_run(BASE_URL, MagicMock(), max_pages=1)
        assert len(result) == 1
        assert result[0].content_hash == "07e82d979e4fc0bf"

    @patch("home_ops.scraper.lifecycle._get_fetcher")
    @patch("home_ops.scraper.lifecycle._save_snapshot")
    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_listings")
    @patch("home_ops.scraper.lifecycle.batch_known_hashes")
    def test_early_stop_on_page2(
        self,
        mock_batch: MagicMock,
        mock_parse: MagicMock,
        mock_fetch: MagicMock,
        mock_snap: MagicMock,
        mock_get_fetcher: MagicMock,
    ) -> None:
        """GIVEN new on page 1, all known page 2 WHEN subsequent_run THEN stops at 2."""
        from home_ops.scraper.lifecycle import subsequent_run

        mock_fetch.side_effect = self._fetch_map({
            BASE_URL: HTML_P1,
            PAGE2_URL: HTML_P2,
            PAGE3_URL: HTML_P3,
        })
        mock_parse.side_effect = lambda html: {
            HTML_P1: _PAGE1_MIXED,
            HTML_P2: _PAGE2_ALL_KNOWN,
        }.get(html, [])
        mock_batch.side_effect = self._dup_for(_KNOWN_SET)

        result = subsequent_run(BASE_URL, MagicMock())
        assert len(result) == 1
        assert result[0].content_hash == "07e82d979e4fc0bf"
        # Should have fetched page 1 and page 2, but NOT page 3
        assert mock_fetch.call_count == 2

    @patch("home_ops.scraper.lifecycle._get_fetcher")
    @patch("home_ops.scraper.lifecycle._save_snapshot")
    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_listings")
    @patch("home_ops.scraper.lifecycle.batch_known_hashes")
    def test_force_fetches_all_pages(
        self,
        mock_batch: MagicMock,
        mock_parse: MagicMock,
        mock_fetch: MagicMock,
        mock_snap: MagicMock,
        mock_get_fetcher: MagicMock,
    ) -> None:
        """GIVEN force=True and all known WHEN subsequent_run THEN fetches max_pages."""
        from home_ops.scraper.lifecycle import subsequent_run

        mock_fetch.side_effect = self._fetch_map({
            BASE_URL: HTML_P1,
            PAGE2_URL: HTML_P2,
            PAGE3_URL: HTML_P3,
        })
        mock_parse.return_value = _PAGE1_ALL_KNOWN
        mock_batch.side_effect = self._dup_for(_KNOWN_SET)

        result = subsequent_run(BASE_URL, MagicMock(), max_pages=3, force=True)
        assert result == []
        # All 3 pages fetched despite all known
        assert mock_fetch.call_count == 3

    @patch("home_ops.scraper.lifecycle._get_fetcher")
    @patch("home_ops.scraper.lifecycle._save_snapshot")
    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_listings")
    @patch("home_ops.scraper.lifecycle.batch_known_hashes")
    def test_fetch_failure_returns_partial(
        self,
        mock_batch: MagicMock,
        mock_parse: MagicMock,
        mock_fetch: MagicMock,
        mock_snap: MagicMock,
        mock_get_fetcher: MagicMock,
    ) -> None:
        """GIVEN page 2 fetch fails WHEN subsequent_run THEN returns page 1 listings."""
        from home_ops.scraper.lifecycle import subsequent_run

        mock_fetch.side_effect = self._fetch_map({BASE_URL: HTML_P1})
        mock_parse.return_value = _PAGE1_MIXED
        mock_batch.side_effect = self._dup_for(_KNOWN_SET)

        # Make page 2 URL fail, but only when it's called
        def _fail_on_page2(fetcher: object, url: str) -> str:
            if url != BASE_URL:
                raise RuntimeError(f"Network timeout on {url}")
            return HTML_P1
        mock_fetch.side_effect = _fail_on_page2
        # _parse_listings only gets called for successful fetches
        mock_parse.side_effect = [  # each call returns next item
            _PAGE1_MIXED,  # page 1
        ]

        result = subsequent_run(BASE_URL, MagicMock())
        assert len(result) == 1
        assert result[0].content_hash == "07e82d979e4fc0bf"

    @patch("home_ops.scraper.lifecycle._get_fetcher")
    @patch("home_ops.scraper.lifecycle._save_snapshot")
    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_listings")
    @patch("home_ops.scraper.lifecycle.batch_known_hashes")
    def test_snapshot_only_page1(
        self,
        mock_batch: MagicMock,
        mock_parse: MagicMock,
        mock_fetch: MagicMock,
        mock_snap: MagicMock,
        mock_get_fetcher: MagicMock,
    ) -> None:
        """GIVEN 2 pages WHEN subsequent_run THEN snapshot only written for page 1."""
        from home_ops.scraper.lifecycle import subsequent_run

        mock_fetch.side_effect = self._fetch_map({
            BASE_URL: HTML_P1,
            PAGE2_URL: HTML_P2,
        })
        mock_parse.side_effect = lambda html: {
            HTML_P1: _PAGE1_MIXED,
            HTML_P2: _PAGE2_ALL_KNOWN,
        }.get(html, [])
        mock_batch.side_effect = self._dup_for(_KNOWN_SET)

        subsequent_run(BASE_URL, MagicMock())
        # _save_snapshot called exactly once (page 1 only — page 2+ skip)
        mock_snap.assert_called_once()

    @patch("home_ops.scraper.lifecycle._get_fetcher")
    @patch("home_ops.scraper.lifecycle._save_snapshot")
    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_listings")
    def test_closed_db_connection_raises(
        self,
        mock_parse: MagicMock,
        mock_fetch: MagicMock,
        mock_snap: MagicMock,
        mock_get_fetcher: MagicMock,
    ) -> None:
        """GIVEN closed DuckDBConnection WHEN subsequent_run THEN DatabaseError."""
        from home_ops.scraper.lifecycle import subsequent_run

        mock_fetch.side_effect = self._fetch_map({BASE_URL: HTML_P1})
        mock_parse.return_value = [{"content_hash": "test_hash", "url": ""}]

        closed_db = DuckDBConnection(":memory:")  # not connected — no connect() call
        with pytest.raises(RuntimeError):
            subsequent_run(BASE_URL, closed_db)
