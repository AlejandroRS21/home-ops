"""Deduplication utilities: address normalisation and content hashing.

These helpers prevent duplicate listings from being inserted when the same
property appears in consecutive scraper runs.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from home_ops.models.data_storage import DuckDBConnection


def normalize_address(addr: str) -> str:
    """Normalise a street address for stable hashing.

    Steps:
    - Strip leading / trailing whitespace
    - Lowercase
    - Collapse multiple consecutive spaces into one
    - Remove leading/trailing spaces after collapse

    Args:
        addr: Raw address string (e.g. "  Calle Mayor, 12   ").

    Returns:
        Normalised string (e.g. "calle mayor, 12").
    """
    return re.sub(r" {2,}", " ", addr.strip().lower()).strip()


def compute_content_hash(address: str, m2: float | None, floor: str | None) -> str:
    """Compute a SHA-256 content hash for deduplication.

    The hash is built from the normalised address, the surface area in m²,
    and the floor identifier.  This combination is stable enough to catch
    repeat listings of the same property while tolerating minor description
    or price changes.

    Args:
        address: Property street address.
        m2: Surface area in square metres (may be None).
        floor: Floor identifier (e.g. "3B", may be None).

    Returns:
        Hexadecimal SHA-256 digest (64 characters).
    """
    norm_addr = normalize_address(address)
    raw = f"{norm_addr}|{m2 or ''}|{floor or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_duplicate(content_hash: str, db_connection: DuckDBConnection) -> bool:
    """Check whether a content hash already exists in the listings table.

    Args:
        content_hash: The SHA-256 digest to look up.
        db_connection: An open DuckDB connection with an initialised schema.

    Returns:
        True if the hash exists, False otherwise.
    """
    row = db_connection.conn.execute(
        "SELECT 1 FROM listings WHERE content_hash = ? LIMIT 1",
        [content_hash],
    ).fetchone()
    return row is not None
