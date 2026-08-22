"""Analytics layer over the Home-Ops DuckDB store.

Pure SQL aggregations (no new dependency — DuckDB ships ``quantile_cont``)
exposed as small typed functions. This is the "Big Data" surface of the
project: price distributions, per-m² economics, and a run time-series
computed straight from the persisted pipeline state.

ponytail: zone is derived from the portal_url slug at observation time and
stored on ``price_history`` rows (not a listings column); per-zone
percentiles unlock once scans record the zone consistently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from home_ops.models.data_storage import DuckDBConnection


def zone_from_portal_url(portal_url: str) -> str | None:
    """Extract the location slug from a portal search URL.

    e.g. ``https://www.idealista.com/venta-viviendas/chiclana-de-la-frontera-cadiz/``
    -> ``"chiclana-de-la-frontera-cadiz"``. Returns None when no location
    segment exists (transaction-type segments excluded).
    """
    for segment in portal_url.split("/"):
        if (
            "-" in segment
            and not segment.startswith("http")
            and segment not in ("venta-viviendas", "alquiler-viviendas")
        ):
            return segment
    return None


def price_history_stats(db: DuckDBConnection) -> dict[str, Any]:
    """Return summary stats over ALL recorded observations (append-only)."""
    row = db.conn.execute(
        """
        SELECT
            COUNT(*) AS n,
            COUNT(DISTINCT content_hash) AS listings,
            AVG(price) AS mean,
            quantile_cont(price, 0.50) AS p50
        FROM price_history
        WHERE price IS NOT NULL AND price > 0
        """
    ).fetchone()
    assert row is not None
    return {
        "observations": int(row[0]),
        "unique_listings": int(row[1]),
        "mean": row[2],
        "p50": row[3],
    }


def price_evolution_by_week(db: DuckDBConnection, zone: str | None = None) -> list[dict[str, Any]]:
    """Return weekly mean/p50 price-per-m² from the append-only history.

    The time-series that makes 'below zone median' scoring possible.
    Optional ``zone`` filters by slug (e.g. 'chiclana-de-la-frontera-cadiz').
    """
    where = "WHERE price > 0 AND m2 > 0"
    params: list[Any] = []
    if zone is not None:
        where += " AND zone = ?"
        params.append(zone)
    rows = db.conn.execute(
        f"""
        SELECT
            date_trunc('week', observed_at) AS week,
            COUNT(*) AS n,
            AVG(price / m2) AS mean_eur_m2,
            quantile_cont(price / m2, 0.50) AS p50_eur_m2
        FROM price_history
        {where}
        GROUP BY week
        ORDER BY week ASC
        """,
        params,
    ).fetchall()
    assert rows is not None
    return [
        {
            "week": str(r[0]),
            "n": int(r[1]),
            "mean_eur_m2": r[2],
            "p50_eur_m2": r[3],
        }
        for r in rows
    ]


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
