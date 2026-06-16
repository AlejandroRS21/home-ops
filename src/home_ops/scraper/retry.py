"""Retry decorator with exponential backoff and Telegram alert on final failure.

The decorator logs each attempt and, when all attempts are exhausted,
sends a failure notification via the home_ops.alerter module.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def retry_with_backoff(
    func: F | None = None,
    *,
    max_attempts: int = 5,
    base_delay: float = 2.0,
) -> F:
    """Decorator that retries *func* with exponential backoff and jitter.

    On the final failed attempt a Telegram alert is dispatched (imported
    lazily from ``home_ops.alerter.telegram``).

    Can be used with or without arguments::

        @retry_with_backoff
        def fetch_data(url): ...

        @retry_with_backoff(max_attempts=3, base_delay=1.0)
        def fetch_data(url): ...

    Args:
        func: The callable to wrap (when used bare).
        max_attempts: Maximum number of attempts before giving up.
        base_delay: Base delay in seconds for the exponential calculation.
    """

    def decorator(f: F) -> F:
        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return f(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt < max_attempts:
                        delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                        logger.warning(
                            "Attempt %d/%d failed for %s: %s. Retrying in %.2fs…",
                            attempt,
                            max_attempts,
                            f.__name__,
                            exc,
                            delay,
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            "All %d attempts failed for %s: %s",
                            max_attempts,
                            f.__name__,
                            exc,
                        )
                        _send_failure_alert(f.__name__, str(exc))

            if last_exc is not None:
                raise last_exc
            return None  # pragma: no cover — never reached

        @functools.wraps(f)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await f(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt < max_attempts:
                        delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                        logger.warning(
                            "Attempt %d/%d failed for %s: %s. Retrying in %.2fs…",
                            attempt,
                            max_attempts,
                            f.__name__,
                            exc,
                            delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            "All %d attempts failed for %s: %s",
                            max_attempts,
                            f.__name__,
                            exc,
                        )
                        _send_failure_alert(f.__name__, str(exc))

            if last_exc is not None:
                raise last_exc
            return None  # pragma: no cover — never reached

        if asyncio.iscoroutinefunction(f):
            return async_wrapper  # type: ignore[return-value]
        return wrapper  # type: ignore[return-value]

    if func is not None:
        return decorator(func)
    return decorator  # type: ignore[return-value]


def _send_failure_alert(func_name: str, message: str) -> None:
    """Send a Telegram alert about a permanently failed operation.

    Imported lazily to avoid circular imports at module level.
    """
    try:
        from home_ops.alerter.telegram import TelegramAlerter  # noqa: PLC0415

        alerter = TelegramAlerter()
        alerter.send_failure_alert(f"Retry exhausted for {func_name}: {message}")
    except Exception:
        logger.exception("Failed to send Telegram failure alert")
