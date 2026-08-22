"""DuckDB connection manager and data access layer."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager, suppress
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from home_ops.models.schema import Listing

# In-memory databases are used for testing and do not support WAL mode
_IN_MEMORY = ":memory:"

# Default database path; override via HOME_OPS_DB_PATH env var
DEFAULT_DB_PATH = Path("data/home_ops.duckdb")


class DuckDBConnection:
    """DuckDB connection wrapper with schema init and atomic operations."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path) if db_path else str(DEFAULT_DB_PATH)
        self._conn: duckdb.DuckDBPyConnection | None = None

    def __enter__(self) -> DuckDBConnection:
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def connect(self) -> None:
        """Open (or create) the DuckDB database and attempt WAL mode.

        WAL mode is only supported for file-based databases and DuckDB
        v0.10+.  Older versions or incompatible builds silently skip it.
        In-memory databases (:memory:) skip WAL pragma entirely.
        """
        try:
            self._conn = duckdb.connect(self.db_path)
            if self.db_path != _IN_MEMORY:
                with suppress(Exception):
                    self._conn.execute("PRAGMA enable_wal;")
        except Exception as exc:
            raise RuntimeError(f"Failed to connect to DuckDB at {self.db_path}: {exc}") from exc

    def close(self) -> None:
        """Close the connection if open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        """Get the underlying DuckDB connection, raising if not connected."""
        if self._conn is None:
            raise RuntimeError("Not connected. Call connect() first or use as context manager.")
        return self._conn

    def init_db(self) -> None:
        """Create tables if they do not exist."""
        self.conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_listings_id START 1;")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER DEFAULT nextval('seq_listings_id') PRIMARY KEY,
                content_hash TEXT UNIQUE NOT NULL,
                external_id TEXT,
                url TEXT,
                address TEXT,
                m2 DOUBLE,
                floor TEXT,
                price DECIMAL(10,2),
                garage_price DECIMAL(10,2),
                price_includes_garage BOOLEAN DEFAULT false,
                certificado_energetico_present BOOLEAN,
                rooms INTEGER,
                description TEXT,
                portal TEXT DEFAULT 'idealista',
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Buyer-protection columns (idempotent for existing databases)
        self.conn.execute(
            "ALTER TABLE listings ADD COLUMN IF NOT EXISTS "
            "scam_flags VARCHAR[] DEFAULT [];"
        )
        self.conn.execute(
            "ALTER TABLE listings ADD COLUMN IF NOT EXISTS "
            "scam_risk_score DOUBLE DEFAULT 0;"
        )
        self.conn.execute(
            "ALTER TABLE listings ADD COLUMN IF NOT EXISTS "
            "total_acquisition_cost DECIMAL(10,2);"
        )
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_approvals (
                listing_id INTEGER PRIMARY KEY,
                approved BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_at TIMESTAMP
            );
        """)
        self.conn.execute(
            "ALTER TABLE pending_approvals ADD COLUMN IF NOT EXISTS score DOUBLE;"
        )
        self.conn.execute(
            "ALTER TABLE pending_approvals ADD COLUMN IF NOT EXISTS alerted BOOLEAN DEFAULT FALSE;"
        )
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS euribor_rate (
                rate DOUBLE NOT NULL,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS seq_scraping_runs_id START 1;
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS scraping_runs (
                id INTEGER DEFAULT nextval('seq_scraping_runs_id') PRIMARY KEY,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                listings_found INTEGER DEFAULT 0,
                listings_new INTEGER DEFAULT 0,
                alerts_sent INTEGER DEFAULT 0,
                status TEXT
            );
        """)
        self.conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS seq_daily_alert_log_id START 1;
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_alert_log (
                id INTEGER DEFAULT nextval('seq_daily_alert_log_id') PRIMARY KEY,
                listing_hash TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT
            );
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS catastro_data (
                listing_id BIGINT PRIMARY KEY,
                superficie_catastro DOUBLE,
                uso TEXT,
                antiguedad INTEGER,
                rc TEXT,
                status TEXT NOT NULL,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_analysis (
                listing_id BIGINT PRIMARY KEY,
                estado_reforma TEXT,
                orientacion TEXT,
                ruido_zona TEXT,
                red_flags_llm VARCHAR[] DEFAULT [],
                model_used TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                raw_response TEXT,
                analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Append-only price observations: one row per listing seen per scan,
        # capturing price changes over time (listings itself is deduped).
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                content_hash TEXT NOT NULL,
                zone TEXT,
                price DECIMAL(10,2),
                m2 DOUBLE,
                observed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

    def record_price_observation(
        self,
        content_hash: str,
        zone: str | None,
        price: Decimal | None,
        m2: float | None,
    ) -> None:
        """Append a price observation to the append-only history.

        Called for every listing seen in a scan — new or duplicate — so
        price evolution of existing ads is captured over time.
        """
        try:
            self.conn.execute(
                "INSERT INTO price_history (content_hash, zone, price, m2) VALUES (?, ?, ?, ?)",
                [content_hash, zone, price, m2],
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to record price observation: {exc}") from exc

    def insert_listing(self, listing: Listing) -> int | None:
        """Insert a listing with atomic dedup via content_hash.

        Returns the row id if inserted, None if skipped (duplicate).
        """
        try:
            result = self.conn.execute(
                """
                INSERT INTO listings (
                    content_hash, external_id, url, address, m2, floor,
                    price, garage_price, price_includes_garage,
                    certificado_energetico_present, rooms, description, portal,
                    scam_flags, scam_risk_score, total_acquisition_cost
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (content_hash) DO NOTHING
                RETURNING id;
                """,
                [
                    listing.content_hash,
                    listing.external_id,
                    listing.url,
                    listing.address,
                    listing.m2,
                    listing.floor,
                    listing.price,
                    listing.garage_price,
                    listing.price_includes_garage,
                    listing.certificado_energetico_present,
                    listing.rooms,
                    listing.description,
                    listing.portal,
                    listing.scam_flags,
                    listing.scam_risk_score,
                    listing.total_acquisition_cost,
                ],
            )
            row = result.fetchone()
            return row[0] if row else None
        except Exception as exc:
            raise RuntimeError(f"Failed to insert listing: {exc}") from exc

    def update_listing_scam_fields(
        self,
        content_hash: str,
        scam_flags: list[str],
        scam_risk_score: float,
        total_acquisition_cost: Decimal | None,
    ) -> None:
        """Persist post-score scam-risk fields on an existing listing row.

        Called after scoring so the buyer-protection output is recorded
        even when the alert is gated. Keyed by content_hash to preserve
        the ON CONFLICT dedup semantics of insert_listing.
        """
        try:
            self.conn.execute(
                "UPDATE listings SET scam_flags = ?, scam_risk_score = ?, "
                "total_acquisition_cost = ? WHERE content_hash = ?",
                [
                    scam_flags,
                    scam_risk_score,
                    total_acquisition_cost,
                    content_hash,
                ],
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to update scam fields for listing {content_hash}: {exc}"
            ) from exc

    def get_listing(self, content_hash: str) -> dict[str, Any] | None:
        """Retrieve a listing by its content hash."""
        try:
            result = self.conn.execute(
                "SELECT * FROM listings WHERE content_hash = ?",
                [content_hash],
            )
            row = result.fetchone()
            if row is None:
                return None
            cols = [desc[0] for desc in result.description]
            return dict(zip(cols, row, strict=True))
        except Exception as exc:
            raise RuntimeError(f"Failed to get listing: {exc}") from exc

@contextmanager
def get_connection(db_path: str | Path | None = None) -> Generator[DuckDBConnection, None, None]:
    """Context manager for temporary DuckDB connections.

    Example:
        with get_connection(":memory:") as db:
            db.init_db()
            db.insert_listing(listing)
    """
    conn = DuckDBConnection(db_path)
    try:
        conn.connect()
        yield conn
    finally:
        conn.close()
