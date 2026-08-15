"""Tests for the Telegram alerter module."""

import logging
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

from home_ops.alerter.telegram import TelegramAlerter
from home_ops.models.schema import Listing
from home_ops.scorer.models import AcquisitionCostBreakdown


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

    def test_send_alert_without_credentials(self) -> None:
        """GIVEN no credentials WHEN send_alert THEN returns False and logs error."""
        alerter = TelegramAlerter(bot_token="", chat_id="", score_threshold=50.0)
        listing = Listing(content_hash="abc", url="https://test.com")
        result = alerter.send_alert(listing, score=80.0)
        assert result is False

    def test_send_alert_missing_chat_id(self) -> None:
        """GIVEN missing chat_id WHEN send_alert THEN returns False."""
        alerter = TelegramAlerter(bot_token="123:xyz", chat_id="", score_threshold=50.0)
        listing = Listing(content_hash="abc", url="https://test.com")
        result = alerter.send_alert(listing, score=80.0)
        assert result is False

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

    def test_format_listing_message_with_flags(self) -> None:
        """GIVEN listing with flags WHEN formatted THEN includes warning line."""
        listing = Listing(
            content_hash="def",
            url="https://test.com/other",
            address="Calle Otra 456",
            price=Decimal("180000.00"),
            m2=60.0,
        )
        flags = ["certificado_missing"]
        message = TelegramAlerter._format_listing_message(listing, 70.0, flags)
        assert "certificado_missing" in message
        assert "⚠️" in message

    def test_send_alert_with_flags(self) -> None:
        """GIVEN flags WHEN send_alert THEN returns False when credentials missing."""
        alerter = TelegramAlerter(bot_token="", chat_id="", score_threshold=50.0)
        listing = Listing(content_hash="ghi", url="https://test.com/flags")
        result = alerter.send_alert(listing, score=75.0, flags=["certificado_missing"])
        assert result is False


class TestBuyerProtectionMessage:
    """Scenario 3.1/3.2 — checklist + financial reality rendered in alerts."""

    @staticmethod
    def _listing() -> Listing:
        return Listing(
            content_hash="bp_msg_001",
            url="https://test.com/bp",
            address="Calle Buyer 7",
            price=Decimal("250000.00"),
            m2=85.0,
        )

    @staticmethod
    def _cost_breakdown() -> AcquisitionCostBreakdown:
        """Resale 250k: ITP 8% (20000) + notary 1.5% (3750) = 273750 total."""
        return AcquisitionCostBreakdown(
            purchase_price=Decimal("250000.00"),
            property_type="resale",
            tax_type="ITP",
            tax_rate=0.08,
            tax_amount=Decimal("20000.00"),
            notary_registry_fee=Decimal("3750.00"),
            total_acquisition_cost=Decimal("273750.00"),
            monthly_mortgage_payment=Decimal("1077.71"),
            mortgage_effort_ratio=0.539,
            high_effort_flag=True,
        )

    def test_alert_renders_five_checks(self) -> None:
        """GIVEN cost breakdown WHEN formatted THEN message contains 5 checklist checks."""
        message = TelegramAlerter._format_listing_message(
            self._listing(), 85.0, cost_breakdown=self._cost_breakdown()
        )
        assert "Checklist comprador" in message
        assert "Nota Simple" in message
        assert "deuda cero" in message.lower()
        assert "notario independiente" in message.lower()
        assert "38/1999" in message
        assert "plusval" in message.lower()

    def test_alert_renders_itemized_outlay(self) -> None:
        """GIVEN cost breakdown WHEN formatted THEN price + taxes + notary = total."""
        message = TelegramAlerter._format_listing_message(
            self._listing(), 85.0, cost_breakdown=self._cost_breakdown()
        )
        assert "250,000.00" in message  # listing price
        assert "20,000.00" in message  # ITP tax
        assert "3,750.00" in message  # notary + registry
        assert "273,750.00" in message  # estimated total outlay

    def test_alert_high_mortgage_effort_warning(self) -> None:
        """GIVEN high_effort_flag WHEN formatted THEN warning line present."""
        message = TelegramAlerter._format_listing_message(
            self._listing(), 85.0, cost_breakdown=self._cost_breakdown()
        )
        assert "esfuerzo" in message.lower()

    def test_alert_without_breakdown_keeps_legacy_format(self) -> None:
        """GIVEN no cost breakdown WHEN formatted THEN no new blocks (rollback path)."""
        message = TelegramAlerter._format_listing_message(self._listing(), 85.0)
        assert "Checklist comprador" not in message
        assert "Coste real de compra" not in message

    def test_send_alert_forwards_cost_breakdown_to_message(self) -> None:
        """GIVEN send_alert with cost_breakdown WHEN sent THEN message includes checklist."""
        alerter = TelegramAlerter(bot_token="123:abc", chat_id="-456")
        alerter._app = MagicMock()
        alerter._app.send_message = AsyncMock(return_value=None)

        cost = AcquisitionCostBreakdown(
            purchase_price=Decimal("100000.00"),
            property_type="new_build",
            tax_type="IVA+AJD",
            tax_rate=0.115,
            tax_amount=Decimal("11500.00"),
            notary_registry_fee=Decimal("1500.00"),
            total_acquisition_cost=Decimal("113000.00"),
            monthly_mortgage_payment=Decimal("257.31"),
            mortgage_effort_ratio=0.086,
            high_effort_flag=False,
        )
        result = alerter.send_alert(
            self._listing(), score=80.0, cost_breakdown=cost
        )
        assert result is True
        sent_text = alerter._app.send_message.call_args.kwargs["text"]
        assert "113,000.00" in sent_text  # new build total outlay
        assert "Checklist comprador" in sent_text


