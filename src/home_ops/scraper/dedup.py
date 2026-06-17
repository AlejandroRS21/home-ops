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


def compute_content_hash(portal: str, zone: str, m2: float | None, floor: str | None) -> str:
    """Compute a SHA-256 content hash for deduplication.

    The hash is built from the portal name, zone (neighbourhood or area),
    surface area in m², and floor identifier.  This combination is stable
    enough to catch repeat listings of the same property while tolerating
    minor description or price changes.

    Args:
        portal: Portal name (e.g. "idealista").
        zone: Neighbourhood or area string.
        m2: Surface area in square metres (may be None).
        floor: Floor identifier (e.g. "planta 4ª", may be None).

    Returns:
        Truncated hexadecimal SHA-256 digest (16 characters).
    """
    raw = "|".join([
        portal, zone,
        str(m2) if m2 is not None else "",
        floor if floor is not None else "",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


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
