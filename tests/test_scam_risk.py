"""Tests for ScamRiskScorer — red-flag text, price anomaly, missing energy cert.

Scenarios 1.1-1.4 from the delta spec.
"""

from decimal import Decimal

from home_ops.models.schema import BuyerProtectionConfig, Listing
from home_ops.scorer.models import ScamRiskBreakdown
from home_ops.scorer.scam_risk import ScamRiskScorer


class TestScamRiskScorer:
    """Pure-function scam-risk evaluation."""

    @staticmethod
    def scorer() -> ScamRiskScorer:
        """Scorer with default buyer-protection config."""
        return ScamRiskScorer(BuyerProtectionConfig())

    def test_returns_breakdown_type(self) -> None:
        """GIVEN a listing WHEN evaluate THEN returns ScamRiskBreakdown."""
        breakdown = self.scorer().evaluate(
            Listing(content_hash="type_001", price=Decimal("200000")),
            zone_median=Decimal("250000"),
        )
        assert isinstance(breakdown, ScamRiskBreakdown)

    def test_red_flag_text_detected(self) -> None:
        """GIVEN 'solo whatsapp' in description WHEN evaluate THEN SCAM_RED_FLAG_TEXT -40."""
        breakdown = self.scorer().evaluate(
            Listing(
                content_hash="rf_001",
                description="Piso barato. Contacto solo whatsapp",
                price=Decimal("200000"),
                certificado_energetico_present=True,
            ),
            zone_median=Decimal("250000"),
        )
        assert "SCAM_RED_FLAG_TEXT" in breakdown.red_flags
        assert breakdown.total_penalty == 40.0

    def test_red_flag_reserva_before_visiting(self) -> None:
        """GIVEN 'reserva antes de visitar' WHEN evaluate THEN red flag penalty."""
        breakdown = self.scorer().evaluate(
            Listing(
                content_hash="rf_002",
                description="Se pide reserva antes de visitar la vivienda",
                price=Decimal("200000"),
                certificado_energetico_present=True,
            ),
            zone_median=Decimal("250000"),
        )
        assert "SCAM_RED_FLAG_TEXT" in breakdown.red_flags
        assert breakdown.total_penalty == 40.0

    def test_red_flag_pattern_accents(self) -> None:
        """GIVEN 'fuera del país' with accent WHEN evaluate THEN matches regex."""
        breakdown = self.scorer().evaluate(
            Listing(
                content_hash="rf_003",
                description="Propietario fuera del país vende urgente",
                price=Decimal("200000"),
                certificado_energetico_present=True,
            ),
            zone_median=Decimal("250000"),
        )
        assert "SCAM_RED_FLAG_TEXT" in breakdown.red_flags

    def test_price_anomaly_bait_detected(self) -> None:
        """GIVEN price >30% below zone median WHEN evaluate THEN SCAM_SUSPECT_PRICE_BAIT -30."""
        breakdown = self.scorer().evaluate(
            Listing(
                content_hash="pa_001",
                description="",
                price=Decimal("150000"),  # 40% below 250k median
                certificado_energetico_present=True,
            ),
            zone_median=Decimal("250000"),
        )
        assert "SCAM_SUSPECT_PRICE_BAIT" in breakdown.red_flags
        assert breakdown.price_anomaly_detected is True
        assert breakdown.total_penalty == 30.0

    def test_price_exactly_30pct_below_not_flagged(self) -> None:
        """GIVEN price exactly 30% below median WHEN evaluate THEN no anomaly (strict >)."""
        breakdown = self.scorer().evaluate(
            Listing(
                content_hash="pa_bnd",
                description="",
                price=Decimal("175000"),  # exactly 30% below 250k
                certificado_energetico_present=True,
            ),
            zone_median=Decimal("250000"),
        )
        assert breakdown.price_anomaly_detected is False
        assert breakdown.red_flags == []

    def test_price_at_median_no_anomaly(self) -> None:
        """GIVEN price at median WHEN evaluate THEN no price flag."""
        breakdown = self.scorer().evaluate(
            Listing(
                content_hash="pa_med",
                description="",
                price=Decimal("250000"),
                certificado_energetico_present=True,
            ),
            zone_median=Decimal("250000"),
        )
        assert breakdown.price_anomaly_detected is False

    def test_missing_energy_cert_false(self) -> None:
        """GIVEN certificado_energetico_present=False WHEN evaluate THEN MISSING_ENERGY_CERT -10."""
        breakdown = self.scorer().evaluate(
            Listing(
                content_hash="mc_001",
                description="",
                price=Decimal("200000"),
                certificado_energetico_present=False,
            ),
            zone_median=Decimal("250000"),
        )
        assert "MISSING_ENERGY_CERT" in breakdown.red_flags
        assert breakdown.missing_cert_detected is True
        assert breakdown.total_penalty == 10.0

    def test_missing_energy_cert_none(self) -> None:
        """GIVEN certificado_energetico_present=None WHEN evaluate THEN flagged (unknown)."""
        breakdown = self.scorer().evaluate(
            Listing(
                content_hash="mc_002",
                description="",
                price=Decimal("200000"),
                certificado_energetico_present=None,
            ),
            zone_median=Decimal("250000"),
        )
        assert breakdown.missing_cert_detected is True

    def test_clean_listing_no_flags(self) -> None:
        """GIVEN clean listing WHEN evaluate THEN no flags, zero penalty, risk 0."""
        breakdown = self.scorer().evaluate(
            Listing(
                content_hash="clean_001",
                description="Piso luminoso con terraza, reformado",
                price=Decimal("200000"),
                certificado_energetico_present=True,
            ),
            zone_median=Decimal("250000"),
        )
        assert breakdown.red_flags == []
        assert breakdown.total_penalty == 0.0
        assert breakdown.risk_score == 0.0
        assert breakdown.price_anomaly_detected is False
        assert breakdown.missing_cert_detected is False

    def test_whatsapp_only_seller_penalty_not_drop(self) -> None:
        """GIVEN WhatsApp-only contact WHEN evaluate THEN penalty, listing never dropped."""
        breakdown = self.scorer().evaluate(
            Listing(
                content_hash="wa_001",
                description="Se atiende solo whatsapp, no hay teléfono",
                price=Decimal("200000"),
                certificado_energetico_present=True,
            ),
            zone_median=Decimal("250000"),
        )
        assert "SCAM_RED_FLAG_TEXT" in breakdown.red_flags
        assert breakdown.total_penalty == 40.0
        # Scorer returns a risk breakdown, not an exclusion decision.
        assert 0.0 <= breakdown.risk_score <= 100.0

    def test_cumulative_penalties(self) -> None:
        """GIVEN red flag + price bait + missing cert WHEN evaluate THEN total = 80."""
        breakdown = self.scorer().evaluate(
            Listing(
                content_hash="cum_001",
                description="Pago por bizum, reserva antes de visitar",
                price=Decimal("100000"),  # 60% below median
                certificado_energetico_present=None,
            ),
            zone_median=Decimal("250000"),
        )
        assert breakdown.total_penalty == 80.0
        assert set(breakdown.red_flags) == {
            "SCAM_RED_FLAG_TEXT",
            "SCAM_SUSPECT_PRICE_BAIT",
            "MISSING_ENERGY_CERT",
        }

    def test_custom_weights_from_config(self) -> None:
        """GIVEN custom scam_weights WHEN evaluate THEN penalty uses configured weight."""
        config = BuyerProtectionConfig(
            scam_weights={"red_flag_text": 25.0, "price_bait": 15.0, "missing_cert": 5.0}
        )
        breakdown = ScamRiskScorer(config).evaluate(
            Listing(
                content_hash="cw_001",
                description="Pago por bizum",
                price=Decimal("150000"),
                certificado_energetico_present=False,
            ),
            zone_median=Decimal("250000"),
        )
        assert breakdown.total_penalty == 25.0 + 15.0 + 5.0

    def test_multiple_phrase_matches_single_penalty(self) -> None:
        """GIVEN several red phrases WHEN evaluate THEN one flag, one -40 penalty."""
        breakdown = self.scorer().evaluate(
            Listing(
                content_hash="mp_001",
                description="solo whatsapp, pago por bizum, reserva antes de visitar",
                price=Decimal("200000"),
                certificado_energetico_present=True,
            ),
            zone_median=Decimal("250000"),
        )
        assert breakdown.red_flags.count("SCAM_RED_FLAG_TEXT") == 1
        assert breakdown.total_penalty == 40.0


class TestScamRiskScorerNoPrice:
    """ScamRiskScorer behaviour with missing price."""

    def test_no_price_no_anomaly(self) -> None:
        """GIVEN price None WHEN evaluate THEN no price-anomaly flag."""
        breakdown = TestScamRiskScorer.scorer().evaluate(
            Listing(
                content_hash="np_001",
                description="",
                certificado_energetico_present=True,
            ),
            zone_median=Decimal("250000"),
        )
        assert breakdown.price_anomaly_detected is False
        assert breakdown.red_flags == []
