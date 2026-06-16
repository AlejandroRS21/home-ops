"""Tests for Pydantic schema models."""

from decimal import Decimal

from home_ops.models.schema import Config, Listing, PriceHistory, Snapshot


class TestListing:
    """Listing model validation tests."""

    def test_minimal_listing(self) -> None:
        """GIVEN minimal required fields WHEN creating Listing THEN succeeds."""
        listing = Listing(content_hash="abc123")
        assert listing.content_hash == "abc123"
        assert listing.portal == "idealista"
        assert listing.price_includes_garage is False

    def test_full_listing(self) -> None:
        """GIVEN all fields WHEN creating Listing THEN values match."""
        listing = Listing(
            content_hash="def456",
            external_id="ext-001",
            url="https://idealista.com/test",
            address="Calle Test 123",
            m2=85.5,
            floor="4B",
            price=Decimal("250000.00"),
            garage_price=Decimal("15000.00"),
            price_includes_garage=False,
            certificado_energetico_present=True,
            rooms=3,
            description="Beautiful apartment",
            portal="idealista",
        )
        assert listing.external_id == "ext-001"
        assert listing.price == Decimal("250000.00")
        assert listing.certificado_energetico_present is True

    def test_content_hash_required(self) -> None:
        """GIVEN no content_hash WHEN creating Listing THEN error."""
        import pytest

        with pytest.raises((TypeError, ValueError)):
            Listing()  # type: ignore[call-arg]


class TestSnapshot:
    """Snapshot model tests."""

    def test_minimal_snapshot(self) -> None:
        """GIVEN minimal snapshot WHEN created THEN defaults set."""
        snap = Snapshot()
        assert snap.file_path == ""


class TestPriceHistory:
    """PriceHistory model tests."""

    def test_minimal_price(self) -> None:
        """GIVEN listing_id and price WHEN created THEN values match."""
        ph = PriceHistory(listing_id=1, price=Decimal("250000.00"))
        assert ph.listing_id == 1
        assert ph.price == Decimal("250000.00")


class TestConfig:
    """Config model tests."""

    def test_default_config(self) -> None:
        """GIVEN no args WHEN creating Config THEN defaults are set."""
        cfg = Config()
        assert cfg.hitl_approval_required is True
        assert cfg.euribor_rate == 3.5
        assert cfg.telegram_chat_id == ""

    def test_custom_config(self) -> None:
        """GIVEN custom values WHEN creating Config THEN values match."""
        cfg = Config(
            portal_url="https://test.url",
            hitl_approval_required=False,
            euribor_rate=2.0,
            telegram_chat_id="-123456789",
        )
        assert cfg.portal_url == "https://test.url"
        assert cfg.hitl_approval_required is False
        assert cfg.euribor_rate == 2.0
        assert cfg.telegram_chat_id == "-123456789"
