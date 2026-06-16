"""Tests for the scraper lifecycle module."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Mock scrapling at sys.modules level BEFORE importing lifecycle module.
# The installed scrapling package requires curl_cffi which is not installed,
# so we prevent the real import chain from running.
_mock_scrapling = MagicMock()
_mock_stealthy_fetcher = MagicMock()
_mock_async_session = MagicMock()
_mock_scrapling.StealthyFetcher = lambda: _mock_stealthy_fetcher
_mock_scrapling.AsyncStealthySession = lambda adaptive=None, auto_save=None: _mock_async_session
sys.modules["scrapling"] = _mock_scrapling

from home_ops.scraper.lifecycle import (  # noqa: E402
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

    @patch("home_ops.scraper.lifecycle._fetch_page_text")
    def test_cold_start_raises_on_fetch_failure(
        self, mock_fetch: MagicMock
    ) -> None:
        """GIVEN _fetch_page_text fails WHEN cold_start THEN RuntimeError."""
        from home_ops.scraper.lifecycle import cold_start

        mock_fetch.side_effect = RuntimeError("Fetch failed")
        with pytest.raises(RuntimeError, match="Fetch failed"):
            cold_start("https://example.com")



