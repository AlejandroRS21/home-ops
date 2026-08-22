"""Tests for the DuckDB analytics layer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from home_ops import analytics
from home_ops.models.schema import Listing

if TYPE_CHECKING:
    from home_ops.models.data_storage import DuckDBConnection


def _insert(db: DuckDBConnection, price: float, m2: float, portal: str = "idealista") -> None:
    listing = Listing(
        content_hash=f"hash-{portal}-{price}-{m2}",
        address=f"Calle {price}",
        url=f"https://idealista.com/inmueble/{price}/",
        portal=portal,
        price=price,
        m2=m2,
    )
    db.insert_listing(listing)


class TestPriceStats:
    def test_empty_db(self, db: DuckDBConnection) -> None:
        stats = analytics.price_stats(db)
        assert stats["count"] == 0

    def test_distribution(self, db: DuckDBConnection) -> None:
        for price in (100_000.0, 150_000.0, 200_000.0, 250_000.0, 300_000.0):
            _insert(db, price, m2=80.0)
        stats = analytics.price_stats(db)
        assert stats["count"] == 5
        assert stats["min"] == 100_000.0
        assert stats["max"] == 300_000.0
        assert stats["p50"] == 200_000.0


class TestPricePerM2:
    def test_ignores_null_m2(self, db: DuckDBConnection) -> None:
        _insert(db, price=150_000.0, m2=75.0)
        # Listing with no m2 must be excluded from per-m² stats.
        listing = Listing(
            content_hash="hash-no-m2",
            address="Sin m2",
            url="https://idealista.com/inmueble/nom2/",
            portal="idealista",
            price=150_000.0,
            m2=None,
        )
        db.insert_listing(listing)
        stats = analytics.price_per_m2_stats(db)
        assert stats["count"] == 1
        assert stats["mean"] == 2000.0  # 150000 / 75


class TestPortalCounts:
    def test_groups_by_portal(self, db: DuckDBConnection) -> None:
        _insert(db, 100_000.0, 80.0, portal="idealista")
        _insert(db, 110_000.0, 80.0, portal="idealista")
        _insert(db, 120_000.0, 80.0, portal="fotocasa")
        counts = dict(analytics.portal_counts(db))
        assert counts == {"idealista": 2, "fotocasa": 1}


class TestRunsTimeseries:
    def test_empty(self, db: DuckDBConnection) -> None:
        assert analytics.runs_timeseries(db) == []

    def test_groups_by_day(self, db: DuckDBConnection) -> None:
        db.conn.execute(
            "INSERT INTO scraping_runs (finished_at, listings_found, listings_new, alerts_sent) "
            "VALUES ('2026-08-22 09:00:00', 10, 3, 1), ('2026-08-22 10:00:00', 5, 2, 0), "
            "('2026-08-21 09:00:00', 8, 8, 2)"
        )
        runs = analytics.runs_timeseries(db)
        assert len(runs) == 2
        by_day = {r["day"]: r for r in runs}
        assert by_day["2026-08-22"]["listings_found"] == 15
        assert by_day["2026-08-22"]["alerts_sent"] == 1
        assert by_day["2026-08-21"]["listings_new"] == 8
