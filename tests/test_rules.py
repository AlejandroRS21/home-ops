"""Tests for the deterministic scoring engine."""

from decimal import Decimal

from home_ops.models.schema import Listing
from home_ops.scorer.rules import DeterministicScorer


class TestDeterministicScorer:
    """DeterministicScorer tests."""

    def test_score_returns_zero_stub(self) -> None:
        """GIVEN any listing WHEN score called THEN returns 0.0 (placeholder)."""
        scorer = DeterministicScorer()
        listing = Listing(content_hash="test", address="Calle Test", price=Decimal("200000"))
        score = scorer.score(listing)
        assert score == 0.0

    def test_score_is_float(self) -> None:
        """GIVEN scorer instance WHEN score called THEN returns float."""
        scorer = DeterministicScorer()
        listing = Listing(content_hash="abc")
        score = scorer.score(listing)
        assert isinstance(score, float)

    def test_score_in_range(self) -> None:
        """GIVEN scorer instance WHEN score called THEN value is 0-100."""
        scorer = DeterministicScorer()
        listing = Listing(content_hash="abc")
        score = scorer.score(listing)
        assert 0.0 <= score <= 100.0
