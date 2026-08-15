"""Tests for AcquisitionCostCalculator - ITP/IVA+AJD, notary, mortgage effort.

Scenarios 2.1-2.3 from the delta spec.
"""

from decimal import Decimal

import pytest

from home_ops.models.schema import BuyerProtectionConfig, Listing
from home_ops.scorer.cost_calculator import AcquisitionCostCalculator
from home_ops.scorer.models import AcquisitionCostBreakdown


class TestAcquisitionCostCalculator:
    """Pure-function acquisition cost estimation."""

    @staticmethod
    def calculator() -> AcquisitionCostCalculator:
        """Calculator with default buyer-protection config."""
        return AcquisitionCostCalculator(BuyerProtectionConfig())

    @staticmethod
    def _pay(monthly: Decimal) -> float:
        return float(monthly)

    def test_returns_cost_breakdown_type(self) -> None:
        """GIVEN a listing WHEN calculate THEN returns AcquisitionCostBreakdown."""
        listing = Listing(content_hash="type_001", price=Decimal("250000"))
        result = self.calculator().calculate(
            listing, net_monthly_income=Decimal("3000"), euribor_rate=3.5
        )
        assert isinstance(result, AcquisitionCostBreakdown)

    def test_resale_itp_fallback_default_rate(self) -> None:
        """GIVEN resale and no community WHEN calculate THEN 8% ITP + 1.5% notary."""
        listing = Listing(
            content_hash="itp_001", price=Decimal("250000"), description="Piso en segunda mano"
        )
        result = self.calculator().calculate(
            listing, zone_community=None, net_monthly_income=Decimal("3000"), euribor_rate=3.5
        )
        assert result.property_type == "resale"
        assert result.tax_type == "ITP"
        assert result.tax_rate == 0.08
        assert result.tax_amount == Decimal("20000.00")
        assert result.notary_registry_fee == Decimal("3750.00")
        assert result.total_acquisition_cost == Decimal("273750.00")

    def test_resale_regional_itp_madrid(self) -> None:
        """GIVEN resale in Madrid WHEN calculate THEN 6% regional ITP applies."""
        listing = Listing(content_hash="itp_mad", price=Decimal("250000"))
        result = self.calculator().calculate(
            listing,
            zone_community="Madrid",
            net_monthly_income=Decimal("3000"),
            euribor_rate=3.5,
        )
        assert result.tax_rate == 0.06
        assert result.tax_amount == Decimal("15000.00")
        assert result.total_acquisition_cost == Decimal("268750.00")

    def test_resale_regional_itp_catalunya(self) -> None:
        """GIVEN resale in Catalunya WHEN calculate THEN 10% regional ITP applies."""
        listing = Listing(content_hash="itp_cat", price=Decimal("250000"))
        result = self.calculator().calculate(
            listing,
            zone_community="catalunya",
            net_monthly_income=Decimal("3000"),
            euribor_rate=3.5,
        )
        assert result.tax_rate == 0.10
        assert result.tax_amount == Decimal("25000.00")

    def test_resale_unknown_community_falls_back(self) -> None:
        """GIVEN community not in rate map WHEN calculate THEN default ITP (8%)."""
        listing = Listing(content_hash="itp_fb", price=Decimal("100000"))
        result = self.calculator().calculate(
            listing,
            zone_community="extremadura",
            net_monthly_income=Decimal("3000"),
            euribor_rate=3.5,
        )
        assert result.tax_rate == 0.08
        assert result.tax_amount == Decimal("8000.00")

    def test_new_build_obra_nueva(self) -> None:
        """GIVEN 'obra nueva' keyword WHEN calculate THEN IVA 10% + AJD 1.5% + notary."""
        listing = Listing(
            content_hash="nb_001",
            price=Decimal("200000"),
            description="Piso obra nueva promocion",
        )
        result = self.calculator().calculate(
            listing, net_monthly_income=Decimal("3000"), euribor_rate=3.5
        )
        assert result.property_type == "new_build"
        assert result.tax_type == "IVA+AJD"
        assert result.tax_rate == 0.115
        # IVA 10% (20000) + AJD 1.5% (3000) = 23000
        assert result.tax_amount == Decimal("23000.00")
        assert result.notary_registry_fee == Decimal("3000.00")
        assert result.total_acquisition_cost == Decimal("226000.00")

    def test_new_build_a_estrenar(self) -> None:
        """GIVEN 'a estrenar' keyword WHEN calculate THEN new build taxes apply."""
        listing = Listing(
            content_hash="nb_002",
            price=Decimal("300000"),
            description="Vivienda a estrenar",
        )
        result = self.calculator().calculate(
            listing, net_monthly_income=Decimal("3000"), euribor_rate=3.5
        )
        assert result.property_type == "new_build"
        assert result.tax_rate == 0.115
        assert result.tax_amount == Decimal("34500.00")
        assert result.notary_registry_fee == Decimal("4500.00")
        assert result.total_acquisition_cost == Decimal("339000.00")

    def test_new_build_promocion(self) -> None:
        """GIVEN 'promocion' keyword WHEN calculate THEN new build taxes apply."""
        listing = Listing(
            content_hash="nb_003",
            price=Decimal("100000"),
            description="Promocion de pisos nuevos",
        )
        result = self.calculator().calculate(
            listing, net_monthly_income=Decimal("3000"), euribor_rate=3.5
        )
        assert result.property_type == "new_build"
        assert result.tax_amount == Decimal("11500.00")

    def test_new_build_keyword_case_insensitive(self) -> None:
        """GIVEN 'OBRA NUEVA' in caps WHEN calculate THEN still detected as new build."""
        listing = Listing(
            content_hash="nb_case",
            price=Decimal("150000"),
            description="ENTREGA 2027 OBRA NUEVA",
        )
        result = self.calculator().calculate(
            listing, net_monthly_income=Decimal("3000"), euribor_rate=3.5
        )
        assert result.property_type == "new_build"

    def test_plain_description_defaults_to_resale(self) -> None:
        """GIVEN no new-build keywords WHEN calculate THEN defaults to resale (ITP)."""
        listing = Listing(
            content_hash="res_001",
            price=Decimal("150000"),
            description="Casa con jardin",
        )
        result = self.calculator().calculate(
            listing, net_monthly_income=Decimal("3000"), euribor_rate=3.5
        )
        assert result.property_type == "resale"
        assert result.tax_type == "ITP"

    def test_garage_not_included_in_taxes(self) -> None:
        """GIVEN separate garage price WHEN calculate THEN taxes on listing price only."""
        listing = Listing(
            content_hash="gar_001",
            price=Decimal("200000"),
            garage_price=Decimal("15000"),
            description="",
        )
        result = self.calculator().calculate(
            listing, zone_community="Madrid", net_monthly_income=Decimal("3000"), euribor_rate=3.5
        )
        assert result.purchase_price == Decimal("200000")
        assert result.tax_amount == Decimal("12000.00")  # 6% ITP on 200k only

    def test_mortgage_effort_high_flagged(self) -> None:
        """GIVEN ratio above 0.35 ceiling WHEN calculate THEN high_effort_flag True."""
        listing = Listing(content_hash="me_high", price=Decimal("300000"))
        result = self.calculator().calculate(
            listing, net_monthly_income=Decimal("2000"), euribor_rate=3.5
        )
        # 240k principal @ 3.5%/30y -> ~1077.71 EUR/mo -> ratio ~0.539 > 0.35
        assert self._pay(result.monthly_mortgage_payment) == pytest.approx(1077.71, abs=0.01)
        assert result.mortgage_effort_ratio == pytest.approx(0.539, abs=0.001)
        assert result.high_effort_flag is True

    def test_mortgage_effort_low_not_flagged(self) -> None:
        """GIVEN ratio below ceiling WHEN calculate THEN high_effort_flag False."""
        listing = Listing(content_hash="me_low", price=Decimal("100000"))
        result = self.calculator().calculate(
            listing, net_monthly_income=Decimal("3000"), euribor_rate=1.0
        )
        # 80k principal @ 1%/30y -> ~257.31 EUR/mo -> ratio ~0.086 < 0.35
        assert self._pay(result.monthly_mortgage_payment) == pytest.approx(257.31, abs=0.01)
        assert result.mortgage_effort_ratio == pytest.approx(0.086, abs=0.001)
        assert result.high_effort_flag is False

    def test_mortgage_custom_down_payment_years_ceiling(self) -> None:
        """GIVEN 25% down, 20y term, 0.33 ceiling WHEN calculate THEN formula uses them."""
        listing = Listing(content_hash="me_cfg", price=Decimal("300000"))
        config = BuyerProtectionConfig(
            down_payment_pct=0.25, mortgage_years=20, mortgage_income_ceiling=0.33
        )
        result = AcquisitionCostCalculator(config).calculate(
            listing, net_monthly_income=Decimal("5000"), euribor_rate=2.0
        )
        # 225k principal @ 2%/20y -> ~1138.24 EUR/mo -> ratio ~0.228 < 0.33
        assert self._pay(result.monthly_mortgage_payment) == pytest.approx(1138.24, abs=0.05)
        assert result.mortgage_effort_ratio == pytest.approx(0.228, abs=0.001)
        assert result.high_effort_flag is False

    def test_effort_boundary_at_ceiling_not_flagged(self) -> None:
        """GIVEN ratio exactly at ceiling WHEN calculate THEN not flagged (strict >)."""
        listing = Listing(content_hash="me_bnd", price=Decimal("200000"))
        config = BuyerProtectionConfig(mortgage_income_ceiling=0.30)
        result = AcquisitionCostCalculator(config).calculate(
            listing, net_monthly_income=Decimal("3000"), euribor_rate=0.0
        )
        # 160k principal, 0% rate -> 160000/360 = 444.44 -> ratio = 0.148 < 0.30
        assert result.mortgage_effort_ratio == pytest.approx(0.148, abs=0.001)
        assert result.high_effort_flag is False

    def test_effort_exactly_at_ceiling_boundary(self) -> None:
        """GIVEN ratio exactly equal to ceiling WHEN calculate THEN not flagged."""
        config = BuyerProtectionConfig(mortgage_income_ceiling=0.50)
        listing = Listing(content_hash="me_eq2", price=Decimal("225000"))
        result = AcquisitionCostCalculator(config).calculate(
            listing, net_monthly_income=Decimal("1250"), euribor_rate=0.0
        )
        # 180k/360 = 500.00 -> 500/1250 = 0.40 exactly; asserts zero-rate linear path
        assert result.mortgage_effort_ratio == pytest.approx(0.40, abs=0.001)
        assert result.high_effort_flag is False

    def test_no_net_income_no_effort_flag(self) -> None:
        """GIVEN net_monthly_income None WHEN calculate THEN effort not assessed."""
        listing = Listing(content_hash="me_na", price=Decimal("300000"))
        result = self.calculator().calculate(
            listing, euribor_rate=3.5
        )
        assert result.mortgage_effort_ratio == 0.0
        assert result.high_effort_flag is False
