"""Tests for Pydantic schema models."""

from decimal import Decimal

import pytest

from home_ops.models.schema import Config, Listing, ScheduleConfig


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

    def test_alert_schedule_default(self) -> None:
        """GIVEN no alert_schedule WHEN creating Config THEN default ScheduleConfig used."""
        cfg = Config()
        assert cfg.alert_schedule is not None
        assert cfg.alert_schedule.mode == "daily"
        assert cfg.alert_schedule.daily_time == "09:00"
        assert cfg.alert_schedule.interval_hours == 6
        assert cfg.alert_schedule.timezone == "Europe/Madrid"
        assert cfg.alert_schedule.max_alerts_per_day == 5


class TestScheduleConfig:
    """ScheduleConfig model tests."""

    def test_defaults(self) -> None:
        """GIVEN no args WHEN creating ScheduleConfig THEN defaults are set."""
        sched = ScheduleConfig()
        assert sched.mode == "daily"
        assert sched.daily_time == "09:00"
        assert sched.interval_hours == 6
        assert sched.timezone == "Europe/Madrid"
        assert sched.max_alerts_per_day == 5

    def test_custom_values(self) -> None:
        """GIVEN custom values WHEN creating ScheduleConfig THEN values match."""
        sched = ScheduleConfig(
            mode="interval",
            daily_time="14:30",
            interval_hours=12,
            timezone="America/New_York",
            max_alerts_per_day=10,
        )
        assert sched.mode == "interval"
        assert sched.daily_time == "14:30"
        assert sched.interval_hours == 12
        assert sched.timezone == "America/New_York"
        assert sched.max_alerts_per_day == 10

    def test_invalid_timezone_raises_error(self) -> None:
        """GIVEN invalid timezone string WHEN creating ScheduleConfig THEN ValueError."""
        with pytest.raises(ValueError, match="timezone"):
            ScheduleConfig(timezone="Invalid/Zone")

    def test_invalid_mode_raises_error(self) -> None:
        """GIVEN invalid mode WHEN creating ScheduleConfig THEN validation error."""
        with pytest.raises(ValueError, match="mode"):
            ScheduleConfig(mode="weekly")  # type: ignore[arg-type]

    def test_interval_mode_accepts_float_hours(self) -> None:
        """GIVEN interval_hours as float WHEN creating ScheduleConfig THEN stores correctly."""
        sched = ScheduleConfig(mode="interval", interval_hours=1.5)
        assert sched.interval_hours == 1.5

    @pytest.mark.parametrize("bad_timezone", ["", "UTC/Invalid", "Europe/", "123"])
    def test_various_bad_timezones_rejected(self, bad_timezone: str) -> None:
        """GIVEN various bad timezone strings WHEN validating THEN ValueError."""
        with pytest.raises(ValueError, match="timezone"):
            ScheduleConfig(timezone=bad_timezone)
