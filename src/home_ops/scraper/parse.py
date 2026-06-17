"""Idealista search-result HTML parser using Scrapling Selector.

Public interface:
    parse_listings(html: str) -> list[dict[str, Any]]
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from scrapling.parser import Selector

_EXTERNAL_ID_RE = re.compile(r"/inmueble/(\d+)/")
_M2_RE = re.compile(r"(\d+(?:\.\d+)?)\s*m[²2]")
_ROOMS_RE = re.compile(r"(\d+)\s*hab\.")
_FLOOR_RE = re.compile(
    r"^\s*(planta\s+\d+(?:ª|º)?|bajo|ático|sótano|entresuelo|entreplanta)",
    re.IGNORECASE,
)

logger = __import__("logging").getLogger(__name__)


def _skip_sponsored(tag: dict[str, Any]) -> bool:
    """Return True if the card should be skipped (sponsored).

    Sponsored cards have either a ``data-adid`` attribute or a ``premium`` CSS class.
    """
    attrib: dict[str, str] = tag.get("attrib", {})
    if attrib.get("data-adid"):
        return True
    class_str: str = attrib.get("class", "") or ""
    return "premium" in class_str.split()


def _extract_price(text: str | None) -> Decimal | None:
    """Extract a Decimal price from an Idealista price string.

    Handles Spanish formats like ``150.000€`` (dots = thousand separators),
    ``150.500,50€`` (comma = decimal separator), and whitespace.
    Returns ``None`` for ``Consultar`` or empty/missing text.
    """
    if not text or not text.strip():
        return None
    cleaned = text.strip()
    if cleaned.lower().startswith("consultar"):
        return None
    # Remove currency symbol and whitespace
    cleaned = cleaned.replace("€", "").strip()
    if not cleaned:
        return None
    # Spanish format: dots are thousand separators, comma is decimal
    # Step 1: remove all dots (thousands separators)
    cleaned = cleaned.replace(".", "")
    # Step 2: replace comma with dot (decimal separator)
    cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _extract_m2(text: str | None) -> float | None:
    """Extract surface area in m² from text like ``80 m²``."""
    if not text:
        return None
    m = _M2_RE.search(text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _extract_rooms(text: str | None) -> int | None:
    """Extract room count from text like ``3 hab.``."""
    if not text:
        return None
    m = _ROOMS_RE.search(text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _extract_floor(text: str | None) -> str | None:
    """Extract floor info from text like ``planta 4ª`` or ``Bajo``."""
    if not text:
        return None
    m = _FLOOR_RE.match(text)
    if m:
        return m.group(1)
    return None


def _extract_external_id(href: str | None) -> str | None:
    """Extract the numeric listing ID from an Idealista href.

    Matches the ``/inmueble/XXXXX/`` pattern.
    """
    if not href:
        return None
    m = _EXTERNAL_ID_RE.search(href)
    if m:
        return m.group(1)
    return None


def _extract_text_recursive(el: Selector) -> str:
    """Extract all text content from a Selector element recursively."""
    pieces: list[str] = []
    for child in el.children:
        if child.tag is None:
            pieces.append(str(child.text or ""))
        else:
            pieces.append(_extract_text_recursive(child))
    return " ".join(pieces).strip()


def _get_detail_texts(card: Selector) -> list[str]:
    """Extract detail texts from the item-detail-char div."""
    texts: list[str] = []
    detail_div = card.css("div.item-detail-char")
    if not detail_div:
        return texts
    # Try direct span children
    for span in detail_div[0].css("span"):
        t = span.css("::text").get("")
        if t:
            texts.append(t.strip())
    # If no span children found, try direct text
    if not texts:
        raw = _extract_text_recursive(detail_div[0])
        if raw:
            texts = [raw]
    return texts


def parse_listings(html: str) -> list[dict[str, Any]]:
    """Parse Idealista search-result HTML into a list of raw listing dicts.

    Args:
        html: The raw HTML of a search results page.

    Returns:
        A list of dicts, one per organic (non-sponsored) listing.
        Each dict contains: external_id, url, address, price, m2, rooms,
        floor, description, portal, price_includes_garage, garage_price,
        certificado_energetico_present.
    """
    if not html or not html.strip():
        return []

    page = Selector(html)
    cards = page.css("article.item")
    if not cards:
        return []

    results: list[dict[str, Any]] = []
    sponsored_count = 0
    for card in cards:
        tag = _selector_tag(card)
        if _skip_sponsored(tag):
            sponsored_count += 1
            continue

        link_el = card.css("a.item-link")
        href = link_el[0].css("::attr(href)").get("") if link_el else ""

        external_id = _extract_external_id(href)
        url = href
        address = link_el[0].css("::attr(title)").get("") if link_el else ""

        price_el = card.css("span.item-price")
        price_text = price_el[0].css("::text").get("") if price_el else ""
        price = _extract_price(price_text)

        # Extract detail characteristics (ordered: rooms, m2, floor)
        detail_texts = _get_detail_texts(card)
        m2: float | None = None
        rooms: int | None = None
        floor: str | None = None
        for dt in detail_texts:
            if m2 is None:
                m2 = _extract_m2(dt)
            if rooms is None:
                rooms = _extract_rooms(dt)
            if floor is None:
                floor = _extract_floor(dt)

        desc_el = card.css("div.item-description")
        description = desc_el[0].css("::text").get("") if desc_el else ""

        # Garage detection
        parking = card.css("span.item-parking")
        price_includes_garage = len(parking) > 0

        results.append(
            {
                "external_id": external_id,
                "url": url,
                "address": address,
                "price": price,
                "m2": m2,
                "rooms": rooms,
                "floor": floor,
                "description": description,
                "portal": "idealista",
                "price_includes_garage": price_includes_garage,
                "garage_price": None,
                "certificado_energetico_present": None,
            }
        )

    if sponsored_count:
        logger.info("Skipped %d sponsored listing(s)", sponsored_count)

    return results


def _selector_tag(sel: Selector) -> dict[str, Any]:
    """Extract a minimal tag dict from a Scrapling Selector for use with _skip_sponsored.

    Scrapling Selectors expose attributes via the ``attrib`` property (LXML-style).
    """
    return {"attrib": dict(sel.attrib)}
