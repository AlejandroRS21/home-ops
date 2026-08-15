"""Scam-risk detection for property listings.

Pure-function scorer: NLP red-flag phrases, price anomaly vs the
micro-zone median, and missing energy certificate checks. Deterministic
and side-effect free — same listing + median always yields the same
``ScamRiskBreakdown``.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Final

from home_ops.models.schema import BuyerProtectionConfig, Listing
from home_ops.scorer.models import ScamRiskBreakdown

SCAM_RED_FLAG_TEXT: Final = "SCAM_RED_FLAG_TEXT"
SCAM_SUSPECT_PRICE_BAIT: Final = "SCAM_SUSPECT_PRICE_BAIT"
MISSING_ENERGY_CERT: Final = "MISSING_ENERGY_CERT"

# A listing more than 30% below the micro-zone median is a price-bait suspect.
PRICE_ANOMALY_THRESHOLD: Final = Decimal("0.30")


class ScamRiskScorer:
    """Pure-function scam-risk evaluation for a single listing.

    Args:
        config: Buyer-protection settings — red-flag patterns and scam
            penalty weights from ``BuyerProtectionConfig``.
    """

    def __init__(self, config: BuyerProtectionConfig) -> None:
        self.config = config
        self._patterns = [
            re.compile(pattern, re.IGNORECASE) for pattern in config.red_flag_patterns
        ]

    def evaluate(self, listing: Listing, zone_median: Decimal) -> ScamRiskBreakdown:
        """Evaluate a listing and return its scam-risk breakdown.

        Args:
            listing: The listing to evaluate (description, price, and
                energy-cert metadata are inspected).
            zone_median: Micro-zone median price used for the price-bait
                anomaly check.

        Returns:
            ScamRiskBreakdown with flags and the aggregated penalty in points.
        """
        red_flags: list[str] = []
        penalty = 0.0

        if self._has_red_flag_text(listing.description):
            red_flags.append(SCAM_RED_FLAG_TEXT)
            penalty += self.config.scam_weights["red_flag_text"]

        price_anomaly = self._is_price_anomaly(listing.price, zone_median)
        if price_anomaly:
            red_flags.append(SCAM_SUSPECT_PRICE_BAIT)
            penalty += self.config.scam_weights["price_bait"]

        missing_cert = listing.certificado_energetico_present is not True
        if missing_cert:
            red_flags.append(MISSING_ENERGY_CERT)
            penalty += self.config.scam_weights["missing_cert"]

        return ScamRiskBreakdown(
            risk_score=min(penalty, 100.0),
            red_flags=red_flags,
            price_anomaly_detected=price_anomaly,
            missing_cert_detected=missing_cert,
            total_penalty=penalty,
        )

    # ------------------------------------------------------------------
    # Internal checks
    # ------------------------------------------------------------------

    def _has_red_flag_text(self, description: str) -> bool:
        """True when any configured red-flag pattern matches the description."""
        return any(p.search(description) is not None for p in self._patterns)

    def _is_price_anomaly(self, price: Decimal | None, zone_median: Decimal) -> bool:
        """True when price is strictly more than 30% below the zone median."""
        if price is None or price <= 0 or zone_median <= 0:
            return False
        return price < zone_median * (Decimal("1") - PRICE_ANOMALY_THRESHOLD)
