"""RulesScorer — config-driven multi-dimensional listing scoring.

Receives a Config via constructor DI, validates weights on init,
iterates config-driven dimensions, redistributes weight for None
fields, and returns a ScoreResult.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pydantic

from home_ops.models.schema import Config, Listing
from home_ops.scorer.affordability import score_affordability
from home_ops.scorer.models import DimensionScore, ScoreResult

logger = logging.getLogger(__name__)


class RulesScorer:
    """Multi-dimensional scoring engine for property listings.

    All thresholds come from ``config.scoring`` — zero hardcoded values.
    Weights MUST sum to 1.0 (ValueError on init if not). None fields
    cause proportional weight redistribution among the remaining dimensions.
    """

    def __init__(self, config: Config) -> None:
        """Initialise scorer with config.

        Args:
            config: Application config with optional ``scoring`` attribute.

        Raises:
            ValueError: If configured weights do not sum to 1.0
                (within floating-point tolerance).
        """
        scoring = getattr(config, "scoring", None)

        # Defensive: handle mock/partial config objects that aren't proper models
        if scoring is None or not isinstance(scoring, pydantic.BaseModel):
            from home_ops.models.schema import ScoringThresholds

            self.thresholds = ScoringThresholds()
        else:
            self.thresholds = scoring

        self.weights = dict(self.thresholds.weights)
        self._validate_weights()
        # Store configured euribor fallback (from config or hardcoded default)
        self._euribor_fallback = getattr(config, "euribor_rate", 3.5)

    def _validate_weights(self) -> None:
        """Ensure configured weights sum to 1.0 (within 1e-6 tolerance).

        Raises:
            ValueError: If total deviates from 1.0.
        """
        total = sum(self.weights.values())
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(
                f"weights must sum to 1.0, got {total:.4f}"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(
        self,
        listing: Listing,
        db_conn: Any = None,
        euribor_rate_override: float | None = None,
    ) -> ScoreResult:
        """Score a single listing across all configured dimensions.

        Args:
            listing: The listing to score.
            db_conn: Optional DuckDB connection for euribor lookup.
            euribor_rate_override: Override euribor rate for testing.

        Returns:
            ScoreResult with total, per-dimension breakdown, and flags.
        """
        dims: list[DimensionScore] = []
        flags: list[str] = []
        weights_adjusted = False

        raw_price = listing.price
        raw_m2 = listing.m2
        raw_cert = listing.certificado_energetico_present
        raw_garage = listing.garage_price

        # Build field map for weights adjustment
        field_map: list[tuple[str, Any]] = [
            ("price", raw_price),
            ("size", raw_m2),
            ("energy_cert", raw_cert),
            ("garage", raw_garage),
        ]

        # Check for None fields and adjust weights
        active_dim_keys: list[str] = []
        active_fields: list[tuple[str, Any]] = []
        for dim_key, raw_val in field_map:
            if raw_val is None:
                weights_adjusted = True
                if dim_key == "energy_cert":
                    flags.append("certificado_missing")
            else:
                active_dim_keys.append(dim_key)
                active_fields.append((dim_key, raw_val))

        # Affordability is always active (uses config salary, not listing field)
        active_fields.append(("affordability", raw_price))
        active_dim_keys.append("affordability")

        # Redistribute weights proportionally among active dimensions
        active_weights = self._compute_active_weights(active_dim_keys, weights_adjusted)

        # Compute per-dimension scores
        for dim_key, raw_val in active_fields:
            weight = active_weights.get(dim_key, 0.0)

            if dim_key == "price":
                score = self._score_price(raw_val)
            elif dim_key == "size":
                score = self._score_size(raw_val)
            elif dim_key == "energy_cert":
                score = self._score_energy_cert(raw_val)
            elif dim_key == "garage":
                score = self._score_garage(raw_val)
            elif dim_key == "affordability":
                score = self._score_affordability(
                    raw_val, db_conn, euribor_rate_override
                )
            else:
                score = 0.0

            dims.append(
                DimensionScore(
                    name=dim_key,
                    score=score,
                    weight=weight,
                    raw_value=raw_val,
                )
            )

        total = sum(d.score * d.weight for d in dims)

        return ScoreResult(
            total=total,
            dimensions=dims,
            listing_id=listing.id,
            computed_at=datetime.now(UTC),
            weights_adjusted=weights_adjusted,
            flags=flags,
        )

    # ------------------------------------------------------------------
    # Weight redistribution
    # ------------------------------------------------------------------

    def _compute_active_weights(
        self,
        active_keys: list[str],
        was_adjusted: bool,
    ) -> dict[str, float]:
        """Return weights for the active dimensions.

        If adjustment is needed, redistribute the weight of missing
        dimensions proportionally across remaining ones.

        Args:
            active_keys: Dimension keys that have non-None values.
            was_adjusted: Whether any dimension was excluded.

        Returns:
            Mapping of dimension key to effective weight.
        """
        if not was_adjusted:
            # No adjustment — return original weights for all configured dims
            return dict(self.weights)

        all_weight = sum(self.weights.get(k, 0.0) for k in active_keys)
        if all_weight <= 0:
            return {k: 0.0 for k in active_keys}

        factor = 1.0 / all_weight
        return {k: self.weights.get(k, 0.0) * factor for k in active_keys}

    # ------------------------------------------------------------------
    # Per-dimension scorers
    # ------------------------------------------------------------------

    def _score_price(self, price: Decimal | None) -> float:
        """Score the price dimension.

        Returns 1.0 if price is at or below median, linearly decreasing
        toward 0.0 as price exceeds median by the penalty threshold.
        """
        if price is None:
            return 0.0

        median = self.thresholds.price_median
        penalty = self.thresholds.price_over_median_penalty

        if price <= median:
            return 1.0

        # Price above median: linear penalty
        over_ratio = (float(price) - median) / median
        # At penalty threshold → score = 0.0
        if over_ratio >= penalty:
            return 0.0
        return 1.0 - (over_ratio / penalty)

    def _score_size(self, m2: float | None) -> float:
        """Score the size dimension.

        Returns 1.0 if m2 >= m2_large_threshold, 0.0 if < m2_threshold,
        linearly interpolated in between.
        """
        if m2 is None:
            return 0.0

        small = self.thresholds.m2_threshold
        large = self.thresholds.m2_large_threshold

        if m2 >= large:
            return 1.0
        if m2 < small:
            return 0.0
        # Linear interpolation between small and large
        return (m2 - small) / (large - small)

    def _score_energy_cert(self, present: bool | None) -> float:
        """Score the energy certificate dimension.

        1.0 if present, 0.0 if absent or None.
        """
        return 1.0 if present is True else 0.0

    def _score_garage(self, garage_price: Decimal | None) -> float:
        """Score the garage dimension.

        1.0 if garage_price > 0, 0.0 if zero or None.
        """
        if garage_price is None:
            return 0.0
        return 1.0 if garage_price > 0 else 0.0

    def _score_affordability(
        self,
        price: Decimal | None,
        db_conn: Any = None,
        rate_override: float | None = None,
    ) -> float:
        """Score the affordability dimension.

        Delegates to ``score_affordability()`` in the affordability
        module with thresholds from config.

        Args:
            price: Listing price.
            db_conn: DuckDB connection for euribor lookup.
            rate_override: Test override for euribor rate.

        Returns:
            Score in [0.0, 1.0] where 1.0 = affordable (low ratio), 0.0 = unaffordable (high ratio).
        """
        if price is None:
            return 0.0

        euribor = self._get_euribor_rate(db_conn, rate_override)

        return score_affordability(
            price=price,
            euribor_rate=euribor,
            salary_province=self.thresholds.salary_province,
            high_ratio=self.thresholds.affordability_high_ratio,
            medium_ratio=self.thresholds.affordability_medium_ratio,
        )

    def _get_euribor_rate(
        self,
        db_conn: Any = None,
        rate_override: float | None = None,
    ) -> float:
        """Get the current euribor rate.

        Priority: rate_override > DuckDB > config default.
        """
        if rate_override is not None:
            return rate_override

        if db_conn is not None:
            row = db_conn.execute(
                "SELECT rate FROM euribor_rate ORDER BY fetched_at DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                return float(row[0])

        logger.warning("No Euribor in DB — using fallback %.2f%%", self._euribor_fallback)
        return self._euribor_fallback
