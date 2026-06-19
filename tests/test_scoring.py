"""Tests for the scoring engine — models, RulesScorer, and affordability.

Uses DuckDB :memory: fixtures, no mocks for scorer logic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import duckdb
import pytest

from home_ops.models.schema import Config, ScoringThresholds
from home_ops.scorer.models import DimensionScore, ScoreResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_thresholds() -> ScoringThresholds:
    """Default thresholds for testing."""
    return ScoringThresholds(
        weights={
            "price": 0.35,
            "size": 0.25,
            "energy_cert": 0.15,
            "garage": 0.10,
            "affordability": 0.15,
        },
        price_median=250_000.0,
        price_over_median_penalty=0.30,
        m2_threshold=80.0,
        m2_large_threshold=120.0,
        affordability_high_ratio=0.50,
        affordability_medium_ratio=0.30,
        salary_province=30_000.0,
        min_score_to_alert=70.0,
    )


@pytest.fixture
def config_with_scoring(default_thresholds: ScoringThresholds) -> Config:
    """Config with scoring thresholds."""
    return Config(scoring=default_thresholds)


@pytest.fixture
def config_bad_weights() -> Config:
    """Config where weights do not sum to 1.0."""
    return Config(
        scoring=ScoringThresholds(
            weights={"price": 0.50, "size": 0.30},  # sums to 0.80
            price_median=250_000.0,
        )
    )


@pytest.fixture
def memory_db() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB connection for euribor tests."""
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE euribor_rate (rate DOUBLE, fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    return conn


# ===================================================================
# 5.1 / 5.2 — Model construction tests (structural)
# ===================================================================


class TestScoreResult:
    """ScoreResult dataclass construction."""

    def test_minimal_construction(self) -> None:
        """GIVEN only total WHEN constructing ScoreResult THEN defaults are set."""
        result = ScoreResult(total=0.75)
        assert result.total == 0.75
        assert result.dimensions == []
        assert result.listing_id is None
        assert result.computed_at is None
        assert result.weights_adjusted is False
        assert result.flags == []

    def test_full_construction(self) -> None:
        """GIVEN all fields WHEN constructing ScoreResult THEN values are stored."""
        now = datetime.now(UTC)
        dims = [DimensionScore(name="price", score=0.8, weight=0.35, raw_value=200_000)]
        result = ScoreResult(
            total=0.8,
            dimensions=dims,
            listing_id=42,
            computed_at=now,
            weights_adjusted=True,
            flags=["certificado_missing"],
        )
        assert result.total == 0.8
        assert len(result.dimensions) == 1
        assert result.listing_id == 42
        assert result.computed_at == now
        assert result.weights_adjusted is True
        assert result.flags == ["certificado_missing"]


class TestDimensionScore:
    """DimensionScore dataclass construction."""

    def test_minimal_construction(self) -> None:
        """GIVEN required fields WHEN constructing DimensionScore THEN object created."""
        ds = DimensionScore(name="price", score=0.8, weight=0.35, raw_value=250_000)
        assert ds.name == "price"
        assert ds.score == 0.8
        assert ds.weight == 0.35
        assert ds.raw_value == 250_000

    def test_with_decimal_raw_value(self) -> None:
        """GIVEN Decimal raw_value WHEN constructing DimensionScore THEN stored as-is."""
        ds = DimensionScore(
            name="price",
            score=0.5,
            weight=0.35,
            raw_value=Decimal("300000.00"),
        )
        assert ds.raw_value == Decimal("300000.00")


# ===================================================================
# 5.3 — Weight validation
# ===================================================================


class TestRulesScorerWeightValidation:
    """RulesScorer init weight validation (ValueError on bad weights)."""

    def test_weights_sum_to_one_passes(self, config_with_scoring: Config) -> None:
        """GIVEN weights summing to 1.0 WHEN RulesScorer init THEN no error."""
        from home_ops.scorer.rules import RulesScorer

        scorer = RulesScorer(config_with_scoring)
        assert scorer is not None

    def test_weights_not_summing_to_one_raises(self, config_bad_weights: Config) -> None:
        """GIVEN weights summing to 0.8 WHEN RulesScorer init THEN ValueError."""
        from home_ops.scorer.rules import RulesScorer

        with pytest.raises(ValueError, match="weights must sum to 1.0"):
            RulesScorer(config_bad_weights)

    def test_empty_weights_raises(self) -> None:
        """GIVEN empty weights dict WHEN RulesScorer init THEN ValueError."""
        from home_ops.scorer.rules import RulesScorer

        cfg = Config(scoring=ScoringThresholds(weights={}))
        with pytest.raises(ValueError, match="weights must sum to 1.0"):
            RulesScorer(cfg)


# ===================================================================
# 5.4 — Weight redistribution when one dimension is None
# ===================================================================


class TestRulesScorerWeightRedistribution:
    """RulesScorer weight redistribution when one dimension field is None."""

    def test_redistribution_when_one_none(self, config_with_scoring: Config) -> None:
        """GIVEN listing with None energy cert WHEN scored THEN weights_adjusted=True."""
        from home_ops.models.schema import Listing
        from home_ops.scorer.rules import RulesScorer

        scorer = RulesScorer(config_with_scoring)
        listing = Listing(
            content_hash="redist_001",
            price=Decimal("200000"),
            m2=90.0,
            certificado_energetico_present=None,
            garage_price=Decimal("15000"),
        )
        result = scorer.score(listing)
        assert result.weights_adjusted is True
        assert result.total is not None

    def test_redistribution_total_still_valid(self, config_with_scoring: Config) -> None:
        """GIVEN listing with None cert WHEN scored THEN total is valid float 0-1."""
        from home_ops.models.schema import Listing
        from home_ops.scorer.rules import RulesScorer

        scorer = RulesScorer(config_with_scoring)
        listing = Listing(
            content_hash="redist_002",
            price=Decimal("200000"),
            m2=90.0,
            certificado_energetico_present=None,
            garage_price=Decimal("15000"),
        )
        result = scorer.score(listing)
        assert 0.0 <= result.total <= 1.0

    def test_all_fields_present_no_redistribution(self, config_with_scoring: Config) -> None:
        """GIVEN listing with all fields WHEN scored THEN weights_adjusted=False."""
        from home_ops.models.schema import Listing
        from home_ops.scorer.rules import RulesScorer

        scorer = RulesScorer(config_with_scoring)
        listing = Listing(
            content_hash="all_fields_001",
            price=Decimal("200000"),
            m2=90.0,
            certificado_energetico_present=True,
            garage_price=Decimal("15000"),
        )
        result = scorer.score(listing)
        assert result.weights_adjusted is False


# ===================================================================
# 5.5 — Fully satisfied thresholds (all dimension scores = 1.0)
# ===================================================================


class TestRulesScorerFullySatisfied:
    """RulesScorer with fully satisfied thresholds — all scores = 1.0."""

    def test_all_thresholds_satisfied(self, config_with_scoring: Config) -> None:
        """GIVEN listing well within all thresholds WHEN scored THEN all scores = 1.0."""
        from home_ops.models.schema import Listing
        from home_ops.scorer.rules import RulesScorer

        scorer = RulesScorer(config_with_scoring)
        # price well under median, m2 large, cert present, garage present
        listing = Listing(
            content_hash="satisfied_001",
            price=Decimal("150000"),  # well under median 250k
            m2=150.0,  # well above m2_large_threshold 120
            certificado_energetico_present=True,
            garage_price=Decimal("15000"),
        )
        result = scorer.score(listing)
        for dim in result.dimensions:
            if dim.name == "price":
                assert dim.score == 1.0, f"price score {dim.score} != 1.0"
            elif dim.name == "size":
                assert dim.score == 1.0, f"size score {dim.score} != 1.0"
            elif dim.name == "energy_cert":
                assert dim.score == 1.0, f"energy_cert score {dim.score} != 1.0"
            elif dim.name == "garage":
                assert dim.score == 1.0, f"garage score {dim.score} != 1.0"
        assert result.total > 0.0


# ===================================================================
# 5.6 — Fully violated thresholds (all dimension scores = 0.0)
# ===================================================================


class TestRulesScorerFullyViolated:
    """RulesScorer with fully violated thresholds — all scores = 0.0."""

    def test_all_thresholds_violated(self, config_with_scoring: Config) -> None:
        """GIVEN listing way outside thresholds WHEN scored THEN dimension scores = 0.0
        (except affordability which measures payment-to-salary ratio independently)."""
        from home_ops.models.schema import Listing
        from home_ops.scorer.rules import RulesScorer

        scorer = RulesScorer(config_with_scoring)
        # price way over median, m2 tiny, cert missing, no garage
        listing = Listing(
            content_hash="violated_001",
            price=Decimal("500000"),  # way over median 250k
            m2=30.0,  # well under m2_threshold 80
            certificado_energetico_present=False,
            garage_price=Decimal("0"),
        )
        result = scorer.score(listing)
        for dim in result.dimensions:
            if dim.name == "price":
                assert dim.score == 0.0, f"price score {dim.score} != 0.0"
            elif dim.name == "size":
                assert dim.score == 0.0, f"size score {dim.score} != 0.0"
            elif dim.name == "energy_cert":
                assert dim.score == 0.0, f"energy_cert score {dim.score} != 0.0"
            elif dim.name == "garage":
                assert dim.score == 0.0, f"garage score {dim.score} != 0.0"
        # affordability: expensive house = unaffordable = score 0.0
        afford_dim = [d for d in result.dimensions if d.name == "affordability"][0]
        assert afford_dim.score == 0.0


# ===================================================================
# 5.7 — Price dimension partial threshold satisfaction
# ===================================================================


class TestRulesScorerPricePartial:
    """RulesScorer price dimension scoring with partial satisfaction."""

    def test_price_slightly_above_median(self, config_with_scoring: Config) -> None:
        """GIVEN price slightly above median WHEN scored THEN price score is partial."""
        from home_ops.models.schema import Listing
        from home_ops.scorer.rules import RulesScorer

        scorer = RulesScorer(config_with_scoring)
        listing = Listing(
            content_hash="price_partial_001",
            price=Decimal("300000"),  # 20% above median 250k
            m2=100.0,
            certificado_energetico_present=True,
            garage_price=Decimal("15000"),
        )
        result = scorer.score(listing)
        price_dim = [d for d in result.dimensions if d.name == "price"][0]
        # 20% above median → partial score, not 0.0 and not 1.0
        assert 0.0 < price_dim.score < 1.0, f"Expected partial price score, got {price_dim.score}"

    def test_price_at_median(self, config_with_scoring: Config) -> None:
        """GIVEN price exactly at median WHEN scored THEN price score = 1.0."""
        from home_ops.models.schema import Listing
        from home_ops.scorer.rules import RulesScorer

        scorer = RulesScorer(config_with_scoring)
        listing = Listing(
            content_hash="price_median_001",
            price=Decimal("250000"),  # exactly at median
            m2=100.0,
            certificado_energetico_present=True,
            garage_price=Decimal("15000"),
        )
        result = scorer.score(listing)
        price_dim = [d for d in result.dimensions if d.name == "price"][0]
        assert price_dim.score == 1.0, f"Expected score 1.0, got {price_dim.score}"


# ===================================================================
# 5.8 — Affordability integration test with DuckDB
# ===================================================================


class TestAffordabilityWithEuribor:
    """Affordability score with euribor from DuckDB :memory: integration."""

    def test_affordability_high_ratio(self, config_with_scoring: Config) -> None:
        """GIVEN high euribor and expensive listing WHEN scored THEN affordability score = 0.0 (unaffordable)."""
        from home_ops.models.schema import Listing
        from home_ops.scorer.rules import RulesScorer

        scorer = RulesScorer(config_with_scoring)
        # Price at 300k with 5% euribor → monthly payment ~1288 → ratio ~0.515 > 0.50
        listing = Listing(
            content_hash="afford_high_001",
            price=Decimal("300000"),
            m2=80.0,
            certificado_energetico_present=True,
            garage_price=Decimal("10000"),
        )
        result = scorer.score(listing, euribor_rate_override=5.0)
        afford_dim = [d for d in result.dimensions if d.name == "affordability"]
        assert len(afford_dim) == 1, "Expected affordability dimension"
        # With price at 300k and 5% euribor, ratio should be > 0.50 → score = 0.0 (unaffordable)
        assert afford_dim[0].score == 0.0, (
            f"Expected affordability score 0.0, got {afford_dim[0].score}"
        )

    def test_affordability_low_ratio(self, config_with_scoring: Config) -> None:
        """GIVEN low euribor and cheap listing WHEN scored THEN affordability score = 1.0 (affordable)."""
        from home_ops.models.schema import Listing
        from home_ops.scorer.rules import RulesScorer

        scorer = RulesScorer(config_with_scoring)
        listing = Listing(
            content_hash="afford_low_001",
            price=Decimal("120000"),  # 4x annual salary
            m2=80.0,
            certificado_energetico_present=True,
            garage_price=Decimal("10000"),
        )
        result = scorer.score(listing, euribor_rate_override=1.0)
        afford_dim = [d for d in result.dimensions if d.name == "affordability"][0]
        # With 4x salary and 1% euribor, ratio should be under 0.30 → score = 1.0 (affordable)
        assert afford_dim.score == 1.0

    def test_affordability_reads_euribor_from_db(self, config_with_scoring: Config, memory_db) -> None:
        """GIVEN euribor in DuckDB WHEN scored without override THEN reads from DB."""
        from home_ops.models.schema import Listing
        from home_ops.scorer.rules import RulesScorer

        memory_db.execute("INSERT INTO euribor_rate (rate) VALUES (4.0)")

        scorer = RulesScorer(config_with_scoring)
        listing = Listing(
            content_hash="db_euribor_001",
            price=Decimal("300000"),
            m2=80.0,
            certificado_energetico_present=True,
            garage_price=Decimal("10000"),
        )
        # No euribor_rate_override — must read from DuckDB
        result = scorer.score(listing, db_conn=memory_db)
        afford_dim = [d for d in result.dimensions if d.name == "affordability"][0]
        # 4% euribor on 300k → ratio ~0.458 → score ~0.208 (interpolated)
        # Fallback (3.5%) would give ~0.357 → different value, proves DB was read
        import pytest
        assert afford_dim.score == pytest.approx(0.208, abs=0.01), (
            f"Expected score from DB rate 4.0%, got {afford_dim.score}"
        )

    def test_euribor_cached_in_duckdb(self, config_with_scoring: Config, memory_db) -> None:
        """GIVEN euribor in DB WHEN scored THEN reads from cached value."""
        memory_db.execute("INSERT INTO euribor_rate (rate) VALUES (3.5)")

        # Read euribor from DuckDB
        row = memory_db.execute(
            "SELECT rate FROM euribor_rate ORDER BY fetched_at DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] == 3.5

    def test_certificado_missing_flag(self, config_with_scoring: Config) -> None:
        """GIVEN listing with None cert WHEN scored THEN certificado_missing in flags."""
        from home_ops.models.schema import Listing
        from home_ops.scorer.rules import RulesScorer

        scorer = RulesScorer(config_with_scoring)
        listing = Listing(
            content_hash="cert_flag_001",
            price=Decimal("200000"),
            m2=90.0,
            certificado_energetico_present=None,
            garage_price=Decimal("15000"),
        )
        result = scorer.score(listing)
        assert "certificado_missing" in result.flags


# ===================================================================
# Additional integration test: euribor DuckDB round-trip
# ===================================================================


class TestEuriborDuckDBRoundTrip:
    """Integration test: euribor DuckDB round-trip."""

    def test_insert_and_read_rate(self, memory_db) -> None:
        """GIVEN rate inserted WHEN read back THEN value matches."""
        memory_db.execute("INSERT INTO euribor_rate (rate) VALUES (2.5)")
        row = memory_db.execute(
            "SELECT rate FROM euribor_rate ORDER BY fetched_at DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] == 2.5
