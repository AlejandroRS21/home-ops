"""Dataclasses for scoring results.

ScoreResult and DimensionScore are the output types produced by RulesScorer.
They are plain dataclasses — no behavior, no dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal


@dataclass
class ScamRiskBreakdown:
    """Scam-risk evaluation result for a single listing.

    Attributes:
        risk_score: Aggregate risk in points [0.0, 100.0] (higher = riskier).
        red_flags: Active risk flag identifiers, e.g. ``["SCAM_RED_FLAG_TEXT"]``.
        price_anomaly_detected: Price >30% below the micro-zone median.
        missing_cert_detected: Energy certificate missing or unknown.
        total_penalty: Sum of active score penalties (points deducted).
    """

    risk_score: float
    red_flags: list[str]
    price_anomaly_detected: bool
    missing_cert_detected: bool
    total_penalty: float


@dataclass
class AcquisitionCostBreakdown:
    """Itemized acquisition-cost estimate for a single listing.

    Attributes:
        purchase_price: Listing price (excludes garage extras).
        property_type: ``"resale"`` or ``"new_build"`` (keyword detected).
        tax_type: ``"ITP"`` (resale) or ``"IVA+AJD"`` (new build).
        tax_rate: Effective tax rate applied to the purchase price.
        tax_amount: Taxes payable on the purchase price.
        notary_registry_fee: Estimated notary + registry fees (~1.5%).
        total_acquisition_cost: Purchase price + taxes + fees.
        monthly_mortgage_payment: Estimated monthly mortgage instalment.
        mortgage_effort_ratio: Monthly payment / net monthly income.
        high_effort_flag: True when the effort ratio exceeds the ceiling.
    """

    purchase_price: Decimal
    property_type: Literal["resale", "new_build"]
    tax_type: Literal["ITP", "IVA+AJD"]
    tax_rate: float
    tax_amount: Decimal
    notary_registry_fee: Decimal
    total_acquisition_cost: Decimal
    monthly_mortgage_payment: Decimal
    mortgage_effort_ratio: float
    high_effort_flag: bool


@dataclass
class ChecklistItem:
    """A single buyer-protection due-diligence checklist item."""

    title: str
    description: str


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
