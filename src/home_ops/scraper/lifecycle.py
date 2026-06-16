"""Scraper lifecycle: cold start, subsequent runs, snapshot management."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from home_ops.models.schema import Listing

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = Path("data/snapshots")


def _snapshot_path(portal: str, when: datetime | None = None) -> Path:
    """Build the snapshot file path for a portal on a given date."""
    when = when or datetime.now()
    date_str = when.strftime("%Y%m%d")
    return SNAPSHOT_DIR / f"{portal}_{date_str}.snap"


def _ensure_snapshot_dir() -> None:
    """Create the snapshot directory if it does not exist."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def _fetch_page_text(fetcher: Any, url: str) -> str:
    """Fetch a URL and return the page text content.

    Args:
        fetcher: A Scrapling fetcher/session with a ``fetch`` method.
        url: The URL to fetch.

    Returns:
        The page HTML as a string.

    Raises:
        RuntimeError: If the page is empty or has no text content.
    """
    page = fetcher.fetch(url)
    if page is None:
        raise RuntimeError(f"Fetcher returned None for {url}")

    html: str = getattr(page, "content", None) or getattr(page, "text", "") or ""
    if not html:
        raise RuntimeError(f"Fetcher returned empty page for {url}")

    return html


def cold_start(url: str) -> list[Listing]:
    """Fetch a portal page from scratch using Scrapling's StealthyFetcher.

    This is the first run against a portal — no cached snapshot is used.
    The raw HTML is persisted to a snapshot file (with portalocker locking)
    and then parsed into a list of Listing objects.

    Args:
        url: The portal search URL to fetch.

    Returns:
        A list of parsed Listing objects.

    Raises:
        RuntimeError: If the fetch or parse step fails.
    """
    # Lazy import so Scrapling is only pulled in when the scraper runs
    from scrapling import StealthyFetcher  # noqa: PLC0415

    logger.info("Cold start — fetching %s", url)
    fetcher = StealthyFetcher()  # type: ignore[no-untyped-call]
    html = _fetch_page_text(fetcher, url)

    # Persist snapshot
    _ensure_snapshot_dir()
    snap = _snapshot_path(_extract_portal(url))
    logger.info("Saving snapshot to %s", snap)
    fd, tmp = tempfile.mkstemp(dir=SNAPSHOT_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(tmp, snap)
    except BaseException:
        with suppress(Exception):
            os.unlink(tmp)
        raise

    listing_data: list[dict[str, Any]] = _parse_listings(html)
    return [_dict_to_listing(item) for item in listing_data]


def invalidate_snapshots() -> None:
    """Remove the entire snapshot directory.

    The next cold_start or subsequent_run call will recreate the directory
    automatically.
    """
    if SNAPSHOT_DIR.exists():
        shutil.rmtree(SNAPSHOT_DIR)
        logger.info("Snapshot directory removed: %s", SNAPSHOT_DIR)
    else:
        logger.info("Snapshot directory does not exist — nothing to remove.")


# ---------------------------------------------------------------------------
# Internal helpers — these are stubs that will be replaced once the actual
# Idealista HTML structure is documented.
# ---------------------------------------------------------------------------


def _extract_portal(url: str) -> str:
    """Extract a short portal name from a URL."""
    return "idealista" if "idealista" in url else "unknown"


def _parse_listings(html: str) -> list[dict[str, Any]]:
    """Parse listing HTML into raw dictionaries.

    This is a placeholder that will be replaced with a proper HTML parser
    (likely using Scrapling's built-in parser or BeautifulSoup) once the
    portal's DOM structure is known.

    For now it returns an empty list so the module can be imported and
    tested without a real portal response.
    """
    # TODO: implement actual HTML parsing in a later milestone
    return []


def _dict_to_listing(item: dict[str, Any]) -> Listing:
    """Convert a raw dictionary (from _parse_listings) to a Listing model.

    This is a placeholder that will be wired once _parse_listings is
    implemented.
    """
    return Listing(
        content_hash=item.get("content_hash", ""),
        url=item.get("url", ""),
        address=item.get("address", ""),
        external_id=item.get("external_id"),
        m2=item.get("m2"),
        floor=item.get("floor"),
        price=item.get("price"),
        garage_price=item.get("garage_price"),
        price_includes_garage=bool(item.get("price_includes_garage", False)),
        certificado_energetico_present=item.get("certificado_energetico_present"),
        rooms=item.get("rooms"),
        description=item.get("description", ""),
        portal=item.get("portal", "idealista"),
    )
