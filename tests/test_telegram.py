"""Tests for the Telegram alerter module."""

from decimal import Decimal

from home_ops.alerter.telegram import TelegramAlerter
from home_ops.models.schema import Listing


class TestTelegramAlerter:
    """TelegramAlerter tests."""

    def test_init_without_token(self) -> None:
        """GIVEN no bot token WHEN init THEN no crash, warning logged."""
        alerter = TelegramAlerter(bot_token="", chat_id="-123")
        assert alerter.bot_token == ""

    def test_init_with_token(self) -> None:
        """GIVEN valid bot token WHEN init THEN app not None."""
        alerter = TelegramAlerter(bot_token="123:abc", chat_id="-456")
        assert alerter.bot_token == "123:abc"
        assert alerter.chat_id == "-456"

    def test_default_score_threshold(self) -> None:
        """GIVEN default init WHEN accessed THEN score_threshold is 70.0."""
        alerter = TelegramAlerter(bot_token="test", chat_id="test")
        assert alerter.score_threshold == 70.0

    def test_custom_score_threshold(self) -> None:
        """GIVEN custom score_threshold WHEN init THEN threshold applied."""
        alerter = TelegramAlerter(bot_token="test", chat_id="test", score_threshold=85.0)
        assert alerter.score_threshold == 85.0

    def test_score_gate_below_threshold(self) -> None:
        """GIVEN score < threshold WHEN _score_gate THEN False."""
        alerter = TelegramAlerter(bot_token="test", chat_id="test", score_threshold=70.0)
        assert alerter._score_gate(50.0) is False

    def test_score_gate_at_threshold(self) -> None:
        """GIVEN score == threshold WHEN _score_gate THEN True."""
        alerter = TelegramAlerter(bot_token="test", chat_id="test", score_threshold=70.0)
        assert alerter._score_gate(70.0) is True

    def test_score_gate_above_threshold(self) -> None:
        """GIVEN score > threshold WHEN _score_gate THEN True."""
        alerter = TelegramAlerter(bot_token="test", chat_id="test", score_threshold=70.0)
        assert alerter._score_gate(95.0) is True

    def test_score_gate_with_custom_threshold(self) -> None:
        """GIVEN custom threshold arg WHEN _score_gate THEN uses it."""
        alerter = TelegramAlerter(bot_token="test", chat_id="test", score_threshold=70.0)
        assert alerter._score_gate(60.0, threshold=50.0) is True

    def test_send_alert_gated(self) -> None:
        """GIVEN score below threshold WHEN send_alert THEN returns False."""
        alerter = TelegramAlerter(bot_token="test", chat_id="test", score_threshold=70.0)
        listing = Listing(content_hash="abc", url="https://test.com")
        result = alerter.send_alert(listing, score=50.0)
        assert result is False

    def test_send_alert_without_credentials(self) -> None:
        """GIVEN no credentials but score passes gate WHEN send_alert THEN returns True."""
        alerter = TelegramAlerter(bot_token="", chat_id="", score_threshold=50.0)
        listing = Listing(content_hash="abc", url="https://test.com")
        result = alerter.send_alert(listing, score=80.0)
        assert result is True

    def test_send_failure_alert_without_credentials(self) -> None:
        """GIVEN no credentials WHEN send_failure_alert THEN returns True."""
        alerter = TelegramAlerter(bot_token="", chat_id="")
        result = alerter.send_failure_alert("Test failure")
        assert result is True

    def test_format_listing_message(self) -> None:
        """GIVEN listing and score WHEN _format_listing_message THEN formatted string."""
        listing = Listing(
            content_hash="abc",
            url="https://test.com/listing",
            address="Calle Test 123",
            price=Decimal("250000.00"),
            m2=85.0,
            floor="3B",
        )
        message = TelegramAlerter._format_listing_message(listing, 85.5)
        assert "Calle Test 123" in message
        assert "250000" in message
        assert "85" in message
        assert "3B" in message
        assert "test.com" in message
