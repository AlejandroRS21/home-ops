"""Dataclasses for scoring results.

ScoreResult and DimensionScore are the output types produced by RulesScorer.
They are plain dataclasses — no behavior, no dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class DimensionScore:
    """Score for a single dimension of a listing.

    Attributes:
        name: Dimension identifier — "price", "size", "energy_cert", "garage",
            "affordability".
        score: Normalized score in range [0.0, 1.0], where 1.0 means the
            threshold is fully satisfied.
        weight: Configuration weight for this dimension (from
            scoring.thresholds.weights).
        raw_value: Original field value from the Listing — preserved for
            debugging and audit.
    """

    name: str
    score: float
    weight: float
    raw_value: Any


@dataclass
class ScoreResult:
    """Aggregated scoring result for a single listing.

    Attributes:
        total: Weighted sum of dimension scores in range [0.0, 1.0]. Always a
            valid float — weight redistribution ensures this even with None
            fields. Multiply by 100 to compare against legacy min_score_to_alert
            thresholds (0-100 scale).
        dimensions: Per-dimension breakdown in config order.
        listing_id: Database ID of the listing, or None if not yet persisted.
        computed_at: Timestamp when the score was computed.
        weights_adjusted: True when one or more dimensions had a None field,
            causing proportional weight redistribution among remaining
            dimensions.
        flags: Informational flags discovered during scoring, e.g.
            ``["certificado_missing"]``.
    """

    total: float
    dimensions: list[DimensionScore] = field(default_factory=list)
    listing_id: int | None = None
    computed_at: datetime | None = None
    weights_adjusted: bool = False
    flags: list[str] = field(default_factory=list)
