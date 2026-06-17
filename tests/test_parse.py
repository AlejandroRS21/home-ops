"""Tests for the Idealista HTML parser.

NOTE: If ``test_lifecycle.py`` ran before this module, ``scrapling`` may be
mocked in ``sys.modules``. We clean it up here to ensure the real scrapling
``Selector`` is imported.
"""

import sys
from typing import Any

# Guard: remove any stale scrapling mock from test_lifecycle.py
# and force re-import of the parse module with the real Selector.
for _key in list(sys.modules):
    if "scrapling" in _key or "home_ops.scraper.parse" in _key:
        sys.modules.pop(_key, None)

from decimal import Decimal  # noqa: E402

from home_ops.scraper.parse import (  # noqa: E402
    _extract_external_id,
    _extract_floor,
    _extract_m2,
    _extract_price,
    _extract_rooms,
    _skip_sponsored,
    parse_listings,
)


class TestSkipSponsored:
    """Sponsored card detection tests."""

    def test_no_adid_no_premium(self) -> None:
        """GIVEN tag without data-adid or premium class WHEN _skip_sponsored THEN False."""
        tag: dict[str, Any] = {"attrib": {}}
        assert _skip_sponsored(tag) is False

    def test_data_adid_present(self) -> None:
        """GIVEN tag with data-adid attr WHEN _skip_sponsored THEN True."""
        tag = {"attrib": {"data-adid": "123"}}
        assert _skip_sponsored(tag) is True

    def test_premium_class(self) -> None:
        """GIVEN tag with premium class WHEN _skip_sponsored THEN True."""
        tag = {"attrib": {"class": "item premium"}}
        assert _skip_sponsored(tag) is True

    def test_both_sponsored_signals(self) -> None:
        """GIVEN tag with both sponsored signals WHEN _skip_sponsored THEN True."""
        tag = {"attrib": {"data-adid": "456", "class": "item premium"}}
        assert _skip_sponsored(tag) is True

    def test_unrelated_class(self) -> None:
        """GIVEN tag with unrelated class WHEN _skip_sponsored THEN False."""
        tag = {"attrib": {"class": "item organic"}}
        assert _skip_sponsored(tag) is False


class TestExtractPrice:
    """Price extraction tests."""

    def test_standard_price(self) -> None:
        """GIVEN '150.000€' WHEN _extract_price THEN Decimal(150000)."""
        assert _extract_price("150.000€") == Decimal("150000")

    def test_price_with_spaces(self) -> None:
        """GIVEN '  150.000 €  ' WHEN _extract_price THEN Decimal(150000)."""
        assert _extract_price("  150.000 €  ") == Decimal("150000")

    def test_price_with_commas_as_decimals(self) -> None:
        """GIVEN '150.500,50€' WHEN _extract_price THEN Decimal(150500.50)."""
        assert _extract_price("150.500,50€") == Decimal("150500.50")

    def test_consultar_returns_none(self) -> None:
        """GIVEN 'Consultar' WHEN _extract_price THEN None."""
        assert _extract_price("Consultar") is None

    def test_empty_string_returns_none(self) -> None:
        """GIVEN empty string WHEN _extract_price THEN None."""
        assert _extract_price("") is None

    def test_low_price(self) -> None:
        """GIVEN '500€' WHEN _extract_price THEN Decimal(500)."""
        assert _extract_price("500€") == Decimal("500")


class TestExtractM2:
    """M² extraction tests."""

    def test_standard_m2(self) -> None:
        """GIVEN '80 m²' WHEN _extract_m2 THEN 80.0."""
        assert _extract_m2("80 m²") == 80.0

    def test_decimal_m2(self) -> None:
        """GIVEN '85.5 m²' WHEN _extract_m2 THEN 85.5."""
        assert _extract_m2("85.5 m²") == 85.5

    def test_no_match_returns_none(self) -> None:
        """GIVEN text without m² WHEN _extract_m2 THEN None."""
        assert _extract_m2("3 hab.") is None

    def test_empty_string_returns_none(self) -> None:
        """GIVEN empty string WHEN _extract_m2 THEN None."""
        assert _extract_m2("") is None


