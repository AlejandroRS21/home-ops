"""DuckDB connection manager and data access layer."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import duckdb

from home_ops.models.schema import Listing

# In-memory databases are used for testing and do not support WAL mode
_IN_MEMORY = ":memory:"

# Default database path; override via HOME_OPS_DB_PATH env var
DEFAULT_DB_PATH = Path("data/home_ops.duckdb")


class DatabaseError(Exception):
    """Raised on DuckDB operation failures."""
    pass


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
            raise DatabaseError(f"Failed to connect to DuckDB at {self.db_path}: {exc}") from exc

    def close(self) -> None:
        """Close the connection if open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        """Get the underlying DuckDB connection, raising if not connected."""
        if self._conn is None:
            raise DatabaseError("Not connected. Call connect() first or use as context manager.")
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
                    certificado_energetico_present, rooms, description, portal
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ],
            )
            row = result.fetchone()
            return row[0] if row else None
        except Exception as exc:
            raise DatabaseError(f"Failed to insert listing: {exc}") from exc

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
            raise DatabaseError(f"Failed to get listing: {exc}") from exc

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
