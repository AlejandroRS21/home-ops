"""Tests for the scraper lifecycle module.

NOTE: The ``cold_start`` function imports ``StealthyFetcher`` lazily via
``_get_fetcher``.  Tests patch ``home_ops.scraper.lifecycle._get_fetcher``
to avoid requiring ``curl_cffi`` at test time.
"""

import logging
import sys
from collections.abc import Callable
from decimal import Decimal
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
from home_ops.models.schema import Listing  # noqa: E402
from home_ops.scraper.lifecycle import (  # noqa: E402
    SNAPSHOT_DIR,
    invalidate_snapshots,
)


@pytest.fixture(autouse=True)
def _no_real_sleep() -> None:
    """Keep the enrichment delay out of unit tests.

    TestEnrichment tests patch ``time.sleep`` explicitly to assert the calls;
    this fixture only prevents real 1.5s sleeps in legacy cold_start /
    subsequent_run tests.
    """
    with patch("home_ops.scraper.lifecycle.time.sleep"):
        yield


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
        """GIVEN _fetch_page_text fails WHEN cold_start THEN re-raises exception."""
        from home_ops.scraper.lifecycle import cold_start

        mock_fetch.side_effect = RuntimeError("Fetch failed")
        with pytest.raises(RuntimeError, match="Fetch failed"):
            cold_start("https://example.com")

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
        # 3 pagination fetches (pages 1-3) + 2 enrichment detail fetches
        assert mock_fetch.call_count == 5

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
        # 3 pagination fetches (early stop) + 2 enrichment detail fetches
        assert mock_fetch.call_count == 5  # only 3 pages fetched, not 5

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
        # 2 pagination fetches (page 1 + page 2) + 1 enrichment detail fetch
        assert mock_fetch.call_count == 3

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
        """GIVEN page 2 fetch fails WHEN subsequent_run THEN raises (no false success)."""
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

        with pytest.raises(RuntimeError, match="Network timeout"):
            subsequent_run(BASE_URL, MagicMock())

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