class TestExtractRooms:
    """Room count extraction tests."""

    def test_standard_rooms(self) -> None:
        """GIVEN '3 hab.' WHEN _extract_rooms THEN 3."""
        assert _extract_rooms("3 hab.") == 3

    def test_single_room(self) -> None:
        """GIVEN '1 hab.' WHEN _extract_rooms THEN 1."""
        assert _extract_rooms("1 hab.") == 1

    def test_no_match_returns_none(self) -> None:
        """GIVEN text without hab. WHEN _extract_rooms THEN None."""
        assert _extract_rooms("80 m²") is None

    def test_empty_string_returns_none(self) -> None:
        """GIVEN empty string WHEN _extract_rooms THEN None."""
        assert _extract_rooms("") is None


class TestExtractFloor:
    """Floor extraction tests."""

    def test_standard_floor(self) -> None:
        """GIVEN 'planta 4ª' WHEN _extract_floor THEN 'planta 4ª'."""
        assert _extract_floor("planta 4ª") == "planta 4ª"

    def test_bajo(self) -> None:
        """GIVEN 'Bajo' WHEN _extract_floor THEN 'Bajo'."""
        assert _extract_floor("Bajo") == "Bajo"

    def test_exterior(self) -> None:
        """GIVEN 'planta 1ª exterior' WHEN _extract_floor THEN 'planta 1ª'."""
        assert _extract_floor("planta 1ª exterior") == "planta 1ª"

    def test_no_match_returns_none(self) -> None:
        """GIVEN text without floor pattern WHEN _extract_floor THEN None."""
        assert _extract_floor("80 m²") is None

    def test_empty_string_returns_none(self) -> None:
        """GIVEN empty string WHEN _extract_floor THEN None."""
        assert _extract_floor("") is None


class TestExtractExternalId:
    """External ID extraction tests."""

    def test_standard_id(self) -> None:
        """GIVEN '/inmueble/98765/' WHEN _extract_external_id THEN '98765'."""
        assert _extract_external_id("/inmueble/98765/") == "98765"

    def test_id_in_full_url(self) -> None:
        """GIVEN full url with id WHEN _extract_external_id THEN id."""
        href = "https://www.idealista.com/inmueble/123456/"
        assert _extract_external_id(href) == "123456"

    def test_no_match_returns_none(self) -> None:
        """GIVEN href without inmueble pattern WHEN _extract_external_id THEN None."""
        assert _extract_external_id("/otro/123/") is None

    def test_empty_string_returns_none(self) -> None:
        """GIVEN empty string WHEN _extract_external_id THEN None."""
        assert _extract_external_id("") is None


SAMPLE_HTML = """<html><body>
<article class="item" data-element-id="1">
  <a class="item-link" href="/inmueble/100001/" title="Piso en Calle Mayor">Piso en Calle Mayor</a>
  <span class="item-price">150.000€</span>
  <div class="item-detail-char">
    <span>3 hab.</span>
    <span>80 m²</span>
    <span>planta 4ª</span>
  </div>
  <div class="item-description">Nice flat in city center</div>
</article>
<article class="item" data-element-id="2">
  <a class="item-link" href="/inmueble/100002/" title="Piso en Calle Sol">Piso en Calle Sol</a>
  <span class="item-price">200.000€</span>
  <div class="item-detail-char">
    <span>2 hab.</span>
    <span>90 m²</span>
    <span>Bajo</span>
  </div>
  <div class="item-description">Cozy ground floor</div>
</article>
<article class="item" data-element-id="3">
  <a class="item-link" href="/inmueble/100003/"
     title="Piso en Avenida Principal">Piso en Avenida Principal</a>
  <span class="item-price">Consultar</span>
  <div class="item-detail-char">
    <span>4 hab.</span>
    <span>120 m²</span>
    <span>planta 1ª</span>
  </div>
  <div class="item-description">Spacious apartment</div>
</article>
<article class="item premium" data-element-id="4">
  <a class="item-link" href="/inmueble/999999/" title="Premium Listing">Premium Listing</a>
  <span class="item-price">300.000€</span>
  <div class="item-detail-char">
    <span>5 hab.</span>
    <span>150 m²</span>
    <span>planta 3ª</span>
  </div>
  <div class="item-description">Premium property</div>
</article>
<article class="item" data-element-id="5" data-adid="ad1">
  <a class="item-link" href="/inmueble/888888/" title="Sponsored by adid">Sponsored Ad</a>
  <span class="item-price">250.000€</span>
  <div class="item-detail-char">
    <span>3 hab.</span>
    <span>100 m²</span>
    <span>planta 2ª</span>
  </div>
  <div class="item-description">Sponsored listing</div>
</article>
</body></html>"""

