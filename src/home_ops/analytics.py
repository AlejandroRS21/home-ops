"""Analytics layer over the Home-Ops DuckDB store.

Pure SQL aggregations (no new dependency — DuckDB ships ``quantile_cont``)
exposed as small typed functions. This is the "Big Data" surface of the
project: price distributions, per-m² economics, and a run time-series
computed straight from the persisted pipeline state.

ponytail: zone-level aggregates are out of scope until ``zone`` becomes a
first-class ``listings`` column (it currently lives only inside
``content_hash``); add a ``zone`` column and pass it through
``_dicts_to_listings`` to unlock per-neighbourhood percentiles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from home_ops.models.data_storage import DuckDBConnection


def price_stats(db: DuckDBConnection) -> dict[str, Any]:
    """Return summary statistics for the listing price distribution."""
    row = db.conn.execute(
        """
        SELECT
            COUNT(*) AS n,
            AVG(price) AS mean,
            MIN(price) AS min,
            MAX(price) AS max,
            quantile_cont(price, 0.25) AS p25,
            quantile_cont(price, 0.50) AS p50,
            quantile_cont(price, 0.75) AS p75
        FROM listings
        WHERE price IS NOT NULL AND price > 0
        """
    ).fetchone()
    assert row is not None
    return {
        "count": row[0],
        "mean": row[1],
        "min": row[2],
        "max": row[3],
        "p25": row[4],
        "p50": row[5],
        "p75": row[6],
    }


def price_per_m2_stats(db: DuckDBConnection) -> dict[str, Any]:
    """Return summary statistics for the price-per-m² distribution."""
    row = db.conn.execute(
        """
        SELECT
            COUNT(*) AS n,
            AVG(price / m2) AS mean,
            MIN(price / m2) AS min,
            MAX(price / m2) AS max,
            quantile_cont(price / m2, 0.25) AS p25,
            quantile_cont(price / m2, 0.50) AS p50,
            quantile_cont(price / m2, 0.75) AS p75
        FROM listings
        WHERE price IS NOT NULL AND price > 0 AND m2 IS NOT NULL AND m2 > 0
        """
    ).fetchone()
    assert row is not None
    return {
        "count": row[0],
        "mean": row[1],
        "min": row[2],
        "max": row[3],
        "p25": row[4],
        "p50": row[5],
        "p75": row[6],
    }


def portal_counts(db: DuckDBConnection) -> list[tuple[str, int]]:
    """Return listing counts grouped by portal."""
    rows = db.conn.execute(
        "SELECT portal, COUNT(*) AS n FROM listings GROUP BY portal ORDER BY n DESC"
    ).fetchall()
    return [(str(r[0]), int(r[1])) for r in rows]


def runs_timeseries(db: DuckDBConnection) -> list[dict[str, Any]]:
    """Return a per-day time-series of pipeline activity.

    One row per calendar day with listings found, new listings, and alerts
    sent — the persisted ``scraping_runs`` table IS the run history.
    """
    rows = db.conn.execute(
        """
        SELECT
            CAST(finished_at AS DATE) AS day,
            SUM(listings_found) AS found,
            SUM(listings_new) AS new,
            SUM(alerts_sent) AS alerts
        FROM scraping_runs
        WHERE finished_at IS NOT NULL
        GROUP BY day
        ORDER BY day ASC
        """
    ).fetchall()
    return [
        {
            "day": str(r[0]),
            "listings_found": int(r[1] or 0),
            "listings_new": int(r[2] or 0),
            "alerts_sent": int(r[3] or 0),
        }
        for r in rows
    ]