class TestEnrichment:
    """Detail-page enrichment: sequential spacing, fetch cap, and degrade."""

    @staticmethod
    def _listing(
        hash_: str,
        url: str,
        *,
        garage: Decimal | None = None,
        cert: bool | None = None,
    ) -> Listing:
        """Build a Listing with the given detail fields (summary defaults)."""
        return Listing(
            content_hash=hash_,
            url=url,
            address=f"Addr {hash_}",
            garage_price=garage,
            certificado_energetico_present=cert,
        )

    @staticmethod
    def _parsed(garage: str = "15000", cert: bool = True) -> dict:
        """A non-empty parse_detail result."""
        return {
            "garage_price": Decimal(garage),
            "certificado_energetico_present": cert,
            "price_includes_garage_override": None,
        }

    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_detail")
    @patch("home_ops.scraper.lifecycle.time.sleep")
    def test_sequential_sleeps_before_each_fetch(
        self,
        mock_sleep: MagicMock,
        mock_parse_detail: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """GIVEN 3 new listings WHEN enriched THEN sleep 1.5 before each of 3 fetches in order."""
        from home_ops.scraper.lifecycle import DETAIL_DELAY_SECONDS, _enrich_new_listings

        listings = [
            self._listing("h1", "https://ex.com/1"),
            self._listing("h2", "https://ex.com/2"),
            self._listing("h3", "https://ex.com/3"),
        ]
        mock_parse_detail.return_value = self._parsed()
        sequence: list[tuple[str, object]] = []

        def _record_fetch(fetcher: object, url: str) -> str:
            sequence.append(("fetch", url))
            return "<html>detail</html>"

        def _record_sleep(seconds: float) -> None:
            sequence.append(("sleep", seconds))

        mock_fetch.side_effect = _record_fetch
        mock_sleep.side_effect = _record_sleep

        _enrich_new_listings(listings, MagicMock())

        # One sleep of DETAIL_DELAY_SECONDS per detail fetch, interleaved before each fetch
        assert mock_sleep.call_count == 3
        mock_sleep.assert_called_with(DETAIL_DELAY_SECONDS)
        assert sequence == [
            ("sleep", DETAIL_DELAY_SECONDS),
            ("fetch", "https://ex.com/1"),
            ("sleep", DETAIL_DELAY_SECONDS),
            ("fetch", "https://ex.com/2"),
            ("sleep", DETAIL_DELAY_SECONDS),
            ("fetch", "https://ex.com/3"),
        ]
        # Parsed fields applied to each listing
        assert listings[0].garage_price == Decimal("15000")
        assert listings[0].certificado_energetico_present is True
        assert listings[2].garage_price == Decimal("15000")

    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_detail")
    @patch("home_ops.scraper.lifecycle.time.sleep")
    def test_fetch_cap_limits_detail_fetches(
        self,
        mock_sleep: MagicMock,
        mock_parse_detail: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """GIVEN 12 listings WHEN enriched THEN exactly DETAIL_FETCH_CAP fetches; 2 keep None."""
        from home_ops.scraper.lifecycle import DETAIL_FETCH_CAP, _enrich_new_listings

        listings = [self._listing(f"h{i}", f"https://ex.com/{i}") for i in range(12)]
        mock_fetch.return_value = "<html>detail</html>"
        mock_parse_detail.return_value = self._parsed()

        _enrich_new_listings(listings, MagicMock())

        assert DETAIL_FETCH_CAP == 10
        assert mock_fetch.call_count == 10
        assert mock_sleep.call_count == 10
        # First 10 enriched, last 2 keep summary (None)
        assert listings[0].garage_price == Decimal("15000")
        assert listings[9].garage_price == Decimal("15000")
        assert listings[10].garage_price is None
        assert listings[11].garage_price is None
        assert listings[11].certificado_energetico_present is None

    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_detail")
    @patch("home_ops.scraper.lifecycle.time.sleep")
    def test_fetch_failure_degrades_keeping_listing(
        self,
        mock_sleep: MagicMock,
        mock_parse_detail: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """GIVEN a detail fetch raises WHEN enriched THEN warning logged, listing kept."""
        from home_ops.scraper.lifecycle import _enrich_new_listings

        listings = [
            self._listing("h1", "https://ex.com/1"),
            self._listing("h2", "https://ex.com/2"),
            self._listing("h3", "https://ex.com/3"),
        ]

        def _fail_second(fetcher: object, url: str) -> str:
            if url == "https://ex.com/2":
                raise RuntimeError("network down")
            return "<html>detail</html>"

        mock_fetch.side_effect = _fail_second
        mock_parse_detail.return_value = self._parsed("5000", cert=False)
        logger = logging.getLogger("home_ops.scraper.lifecycle")
        with patch.object(logger, "warning") as mock_warning:
            _enrich_new_listings(listings, MagicMock())

        # Failed listing kept with summary values; run continues
        assert len(listings) == 3
        assert listings[1].garage_price is None
        mock_warning.assert_called_once()
        assert mock_warning.call_args.args[0] == (
            "Detail fetch failed for %s: %s — keeping summary data"
        )
        assert mock_warning.call_args.args[1] == "https://ex.com/2"
        assert isinstance(mock_warning.call_args.args[2], RuntimeError)
        # Siblings still enriched
        assert listings[0].garage_price == Decimal("5000")
        assert listings[2].garage_price == Decimal("5000")

    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_detail")
    @patch("home_ops.scraper.lifecycle.time.sleep")
    def test_parse_empty_keeps_existing_summary_values(
        self,
        mock_sleep: MagicMock,
        mock_parse_detail: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """GIVEN parse_detail all None WHEN enriched THEN existing non-None fields preserved."""
        from home_ops.scraper.lifecycle import _enrich_new_listings

        listings = [
            self._listing("h1", "https://ex.com/1", garage=Decimal("12000"), cert=True),
            self._listing("h2", "https://ex.com/2"),
        ]
        mock_fetch.return_value = "<html>detail</html>"
        mock_parse_detail.return_value = {
            "garage_price": None,
            "certificado_energetico_present": None,
            "price_includes_garage_override": None,
        }

        _enrich_new_listings(listings, MagicMock())

        # Never overwrite existing non-None values with None
        assert listings[0].garage_price == Decimal("12000")
        assert listings[0].certificado_energetico_present is True
        # Listing with no prior fields stays None; both listings kept
        assert listings[1].garage_price is None
        assert len(listings) == 2

    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    @patch("home_ops.scraper.lifecycle.parse_detail")
    @patch("home_ops.scraper.lifecycle.time.sleep")
    def test_empty_url_is_skipped(
        self,
        mock_sleep: MagicMock,
        mock_parse_detail: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """GIVEN a listing with empty url WHEN enriched THEN no fetch or sleep for it."""
        from home_ops.scraper.lifecycle import _enrich_new_listings

        listings = [
            self._listing("h1", ""),
            self._listing("h2", "https://ex.com/2"),
        ]
        mock_fetch.return_value = "<html>detail</html>"
        mock_parse_detail.return_value = self._parsed("3000")

        _enrich_new_listings(listings, MagicMock())

        mock_fetch.assert_called_once()
        assert mock_fetch.call_args.args[1] == "https://ex.com/2"
        assert mock_sleep.call_count == 1
        # Skipped listing keeps summary values
        assert listings[0].garage_price is None
        assert listings[1].garage_price == Decimal("3000")