class TestSendAlertRetries:
    """Retry behaviour of TelegramAlerter.send_alert on transient failures."""

    @staticmethod
    def _alerter_with_send_message(side_effect: Any) -> tuple[TelegramAlerter, MagicMock]:
        """Build an alerter whose _app.send_message is a controllable AsyncMock."""
        alerter = TelegramAlerter(bot_token="123:abc", chat_id="-456")
        alerter._app = MagicMock()
        alerter._app.send_message = AsyncMock(side_effect=side_effect)
        return alerter, alerter._app.send_message

    def test_send_alert_success_first_try_no_sleep(self) -> None:
        """GIVEN send_message succeeds first try WHEN send_alert THEN True, no sleep."""
        alerter, send_message = self._alerter_with_send_message(None)
        listing = Listing(content_hash="retry_ok", url="https://test.com/ok")
        with patch("home_ops.alerter.telegram.time.sleep") as mock_sleep:
            result = alerter.send_alert(listing, score=80.0)
        assert result is True
        send_message.assert_called_once()
        mock_sleep.assert_not_called()

    def test_send_alert_timed_out_then_ok_retries_once(self) -> None:
        """GIVEN TimedOut then success WHEN send_alert THEN True and sleeps once."""
        alerter, send_message = self._alerter_with_send_message([TimedOut("timeout"), None])
        listing = Listing(content_hash="retry_once", url="https://test.com/once")
        with patch("home_ops.alerter.telegram.time.sleep") as mock_sleep:
            result = alerter.send_alert(listing, score=80.0)
        assert result is True
        assert send_message.call_count == 2
        mock_sleep.assert_called_once_with(1.0)

    def test_send_alert_retries_exhausted_returns_false(self, caplog) -> None:
        """GIVEN three transient failures WHEN send_alert THEN False, backoff sleeps, warns."""
        alerter, send_message = self._alerter_with_send_message(
            [TimedOut("t1"), TimedOut("t2"), TimedOut("t3")]
        )
        listing = Listing(content_hash="retry_exhaust", url="https://test.com/exhaust")
        with patch("home_ops.alerter.telegram.time.sleep") as mock_sleep, \
                caplog.at_level(logging.WARNING, logger="home_ops.alerter.telegram"):
            result = alerter.send_alert(listing, score=80.0)
        assert result is False
        assert send_message.call_count == 3
        assert mock_sleep.call_args_list == [call(1.0), call(2.0)]
        warn_markers = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "retrying" in r.getMessage().lower()
        ]
        assert len(warn_markers) == 2

    def test_send_alert_permanent_failure_no_retry(self) -> None:
        """GIVEN BadRequest WHEN send_alert THEN False, single call, no sleep."""
        alerter, send_message = self._alerter_with_send_message(BadRequest("bad request"))
        listing = Listing(content_hash="retry_perm", url="https://test.com/perm")
        with patch("home_ops.alerter.telegram.time.sleep") as mock_sleep:
            result = alerter.send_alert(listing, score=80.0)
        assert result is False
        send_message.assert_called_once()
        mock_sleep.assert_not_called()

    @pytest.mark.parametrize(
        "transient_exc",
        [TimedOut("timeout"), NetworkError("network"), RetryAfter(1)],
        ids=["TimedOut", "NetworkError", "RetryAfter"],
    )
    def test_send_alert_transient_failures_are_retried(self, transient_exc) -> None:
        """GIVEN a transient failure once THEN send_alert retries and returns True."""
        alerter, send_message = self._alerter_with_send_message([transient_exc, None])
        listing = Listing(content_hash="retry_transient", url="https://test.com/transient")
        with patch("home_ops.alerter.telegram.time.sleep") as mock_sleep:
            result = alerter.send_alert(listing, score=80.0)
        assert result is True
        assert send_message.call_count == 2
        mock_sleep.assert_called_once_with(1.0)
