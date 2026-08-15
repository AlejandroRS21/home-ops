"""Tests for DuckDB data storage layer.

Tests use in-memory DuckDB database to avoid file I/O.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from home_ops.models.data_storage import DuckDBConnection, get_connection
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
        with pytest.raises(RuntimeError, match="Not connected"):
            _ = conn.conn

    def test_init_db_creates_tables(self, db: DuckDBConnection) -> None:
        """GIVEN fresh DB WHEN init_db THEN tables exist."""
        tables = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "listings" in table_names
        assert "pending_approvals" in table_names
        assert "scraping_runs" in table_names
        assert "daily_alert_log" in table_names

    def test_init_db_idempotent(self, db: DuckDBConnection) -> None:
        """GIVEN inited DB WHEN init_db called again THEN no error."""
        db.init_db()  # second call should not raise


class TestScamColumnsMigration:
    """Scenario 5.1 — scam columns added idempotently and round-trip."""

    @staticmethod
    def _listings_columns(db: DuckDBConnection) -> set[str]:
        rows = db.conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'listings'"
        ).fetchall()
        return {r[0] for r in rows}

    def test_init_db_adds_scam_columns(self, db: DuckDBConnection) -> None:
        """GIVEN fresh init_db WHEN inspected THEN scam columns exist."""
        columns = self._listings_columns(db)
        assert "scam_flags" in columns
        assert "scam_risk_score" in columns
        assert "total_acquisition_cost" in columns

    def test_init_db_scam_columns_idempotent(self, db: DuckDBConnection) -> None:
        """GIVEN re-run init_db WHEN inspected THEN columns still exist, no error."""
        db.init_db()
        db.init_db()
        columns = self._listings_columns(db)
        assert "scam_flags" in columns
        assert "scam_risk_score" in columns
        assert "total_acquisition_cost" in columns

    def test_init_db_migrates_legacy_listings_table(self) -> None:
        """GIVEN pre-existing table without scam columns WHEN init_db THEN columns added."""
        conn = DuckDBConnection(":memory:")
        conn.connect()
        # Simulate a legacy DB whose listings table predates buyer protection
        conn.conn.execute(
            "CREATE TABLE listings (id BIGINT PRIMARY KEY, content_hash TEXT"
            " UNIQUE NOT NULL, price DECIMAL(10,2));"
        )
        conn.init_db()
        try:
            rows = conn.conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'listings'"
            ).fetchall()
            columns = {r[0] for r in rows}
            assert "scam_flags" in columns
            assert "scam_risk_score" in columns
            assert "total_acquisition_cost" in columns
        finally:
            conn.close()

    def test_insert_listing_preserves_scam_fields(self, db: DuckDBConnection) -> None:
        """GIVEN listing with scam fields WHEN inserted THEN values round-trip."""
        listing = Listing(
            content_hash="scam_store_001",
            price=Decimal("150000.00"),
            scam_flags=["SCAM_RED_FLAG_TEXT", "SCAM_SUSPECT_PRICE_BAIT"],
            scam_risk_score=70.0,
            total_acquisition_cost=Decimal("164250.00"),
        )
        listing_id = db.insert_listing(listing)
        assert listing_id is not None

        stored = db.get_listing("scam_store_001")
        assert stored is not None
        assert stored["scam_flags"] == ["SCAM_RED_FLAG_TEXT", "SCAM_SUSPECT_PRICE_BAIT"]
        assert stored["scam_risk_score"] == 70.0
        assert stored["total_acquisition_cost"] == Decimal("164250.00")

    def test_insert_listing_default_scam_fields(self, db: DuckDBConnection) -> None:
        """GIVEN plain listing WHEN inserted THEN scam columns default."""
        listing = Listing(content_hash="scam_default_001", price=Decimal("200000.00"))
        assert db.insert_listing(listing) is not None

        stored = db.get_listing("scam_default_001")
        assert stored is not None
        assert stored["scam_flags"] == []
        assert stored["scam_risk_score"] == 0.0
        assert stored["total_acquisition_cost"] is None


class TestNewTables:
    """Tests for scraping_runs and daily_alert_log tables."""

    def test_scraping_runs_insert_and_query(self, db: DuckDBConnection) -> None:
        """GIVEN scraping_runs table WHEN inserting row THEN round-trips."""
        db.conn.execute(
            """INSERT INTO scraping_runs (started_at, finished_at, listings_found, listings_new, alerts_sent, status)
               VALUES ('2026-01-01 10:00:00', '2026-01-01 10:05:00', 10, 3, 2, 'success')"""
        )
        rows = db.conn.execute("SELECT * FROM scraping_runs").fetchall()
        assert len(rows) == 1
        assert rows[0][3] == 10  # listings_found
        assert rows[0][4] == 3   # listings_new
        assert rows[0][5] == 2   # alerts_sent
        assert rows[0][6] == "success"

    def test_scraping_runs_auto_increment_id(self, db: DuckDBConnection) -> None:
        """GIVEN scraping_runs WHEN inserting multiple rows THEN id auto-increments."""
        db.conn.execute(
            """INSERT INTO scraping_runs (started_at, finished_at, status)
               VALUES ('2026-01-01 10:00:00', '2026-01-01 10:05:00', 'success')"""
        )
        db.conn.execute(
            """INSERT INTO scraping_runs (started_at, finished_at, status)
               VALUES ('2026-01-02 10:00:00', '2026-01-02 10:05:00', 'failed')"""
        )
        rows = db.conn.execute("SELECT id, status FROM scraping_runs ORDER BY id").fetchall()
        assert len(rows) == 2
        assert rows[0][0] == 1
        assert rows[1][0] == 2

    def test_daily_alert_log_insert_and_query(self, db: DuckDBConnection) -> None:
        """GIVEN daily_alert_log table WHEN inserting THEN sent_at defaults."""
        db.conn.execute(
            "INSERT INTO daily_alert_log (listing_hash, status) VALUES ('hash001', 'sent')"
        )
        row = db.conn.execute(
            "SELECT listing_hash, status FROM daily_alert_log WHERE listing_hash = 'hash001'"
        ).fetchone()
        assert row is not None
        assert row[0] == "hash001"
        assert row[1] == "sent"

    def test_daily_alert_log_sent_at_defaults_now(self, db: DuckDBConnection) -> None:
        """GIVEN daily_alert_log insert WHEN sent_at omitted THEN defaults to timestamp."""
        db.conn.execute(
            "INSERT INTO daily_alert_log (listing_hash, status) VALUES ('hash002', 'sent')"
        )
        row = db.conn.execute(
            "SELECT sent_at FROM daily_alert_log WHERE listing_hash = 'hash002'"
        ).fetchone()
        assert row is not None
        assert row[0] is not None  # sent_at should be a timestamp

    def test_daily_alert_log_count_for_today(self, db: DuckDBConnection) -> None:
        """GIVEN daily_alert_log entries TODAY WHEN counting THEN returns correct count."""
        db.conn.execute(
            "INSERT INTO daily_alert_log (listing_hash, status) VALUES ('h1', 'sent')"
        )
        db.conn.execute(
            "INSERT INTO daily_alert_log (listing_hash, status) VALUES ('h2', 'sent')"
        )
        db.conn.execute(
            "INSERT INTO daily_alert_log (listing_hash, status) VALUES ('h3', 'queued')"
        )
        count = db.conn.execute(
            "SELECT COUNT(*) FROM daily_alert_log WHERE status = 'sent'"
        ).fetchone()
        assert count is not None
        assert count[0] == 2


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


class TestUpdateListingScamFields:
    """Scenario 6.1 — post-score scam-field persistence via UPDATE."""

    def test_update_persists_scam_fields(self, db: DuckDBConnection) -> None:
        """GIVEN inserted listing WHEN scam fields updated THEN values round-trip."""
        listing = Listing(content_hash="scam_update_001", price=Decimal("150000.00"))
        assert db.insert_listing(listing) is not None

        db.update_listing_scam_fields(
            "scam_update_001",
            ["SCAM_RED_FLAG_TEXT", "MISSING_ENERGY_CERT"],
            50.0,
            Decimal("164250.00"),
        )

        stored = db.get_listing("scam_update_001")
        assert stored is not None
        assert stored["scam_flags"] == ["SCAM_RED_FLAG_TEXT", "MISSING_ENERGY_CERT"]
        assert stored["scam_risk_score"] == 50.0
        assert stored["total_acquisition_cost"] == Decimal("164250.00")

    def test_update_clears_cost_and_flags(self, db: DuckDBConnection) -> None:
        """GIVEN row with prior scam values WHEN updated to neutral THEN fields clear."""
        listing = Listing(
            content_hash="scam_update_002",
            price=Decimal("150000.00"),
            scam_flags=["SCAM_RED_FLAG_TEXT"],
            scam_risk_score=40.0,
            total_acquisition_cost=Decimal("164250.00"),
        )
        assert db.insert_listing(listing) is not None

        db.update_listing_scam_fields("scam_update_002", [], 0.0, None)

        stored = db.get_listing("scam_update_002")
        assert stored is not None
        assert stored["scam_flags"] == []
        assert stored["scam_risk_score"] == 0.0
        assert stored["total_acquisition_cost"] is None