SAMPLE_WITH_PARKING = """<html><body>
<article class="item" data-element-id="1">
  <a class="item-link" href="/inmueble/200001/" title="Piso con parking">Piso con parking</a>
  <span class="item-price">180.000€</span>
  <span class="item-parking">Plaza de garaje incluida</span>
  <div class="item-detail-char">
    <span>3 hab.</span>
    <span>85 m²</span>
    <span>planta 2ª</span>
  </div>
  <div class="item-description">With parking space</div>
</article>
</body></html>"""


class TestParseListings:
    """Full parse_listings integration tests."""

    def test_happy_path_three_organic_one_sponsored(self) -> None:
        """GIVEN HTML with 3 organic + 1 premium + 1 adid WHEN parse_listings THEN 3 listings."""
        result = parse_listings(SAMPLE_HTML)
        assert len(result) == 3

    def test_first_listing_fields(self) -> None:
        """GIVEN first organic card WHEN parsed THEN fields are extracted correctly."""
        result = parse_listings(SAMPLE_HTML)
        first = result[0]
        assert first["external_id"] == "100001"
        assert first["url"] == "/inmueble/100001/"
        assert first["address"] == "Piso en Calle Mayor"
        assert first["price"] == Decimal("150000")
        assert first["m2"] == 80.0
        assert first["rooms"] == 3
        assert first["floor"] == "planta 4ª"
        assert first["description"] == "Nice flat in city center"
        assert first["portal"] == "idealista"
        assert first["price_includes_garage"] is False
        assert first["garage_price"] is None
        assert first["certificado_energetico_present"] is None

    def test_second_listing_floor_bajo(self) -> None:
        """GIVEN second card with Bajo floor WHEN parsed THEN floor='Bajo'."""
        result = parse_listings(SAMPLE_HTML)
        second = result[1]
        assert second["external_id"] == "100002"
        assert second["floor"] == "Bajo"

    def test_consultar_price_is_none(self) -> None:
        """GIVEN card with Consultar price WHEN parsed THEN price=None."""
        result = parse_listings(SAMPLE_HTML)
        third = result[2]
        assert third["price"] is None
        assert third["external_id"] == "100003"

    def test_sponsored_premium_excluded(self) -> None:
        """GIVEN premium class card WHEN parsed THEN excluded from results."""
        result = parse_listings(SAMPLE_HTML)
        ids = [r["external_id"] for r in result]
        assert "999999" not in ids

    def test_sponsored_adid_excluded(self) -> None:
        """GIVEN data-adid card WHEN parsed THEN excluded from results."""
        result = parse_listings(SAMPLE_HTML)
        ids = [r["external_id"] for r in result]
        assert "888888" not in ids

    def test_empty_html_returns_empty_list(self) -> None:
        """GIVEN empty HTML WHEN parse_listings THEN empty list."""
        assert parse_listings("") == []
        assert parse_listings("<html></html>") == []

    def test_no_cards_returns_empty(self) -> None:
        """GIVEN HTML with no article.item cards WHEN parse_listings THEN empty."""
        assert parse_listings("<html><body><p>no listings</p></body></html>") == []

    def test_missing_m2_floor_returns_none(self) -> None:
        """GIVEN card missing m2/floor WHEN parsed THEN m2=None floor=None."""
        html = """<html><body>
<article class="item" data-element-id="1">
  <a class="item-link" href="/inmueble/300001/" title="Test">Test</a>
  <span class="item-price">100.000€</span>
  <div class="item-detail-char">
    <span>2 hab.</span>
  </div>
  <div class="item-description">Test</div>
</article>
</body></html>"""
        result = parse_listings(html)
        assert len(result) == 1
        assert result[0]["m2"] is None
        assert result[0]["floor"] is None

    def test_parking_detected(self) -> None:
        """GIVEN card with parking span WHEN parsed THEN price_includes_garage=True."""
        result = parse_listings(SAMPLE_WITH_PARKING)
        assert len(result) == 1
        assert result[0]["price_includes_garage"] is True
        assert result[0]["external_id"] == "200001"
