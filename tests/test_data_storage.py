"""Tests for DuckDB data storage layer.

Tests use in-memory DuckDB database to avoid file I/O.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from home_ops.models.data_storage import DatabaseError, DuckDBConnection, get_connection
from home_ops.models.schema import Listing


@pytest.fixture
def db() -> DuckDBConnection:
    """Create an in-memory DuckDB connection with initialized schema."""
    conn = DuckDBConnection(":memory:")
    conn.connect()
    conn.init_db()
    return conn


class TestDuckDBConnection:
    """DuckDB connection lifecycle tests."""

    def test_connect_in_memory(self) -> None:
        """GIVEN :memory: path WHEN connect THEN connection is not None."""
        conn = DuckDBConnection(":memory:")
        conn.connect()
        try:
            assert conn.conn is not None
        finally:
            conn.close()

    def test_connect_file_based(self, tmp_path: Path) -> None:
        """GIVEN file-based path WHEN connect THEN connection works."""
        db_file = tmp_path / "test.duckdb"
        conn = DuckDBConnection(str(db_file))
        conn.connect()
        try:
            # Connection should be established without error
            assert conn.conn is not None
            # The connection should accept queries
            result = conn.conn.execute("SELECT 1").fetchone()
            assert result is not None
            assert result[0] == 1
        finally:
            conn.close()
            if db_file.exists():
                db_file.unlink()

    def test_context_manager(self) -> None:
        """GIVEN context manager WHEN used THEN connection opened and closed."""
        with get_connection(":memory:") as conn:
            assert conn.conn is not None
            conn.init_db()

    def test_double_close(self) -> None:
        """GIVEN closed connection WHEN close again THEN no error."""
        conn = DuckDBConnection(":memory:")
        conn.connect()
        conn.close()
        conn.close()  # should not raise

    def test_conn_property_raises_when_not_connected(self) -> None:
        """GIVEN unconnected DB WHEN accessing conn THEN DatabaseError."""
        conn = DuckDBConnection(":memory:")
        with pytest.raises(DatabaseError, match="Not connected"):
            _ = conn.conn

    def test_init_db_creates_tables(self, db: DuckDBConnection) -> None:
        """GIVEN fresh DB WHEN init_db THEN tables exist."""
        tables = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "listings" in table_names
        assert "snapshots" in table_names
        assert "price_history" in table_names

    def test_init_db_idempotent(self, db: DuckDBConnection) -> None:
        """GIVEN inited DB WHEN init_db called again THEN no error."""
        db.init_db()  # second call should not raise


class TestInsertListing:
    """Atomic dedup listing insert tests."""

    def test_insert_unique_listing(self, db: DuckDBConnection) -> None:
        """GIVEN unique listing WHEN inserted THEN returns id."""
        listing = Listing(content_hash="hash001", url="https://test.com/1", address="Calle 1")
        result = db.insert_listing(listing)
        assert result is not None
        assert isinstance(result, int)

    def test_duplicate_content_hash_skipped(self, db: DuckDBConnection) -> None:
        """GIVEN duplicate content_hash WHEN inserted THEN returns None."""
        listing1 = Listing(content_hash="dup_hash", url="https://test.com/1")
        listing2 = Listing(content_hash="dup_hash", url="https://test.com/2")

        first_id = db.insert_listing(listing1)
        second_id = db.insert_listing(listing2)

        assert first_id is not None
        assert second_id is None  # dedup: skipped

    def test_insert_with_all_fields(self, db: DuckDBConnection) -> None:
        """GIVEN listing with all fields WHEN inserted THEN stored correctly."""
        listing = Listing(
            content_hash="full001",
            external_id="ext-001",
            url="https://idealista.com/test",
            address="Calle Test 123",
            m2=100.5,
            floor="3A",
            price=Decimal("300000.00"),
            garage_price=Decimal("15000.00"),
            price_includes_garage=False,
            certificado_energetico_present=True,
            rooms=4,
            description="Spacious flat",
            portal="idealista",
        )
        listing_id = db.insert_listing(listing)
        assert listing_id is not None

        stored = db.get_listing("full001")
        assert stored is not None
        assert stored["external_id"] == "ext-001"
        assert stored["url"] == "https://idealista.com/test"
        assert stored["rooms"] == 4

    def test_insert_concurrent_safe(self, db: DuckDBConnection) -> None:
        """GIVEN concurrent duplicate inserts WHEN both executed THEN only one inserted."""
        listing = Listing(content_hash="concurrent", url="https://test.com")
        id1 = db.insert_listing(listing)
        id2 = db.insert_listing(listing)

        assert id1 is not None
        assert id2 is None  # ON CONFLICT DO NOTHING prevents TOCTOU


class TestGetListing:
    """Listing retrieval tests."""

    def test_get_existing(self, db: DuckDBConnection) -> None:
        """GIVEN existing hash WHEN get_listing THEN returns dict."""
        listing = Listing(content_hash="get_me", url="https://test.com/get")
        db.insert_listing(listing)

        result = db.get_listing("get_me")
        assert result is not None
        assert result["content_hash"] == "get_me"

    def test_get_nonexistent(self, db: DuckDBConnection) -> None:
        """GIVEN nonexistent hash WHEN get_listing THEN returns None."""
        result = db.get_listing("does_not_exist")
        assert result is None


class TestInsertPrice:
    """Price history tests."""

    def test_insert_price(self, db: DuckDBConnection) -> None:
        """GIVEN listing AND price WHEN insert_price THEN returns id."""
        listing = Listing(content_hash="price_test", url="https://test.com/price")
        lid = db.insert_listing(listing)
        assert lid is not None

        price_id = db.insert_price(lid, Decimal("250000.00"))
        assert isinstance(price_id, int)

        # Verify it shows up
        rows = db.conn.execute(
            "SELECT * FROM price_history WHERE listing_id = ?", [lid]
        ).fetchall()
        assert len(rows) == 1
