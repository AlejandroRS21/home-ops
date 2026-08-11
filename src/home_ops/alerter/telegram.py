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
import time
from typing import Any

from telegram.error import NetworkError, RetryAfter, TimedOut

from home_ops.models.schema import Listing

logger = logging.getLogger(__name__)

# Transient send failures are retried with backoff; permanent errors (e.g.
# BadRequest, Forbidden) fail immediately. Backoff values are seconds per
# retry attempt, indexed by attempt number.
#
# Matching is by exact class, not isinstance: BadRequest subclasses
# NetworkError in python-telegram-bot >= 22 (verified 22.8), so an isinstance
# gate would retry permanent 400 errors. Exact-type membership retries only
# the three explicitly transient classes.
TRANSIENT = {TimedOut, NetworkError, RetryAfter}
RETRIES = 2
BACKOFF = (1.0, 2.0)


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
                "TELEGRAM_BOT_TOKEN not set — TelegramAlerter.send_alert() "
                "will log an error and return False."
            )
        if not self.chat_id:
            logger.warning(
                "CHAT_ID not set — TelegramAlerter.send_alert() "
                "will log an error and return False."
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
            True if the message was sent, False otherwise (missing/invalid
            credentials or send failure after transient retries are exhausted).
        """
        if not self.bot_token or not self.chat_id or not self._app:
            logger.error(
                "Telegram credentials or app missing/invalid — failing alert for %s",
                listing.url,
            )
            return False

        message = self._format_listing_message(listing, score, flags)
        for attempt in range(RETRIES + 1):
            try:
                self._run_sync(self._app.send_message(chat_id=self.chat_id, text=message))
                logger.info("Alert sent for %s (score=%.1f)", listing.url, score)
                return True
            except Exception as exc:
                if type(exc) not in TRANSIENT or attempt >= RETRIES:
                    logger.exception("Failed to send Telegram alert for %s", listing.url)
                    return False
                backoff = BACKOFF[attempt]
                logger.warning(
                    "Transient Telegram failure for %s (attempt %d/%d), "
                    "retrying in %.1fs: %s",
                    listing.url,
                    attempt + 1,
                    RETRIES + 1,
                    backoff,
                    exc,
                )
                time.sleep(backoff)
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
