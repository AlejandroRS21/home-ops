"""Scraper lifecycle: cold start, subsequent runs, snapshot management."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode, urlparse, urlunparse

from home_ops.models.schema import Listing
from home_ops.scraper.dedup import compute_content_hash
from home_ops.scraper.parse import parse_listings

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

    return cast(str, page.body.decode("utf-8"))


def _dicts_to_listings(dicts: list[dict[str, Any]], zone: str) -> list[Listing]:
    """Convert raw listing dicts (from parse_listings) to Listing objects.

    Each dict gets a ``content_hash`` computed from the portal, zone, m², and
    floor fields.
    """
    results: list[Listing] = []
    for item in dicts:
        portal = item.get("portal", "idealista")
        m2 = item.get("m2")
        floor = item.get("floor")
        ch = compute_content_hash(portal, zone, m2, floor)
        results.append(
            Listing(
                content_hash=ch,
                url=item.get("url", ""),
                address=item.get("address", ""),
                external_id=item.get("external_id"),
                m2=m2,
                floor=floor,
                price=item.get("price"),
                garage_price=item.get("garage_price"),
                price_includes_garage=bool(item.get("price_includes_garage", False)),
                certificado_energetico_present=item.get("certificado_energetico_present"),
                rooms=item.get("rooms"),
                description=item.get("description", ""),
                portal=portal,
            )
        )
    return results


def _save_snapshot(snap: Path, html: str) -> None:
    """Persist raw HTML to a snapshot file atomically."""
    _ensure_snapshot_dir()
    fd, tmp = tempfile.mkstemp(dir=SNAPSHOT_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(tmp, snap)
    except BaseException:
        with suppress(Exception):
            os.unlink(tmp)
        raise


# Module-level cache for Scrapling's StealthyFetcher.
# Loaded lazily so external code can patch it before cold_start runs.
_StealthyFetcher: Any = None


def _get_fetcher() -> Any:
    """Return a StealthyFetcher instance, loading Scrapling on first call."""
    global _StealthyFetcher  # noqa: PLW0603
    if _StealthyFetcher is None:
        from scrapling import StealthyFetcher  # noqa: PLC0415

        _StealthyFetcher = StealthyFetcher
    return _StealthyFetcher()


def cold_start(url: str, zone: str = "", max_pages: int = 5) -> list[Listing]:
    """Fetch a portal page from scratch using Scrapling's StealthyFetcher.

    Iterates over paginated search results up to ``max_pages`` pages.
    Page 1 uses the base URL; pages 2+ append ``?pagina=N``.
    Stops early if a page returns zero listings.

    The raw HTML from the first page is persisted to a snapshot file.

    Args:
        url: The portal search URL to fetch.
        zone: Neighbourhood or area name for content-hash computation.
        max_pages: Maximum number of pages to fetch.

    Returns:
        A list of parsed Listing objects aggregated across all pages.

    Raises:
        RuntimeError: If the initial fetch or parse step fails.
    """
    logger.info("Cold start — fetching %s (max %d pages)", url, max_pages)

    fetcher = _get_fetcher()
    portal = _extract_portal(url)
    snap = _snapshot_path(portal)
    first_page = True

    all_listings: list[Listing] = []
    page_num = 0

    for page_num in range(1, max_pages + 1):
        if page_num == 1:
            page_url = url
        else:
            parsed = urlparse(url)
            query = dict(kv.split("=", 1) for kv in parsed.query.split("&") if kv)
            query["pagina"] = str(page_num)
            parsed = parsed._replace(query=urlencode(query))
            page_url = urlunparse(parsed)
        logger.info("Fetching page %d: %s", page_num, page_url)

        try:
            html = _fetch_page_text(fetcher, page_url)
        except RuntimeError as exc:
            logger.warning("Page %d fetch failed: %s — stopping pagination", page_num, exc)
            break

        raw_dicts = parse_listings(html)
        logger.info("Page %d: found %d listings", page_num, len(raw_dicts))

        if first_page:
            _save_snapshot(snap, html)
            first_page = False

        if not raw_dicts:
            logger.info("Page %d returned 0 listings — stopping pagination", page_num)
            break

        listings = _dicts_to_listings(raw_dicts, zone)
        all_listings.extend(listings)

    logger.info(
        "Cold start complete — %d total listings across %d pages",
        len(all_listings),
        page_num,
    )
    return all_listings


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
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_portal(url: str) -> str:
    """Extract a short portal name from a URL."""
    return "idealista" if "idealista" in url else "unknown"
