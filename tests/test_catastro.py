"""Tests for the Catastro OVC enrichment module."""

from __future__ import annotations

import urllib.error
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from home_ops.enricher import catastro
from home_ops.models.schema import Listing

if TYPE_CHECKING:
    from home_ops.models.data_storage import DuckDBConnection

_PORTAL_URL = (
    "https://www.idealista.com/venta-viviendas/"
    "chiclana-de-la-frontera-cadiz/con-precio-hasta_500000,"
)

_XML_SINGLE = """<?xml version="1.0" encoding="UTF-8"?>
<consulta_dnp>
    <control><cudnp>1</cudnp></control>
    <bico>
        <bi>
            <rc>
                <pc1>1234567</pc1>
                <pc2>AB1234</pc2>
                <car>0001</car>
                <cc1>WX</cc1>
                <cc2>0001</cc2>
            </rc>
            <debi>
                <luso>Residencial</luso>
                <sfc>85.5</sfc>
                <ant>1990</ant>
            </debi>
        </bi>
    </bico>
</consulta_dnp>
"""

_XML_MULTI = """<?xml version="1.0" encoding="UTF-8"?>
<consulta_dnp>
    <control><cudnp>2</cudnp></control>
    <lrcdnp>
        <rcdnp><rc><pc1>1</pc1></rc><dt>Calle Real 5, 1A</dt></rcdnp>
        <rcdnp><rc><pc1>2</pc1></rc><dt>Calle Real 5, 1B</dt></rcdnp>
    </lrcdnp>
</consulta_dnp>
"""

_XML_ERROR = """<?xml version="1.0" encoding="UTF-8"?>
<consulta_dnp>
    <lerr>
        <err>
            <cod>1</cod>
            <des>La provincia no existe</des>
        </err>
    </lerr>
</consulta_dnp>
"""


def _make_listing(db: DuckDBConnection, address: str = "Calle Real 5, 3º") -> Listing:
    """Build a Listing and insert it into `listings` so the FK to catastro_data holds."""
    listing = Listing(
        content_hash=f"hash-{address}",
        address=address,
        url="https://www.idealista.com/inmueble/12345/",
        portal="idealista",
    )
    listing.id = db.insert_listing(listing)
    return listing


def _mock_urlopen(xml_body: str) -> MagicMock:
    """Build a mock replacing urllib.request.urlopen as a context manager."""
    response = MagicMock()
    response.status = 200
    response.read.return_value = xml_body.encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return MagicMock(return_value=response)


class TestLookupShapes:
    """The three OVC XML response shapes, end to end through lookup()."""

    def test_single_match(self, db: DuckDBConnection) -> None:
        """GIVEN a single-match XML response WHEN lookup THEN status ok with data."""
        listing = _make_listing(db)
        with patch("urllib.request.urlopen", _mock_urlopen(_XML_SINGLE)):
            result = catastro.lookup(listing, _PORTAL_URL, db)

        assert result is not None
        assert result.status == "ok"
        assert result.superficie_catastro == 85.5
        assert result.uso == "Residencial"
        assert result.antiguedad == 1990
        assert result.rc == "1234567AB12340001WX0001"

    def test_multi_candidate(self, db: DuckDBConnection) -> None:
        """GIVEN a multi-candidate XML response WHEN lookup THEN status multi_candidate."""
        listing = _make_listing(db)
        with patch("urllib.request.urlopen", _mock_urlopen(_XML_MULTI)):
            result = catastro.lookup(listing, _PORTAL_URL, db)

        assert result is not None
        assert result.status == "multi_candidate"
        assert result.superficie_catastro is None
        assert result.rc is None

    def test_error_response(self, db: DuckDBConnection) -> None:
        """GIVEN an error XML response WHEN lookup THEN status not_found."""
        listing = _make_listing(db)
        with patch("urllib.request.urlopen", _mock_urlopen(_XML_ERROR)):
            result = catastro.lookup(listing, _PORTAL_URL, db)

        assert result is not None
        assert result.status == "not_found"


class TestAddressRegex:
    """_extract_via address parsing."""

    @pytest.mark.parametrize(
        ("address", "expected_tipo", "expected_nombre", "expected_numero"),
        [
            ("Calle Real 5, 3º", "CL", "Real", "5"),
            ("Avenida de la Constitución, 12", "AV", "de la Constitución", "12"),
            ("Plaza Mayor 1", "PZ", "Mayor", "1"),
            ("Paseo Marítimo, 100, bajo", "PS", "Marítimo", "100"),
        ],
    )
    def test_matches(
        self, address: str, expected_tipo: str, expected_nombre: str, expected_numero: str
    ) -> None:
        """GIVEN a well-formed address WHEN extracted THEN tipo/nombre/numero match."""
        parsed = catastro._extract_via(address)
        assert parsed is not None
        assert parsed.tipo_via == expected_tipo
        assert parsed.nombre_via == expected_nombre
        assert parsed.numero == expected_numero

    def test_no_match_returns_none(self) -> None:
        """GIVEN an address with no recognizable via type WHEN extracted THEN None."""
        assert catastro._extract_via("Urbanización Los Pinos, s/n") is None

    def test_empty_address_returns_none(self) -> None:
        """GIVEN an empty address WHEN extracted THEN None."""
        assert catastro._extract_via("") is None


