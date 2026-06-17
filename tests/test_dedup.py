"""Tests for the dedup module."""

from __future__ import annotations

from typing import TYPE_CHECKING

from home_ops.scraper.dedup import compute_content_hash, is_duplicate, normalize_address

if TYPE_CHECKING:
    from home_ops.models.data_storage import DuckDBConnection


class TestNormalizeAddress:
    """Address normalisation tests."""

    def test_strip_whitespace(self) -> None:
        """GIVEN leading/trailing whitespace WHEN normalize THEN stripped."""
        assert normalize_address("  Calle Mayor  ") == "calle mayor"

    def test_lowercase(self) -> None:
        """GIVEN mixed case WHEN normalize THEN lowercased."""
        assert normalize_address("Calle Mayor, 12") == "calle mayor, 12"

    def test_collapse_spaces(self) -> None:
        """GIVEN multiple spaces WHEN normalize THEN collapsed."""
        assert normalize_address("Calle   Mayor   12") == "calle mayor 12"

    def test_empty_string(self) -> None:
        """GIVEN empty string WHEN normalize THEN empty."""
        assert normalize_address("") == ""

    def test_special_chars(self) -> None:
        """GIVEN accented chars WHEN normalize THEN preserved."""
        assert normalize_address("C/ Álvaro de Bazán") == "c/ álvaro de bazán"


class TestComputeContentHash:
    """Content hash computation tests."""

    def test_known_hash(self) -> None:
        """GIVEN known inputs WHEN compute THEN returns deterministic hash."""
        h1 = compute_content_hash("idealista", "centro", 85.0, "planta 4ª")
        h2 = compute_content_hash("idealista", "centro", 85.0, "planta 4ª")
        assert h1 == h2
        assert len(h1) == 16  # truncated SHA-256 hex digest

    def test_different_portal_differs(self) -> None:
        """GIVEN different portals WHEN compute THEN hashes differ."""
        h1 = compute_content_hash("idealista", "centro", 80.0, "2")
        h2 = compute_content_hash("fotocasa", "centro", 80.0, "2")
        assert h1 != h2

    def test_different_zone_differs(self) -> None:
        """GIVEN different zones WHEN compute THEN hashes differ."""
        h1 = compute_content_hash("idealista", "centro", 80.0, "2")
        h2 = compute_content_hash("idealista", "norte", 80.0, "2")
        assert h1 != h2

    def test_different_m2_differs(self) -> None:
        """GIVEN different m2 WHEN compute THEN hashes differ."""
        h1 = compute_content_hash("idealista", "centro", 80.0, "2")
        h2 = compute_content_hash("idealista", "centro", 90.0, "2")
        assert h1 != h2

    def test_different_floor_differs(self) -> None:
        """GIVEN different floor WHEN compute THEN hashes differ."""
        h1 = compute_content_hash("idealista", "centro", 80.0, "1A")
        h2 = compute_content_hash("idealista", "centro", 80.0, "2B")
        assert h1 != h2

    def test_none_values(self) -> None:
        """GIVEN None for m2 or floor WHEN compute THEN handles gracefully."""
        h = compute_content_hash("idealista", "test", None, None)
        assert len(h) == 16


class TestIsDuplicate:
    """Duplicate detection tests."""

    def test_duplicate_found(self, db: DuckDBConnection) -> None:
        """GIVEN existing hash WHEN is_duplicate THEN True."""
        db.conn.execute(
            "INSERT INTO listings (content_hash) VALUES (?)",
            ["existing_hash"],
        )
        assert is_duplicate("existing_hash", db) is True

    def test_duplicate_not_found(self, db: DuckDBConnection) -> None:
        """GIVEN non-existing hash WHEN is_duplicate THEN False."""
        assert is_duplicate("nonexistent", db) is False

    def test_empty_hash(self, db: DuckDBConnection) -> None:
        """GIVEN empty hash WHEN is_duplicate THEN False."""
        assert is_duplicate("", db) is False
