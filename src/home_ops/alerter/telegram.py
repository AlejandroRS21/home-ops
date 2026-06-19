"""Telegram alert sender with score-gating.

The ``TelegramAlerter`` sends property-listing alerts and failure
notifications to a configured Telegram chat.  Messages are gated by a
minimum score threshold so that only truly interesting listings reach
the user.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from home_ops.models.schema import Listing

logger = logging.getLogger(__name__)


class TelegramAlerter:
    """Send listing alerts and failure notifications via Telegram.

    Usage::

        alerter = TelegramAlerter()
        alerter.send_alert(listing, score=85.0)
    """

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        score_threshold: float = 70.0,
    ) -> None:
        """Initialise the alerter and load Telegram credentials from the environment.

        Args:
            bot_token: Telegram bot token.  Falls back to
                       ``TELEGRAM_BOT_TOKEN`` env var when ``None``.
            chat_id: Target chat ID.  Falls back to ``CHAT_ID`` env var
                     when ``None``.
            score_threshold: Minimum score required for an alert to be sent.
                             Defaults to 70.0.
        """
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("CHAT_ID", "")
        self.score_threshold = score_threshold

        if not self.bot_token:
            logger.warning(
                "TELEGRAM_BOT_TOKEN not set — TelegramAlerter will be a no-op."
            )
        if not self.chat_id:
            logger.warning(
                "CHAT_ID not set — TelegramAlerter will be a no-op."
            )

        # Initialise the telegram Bot directly (lazy — no network I/O)
        self._app: Any = None
        if self.bot_token:
            try:
                from telegram import Bot  # noqa: PLC0415

                self._app = Bot(token=self.bot_token)
            except Exception:
                logger.exception("Failed to create Telegram Bot")
                self._app = None

    def send_alert(self, listing: Listing, score: float, flags: list[str] | None = None) -> bool:
        """Send a Telegram message about a scored listing.

        Args:
            listing: The listing to notify about.
            score: The combined score for this listing (0–100).
            flags: Optional scoring flags (warnings) to include in the message.

        Returns:
            True if the message was sent (or would have been sent when
            credentials are missing), False on failure.
        """
        if not self._app:
            logger.info(
                "Telegram app not available — skipping alert for %s",
                listing.url,
            )
            return True  # Silently accept when credentials are missing

        message = self._format_listing_message(listing, score, flags)
        try:
            self._run_sync(self._app.send_message(chat_id=self.chat_id, text=message))
            logger.info("Alert sent for %s (score=%.1f)", listing.url, score)
            return True
        except Exception:
            logger.exception("Failed to send Telegram alert for %s", listing.url)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _run_sync(coro: Any) -> Any:
        """Run a coroutine synchronously using asyncio.run."""
        return asyncio.run(coro)

    @staticmethod
    def _format_listing_message(
        listing: Listing, score: float, flags: list[str] | None = None
    ) -> str:
        """Format a listing as a human-readable Telegram message.

        Args:
            listing: The listing to format.
            score: The computed score.
            flags: Optional scoring flags to include as warnings.

        Returns:
            A plain-text message suitable for ``send_message``.
        """
        parts = [
            f"🏠 *{listing.address or 'Property'}*",
            f"💰 {listing.price or 'N/A'} €",
            f"📐 {listing.m2 or '?'} m² · {listing.floor or '?'}ª planta",
            f"⭐ Score: {score:.0f}/100",
        ]
        if flags:
            parts.append(f"⚠️ {' · '.join(flags)}")
        if listing.url:
            parts.append(f"🔗 {listing.url}")
        return "\n".join(parts)
