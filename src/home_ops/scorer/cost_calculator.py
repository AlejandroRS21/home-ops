"""Acquisition cost estimation for property listings.

Pure-function calculator: regional ITP (resale) or IVA+AJD (new build)
taxes, notary + registry fees, and a monthly mortgage effort ratio.
Deterministic and side-effect free.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal

from home_ops.models.schema import BuyerProtectionConfig, Listing
from home_ops.scorer.models import AcquisitionCostBreakdown

# New-build tax: 10% IVA + 1.5% AJD on the purchase price.
NEW_BUILD_IVA_RATE: Final = Decimal("0.10")
NEW_BUILD_AJD_RATE: Final = Decimal("0.015")
# Notary + registry estimate: ~1.5% of the purchase price.
NOTARY_REGISTRY_RATE: Final = Decimal("0.015")
# Fallback euribor when no rate is provided (matches scorer fallback).
DEFAULT_EURIBOR_RATE: Final = 3.5

_NEW_BUILD_RE: Final = re.compile(
    r"obra nueva|promoci[oó]n|a estrenar", re.IGNORECASE
)

_CENT = Decimal("0.01")


class AcquisitionCostCalculator:
    """Pure-function acquisition cost estimation for a single listing.

    Args:
        config: Buyer-protection settings — ITP rates, down-payment
            percentage, mortgage term, and effort ceiling.
    """

    def __init__(self, config: BuyerProtectionConfig) -> None:
        self.config = config

    def calculate(
        self,
        listing: Listing,
        zone_community: str | None = None,
        net_monthly_income: Decimal | None = None,
        euribor_rate: float | None = None,
    ) -> AcquisitionCostBreakdown:
        """Estimate the total acquisition cost and mortgage effort.

        Args:
            listing: The listing (price and description inspected).
            zone_community: Autonomous Community for regional ITP lookup;
                falls back to ``default_itp_rate`` when absent or unknown.
            net_monthly_income: Buyer net monthly income used for the
                effort ratio; ``None`` skips the effort assessment.
            euribor_rate: Annual Euribor rate (e.g. 3.5 for 3.5%).

        Returns:
            AcquisitionCostBreakdown with itemized taxes, fees, total
            outlay, and mortgage effort.
        """
        price = listing.price or Decimal("0")
        property_type, tax_type, tax_rate = self._resolve_tax(
            listing.description, zone_community
        )

        tax_rate_dec = Decimal(str(tax_rate))
        tax_amount = (price * tax_rate_dec).quantize(_CENT, rounding=ROUND_HALF_UP)
        notary_fee = (price * NOTARY_REGISTRY_RATE).quantize(
            _CENT, rounding=ROUND_HALF_UP
        )
        total = price + tax_amount + notary_fee

        monthly_payment = self._monthly_payment(price, euribor_rate)
        ratio, high_effort = self._effort_ratio(
            monthly_payment, net_monthly_income
        )

        return AcquisitionCostBreakdown(
            purchase_price=price,
            property_type=property_type,
            tax_type=tax_type,
            tax_rate=tax_rate,
            tax_amount=tax_amount,
            notary_registry_fee=notary_fee,
            total_acquisition_cost=total,
            monthly_mortgage_payment=monthly_payment,
            mortgage_effort_ratio=ratio,
            high_effort_flag=high_effort,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_tax(
        self, description: str, zone_community: str | None
    ) -> tuple[Literal["resale", "new_build"], Literal["ITP", "IVA+AJD"], float]:
        """Return (property type, tax type, effective tax rate)."""
        if _NEW_BUILD_RE.search(description) is not None:
            rate = float(NEW_BUILD_IVA_RATE + NEW_BUILD_AJD_RATE)
            return "new_build", "IVA+AJD", rate

        if zone_community:
            regional_rate = self.config.regional_itp_rates.get(zone_community.lower())
            if regional_rate is not None:
                return "resale", "ITP", regional_rate
        return "resale", "ITP", self.config.default_itp_rate

    def _monthly_payment(self, price: Decimal, euribor_rate: float | None) -> Decimal:
        """Monthly mortgage payment using the standard amortisation formula.

        Principal = price x (1 - down payment); term from config.
        """
        principal = price * (Decimal("1") - Decimal(str(self.config.down_payment_pct)))
        annual_rate = euribor_rate if euribor_rate is not None else DEFAULT_EURIBOR_RATE
        monthly_rate = Decimal(str(annual_rate / 100.0 / 12.0))
        n_payments = self.config.mortgage_years * 12

        if principal <= 0 or n_payments <= 0:
            return Decimal("0.00")

        if monthly_rate == 0:
            return (principal / n_payments).quantize(_CENT, rounding=ROUND_HALF_UP)

        factor = (Decimal("1") + monthly_rate) ** n_payments
        payment = principal * monthly_rate * factor / (factor - Decimal("1"))
        return payment.quantize(_CENT, rounding=ROUND_HALF_UP)

    def _effort_ratio(
        self, monthly_payment: Decimal, net_monthly_income: Decimal | None
    ) -> tuple[float, bool]:
        """Return (effort ratio, high-effort flag)."""
        if net_monthly_income is None or net_monthly_income <= 0:
            return 0.0, False
        ratio = float(monthly_payment / net_monthly_income)
        return ratio, ratio > self.config.mortgage_income_ceiling
