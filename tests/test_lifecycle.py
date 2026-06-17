"""Tests for the scraper lifecycle module.

NOTE: The ``cold_start`` function imports ``StealthyFetcher`` lazily via
``_get_fetcher``.  Tests patch ``home_ops.scraper.lifecycle._get_fetcher``
to avoid requiring ``curl_cffi`` at test time.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from home_ops.scraper.lifecycle import (
    SNAPSHOT_DIR,
    _ensure_snapshot_dir,
    _extract_portal,
    _snapshot_path,
    invalidate_snapshots,
)


class TestSnapshotPath:
    """Snapshot path resolution tests."""

    def test_default_portal(self) -> None:
        """GIVEN idealista url WHEN _extract_portal THEN returns idealista."""
        assert _extract_portal("https://www.idealista.com/") == "idealista"

    def test_unknown_portal(self) -> None:
        """GIVEN unknown url WHEN _extract_portal THEN returns unknown."""
        assert _extract_portal("https://example.com/") == "unknown"

    def test_snapshot_path_format(self) -> None:
        """GIVEN portal name WHEN _snapshot_path THEN format is correct."""
        path = _snapshot_path("idealista")
        assert path.name.startswith("idealista_")
        assert path.name.endswith(".snap")
        assert path.parent == SNAPSHOT_DIR


class TestSnapshotDir:
    """Snapshot directory management tests."""

    def test_ensure_dir_creates(self, tmp_path: Path) -> None:
        """GIVEN non-existent dir WHEN _ensure_snapshot_dir THEN dir created."""
        import home_ops.scraper.lifecycle as lifecycle_mod
        original = lifecycle_mod.SNAPSHOT_DIR
        lifecycle_mod.SNAPSHOT_DIR = tmp_path / "snapshots"
        try:
            _ensure_snapshot_dir()
            assert (tmp_path / "snapshots").exists()
        finally:
            lifecycle_mod.SNAPSHOT_DIR = original

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
