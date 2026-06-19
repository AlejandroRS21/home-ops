"""Deduplication utilities: address normalisation and content hashing.

These helpers prevent duplicate listings from being inserted when the same
property appears in consecutive scraper runs.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from home_ops.models.data_storage import DuckDBConnection


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


def batch_known_hashes(
    db_connection: DuckDBConnection,
    hashes: list[str],
) -> set[str]:
    """Check which content hashes already exist — single batch query.

    Avoids the N+1 pattern of calling ``is_duplicate`` per hash.

    Args:
        db_connection: An open DuckDB connection with an initialised schema.
        hashes: List of content hashes to check.

    Returns:
        Set of hashes that already exist in the listings table.
    """
    if not hashes:
        return set()

    placeholders = ",".join("?" for _ in hashes)
    rows = db_connection.conn.execute(
        f"SELECT content_hash FROM listings WHERE content_hash IN ({placeholders})",
        hashes,
    ).fetchall()
    return {row[0] for row in rows}