class TestSlugResolution:
    """_resolve_provincia_municipio slug lookup."""

    def test_known_slug(self) -> None:
        """GIVEN a portal_url with a known slug WHEN resolved THEN Provincia/Municipio."""
        result = catastro._resolve_provincia_municipio(_PORTAL_URL)
        assert result == ("CADIZ", "CHICLANA DE LA FRONTERA")

    def test_unknown_slug_returns_none(self) -> None:
        """GIVEN a portal_url with no matching slug WHEN resolved THEN None."""
        result = catastro._resolve_provincia_municipio(
            "https://www.idealista.com/venta-viviendas/madrid-madrid/"
        )
        assert result is None


class TestCacheHit:
    """Cache hit path never touches the network."""

    def test_cached_result_skips_network(self, db: DuckDBConnection) -> None:
        """GIVEN a cached row for listing_id WHEN lookup THEN no urlopen call."""
        listing = _make_listing(db)
        db.conn.execute(
            "INSERT INTO catastro_data (listing_id, superficie_catastro, uso, "
            "antiguedad, rc, status) VALUES (?, ?, ?, ?, ?, ?)",
            [listing.id, 90.0, "Residencial", 2000, "RC123", "ok"],
        )

        with patch("urllib.request.urlopen") as mock_urlopen:
            result = catastro.lookup(listing, _PORTAL_URL, db)

        mock_urlopen.assert_not_called()
        assert result is not None
        assert result.status == "ok"
        assert result.superficie_catastro == 90.0
        assert result.rc == "RC123"


class TestBackoff:
    """_backoff_retry exponential backoff on transient failures."""

    def test_retries_then_succeeds(self) -> None:
        """GIVEN two transient failures then success WHEN retried THEN succeeds."""
        calls = {"n": 0}

        def flaky() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise catastro.CatastroTransientError("boom")
            return "ok"

        with patch("time.sleep") as mock_sleep:
            result = catastro._backoff_retry(flaky, max_attempts=3, base_delay=1.0)

        assert result == "ok"
        assert calls["n"] == 3
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1.0)
        mock_sleep.assert_any_call(2.0)

    def test_exhausts_attempts_raises(self) -> None:
        """GIVEN a function that always fails WHEN retried THEN raises after max_attempts."""

        def always_fails() -> str:
            raise catastro.CatastroTransientError("boom")

        with patch("time.sleep"), pytest.raises(catastro.CatastroTransientError):
            catastro._backoff_retry(always_fails, max_attempts=3, base_delay=1.0)

    def test_lookup_service_unavailable_not_cached(self, db: DuckDBConnection) -> None:
        """GIVEN persistent transient failures WHEN lookup THEN service_unavailable, no row."""
        listing = _make_listing(db)

        with (
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")),
            patch("time.sleep"),
        ):
            result = catastro.lookup(listing, _PORTAL_URL, db)

        assert result is not None
        assert result.status == "service_unavailable"
        row = db.conn.execute(
            "SELECT 1 FROM catastro_data WHERE listing_id = ?", [listing.id]
        ).fetchone()
        assert row is None


class TestPreconditionFailures:
    """Precondition failures that skip the network entirely and return None."""

    def test_unresolved_location_returns_none(self, db: DuckDBConnection) -> None:
        """GIVEN an unmapped portal slug WHEN lookup THEN None, no network call."""
        listing = _make_listing(db)
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = catastro.lookup(
                listing, "https://www.idealista.com/venta-viviendas/madrid-madrid/", db
            )
        mock_urlopen.assert_not_called()
        assert result is None

    def test_no_address_match_returns_none(self, db: DuckDBConnection) -> None:
        """GIVEN an address the regex can't parse WHEN lookup THEN None, no network call."""
        listing = _make_listing(db, address="Urbanización Los Pinos, s/n")
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = catastro.lookup(listing, _PORTAL_URL, db)
        mock_urlopen.assert_not_called()
        assert result is None

    def test_missing_listing_id_returns_none(self, db: DuckDBConnection) -> None:
        """GIVEN a listing with no id (pre-insert) WHEN lookup THEN None."""
        listing = _make_listing(db)
        listing.id = None
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = catastro.lookup(listing, _PORTAL_URL, db)
        mock_urlopen.assert_not_called()
        assert result is None
