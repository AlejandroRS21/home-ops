"""Best-effort cadastral (Catastro OVC) enrichment for scraped listings.

Fetches official surface/usage/age/cadastral-reference data from the free
public OVC ``Consulta_DNPLOC`` web service for each newly inserted
listing, caches the outcome in ``catastro_data`` (never re-queries a
listing), and never raises to the caller. See:
docs/changes/catastro-ovc-integration/{SPEC,DESIGN}.md
"""

from __future__ import annotations

import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlencode
from xml.etree import ElementTree

if TYPE_CHECKING:
    from home_ops.models.data_storage import DuckDBConnection
    from home_ops.models.schema import Listing

logger = logging.getLogger(__name__)

_OVC_URL = (
    "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/"
    "OVCCallejero.asmx/Consulta_DNPLOC"
)

# slug: (Provincia, Municipio) — exact Catastro official denominations.
# Only entries for the configured idealista_url(s) are needed; grows as
# more locations are used (see DESIGN.md).
_SLUG_TO_LOCATION: dict[str, tuple[str, str]] = {
    "chiclana-de-la-frontera-cadiz": ("CADIZ", "CHICLANA DE LA FRONTERA"),
}

_VIA_ABBREVIATIONS = {
    "calle": "CL",
    "avenida": "AV",
    "plaza": "PZ",
    "paseo": "PS",
    "camino": "CM",
    "carretera": "CR",
    "ronda": "RD",
}
_ADDRESS_RE = re.compile(
    r"^\s*(?P<tipo>Calle|Avenida|Plaza|Paseo|Camino|Carretera|Ronda)\s+"
    r"(?P<nombre>[^,\d]+?)[,\s]+(?P<numero>\d+)\b",
    re.IGNORECASE,
)

_MAX_ATTEMPTS = 3
_BASE_DELAY = 1.0
_BACKOFF_MULTIPLIER = 2


@dataclass
class ParsedAddress:
    """Result of regex-extracting TipoVia/NombreVia/Numero from address."""

    tipo_via: str
    nombre_via: str
    numero: str


CatastroStatus = Literal[
    "ok",
    "no_address_match",
    "location_unresolved",
    "multi_candidate",
    "not_found",
    "service_unavailable",
]


@dataclass
class CatastroResult:
    """Outcome of a cadastral lookup for one listing."""

    listing_id: int
    status: CatastroStatus
    superficie_catastro: float | None = None
    uso: str | None = None
    antiguedad: int | None = None
    rc: str | None = None
    fetched_at: datetime = field(default_factory=datetime.now)


class CatastroTransientError(Exception):
    """Raised by _query_ovc on network/timeout/5xx failures; caller retries."""


def lookup(
    listing: Listing,
    portal_url: str,
    db: DuckDBConnection,
    *,
    timeout: float = 10.0,
) -> CatastroResult | None:
    """Best-effort cadastral enrichment for one listing.

    Checks catastro_data cache first (by listing.id). If cached, returns
    the cached result without a network call. Otherwise resolves
    Provincia/Municipio from portal_url and TipoVia/NombreVia/Numero from
    listing.address, queries the OVC service (with retry/backoff on
    transient failure), persists the outcome to catastro_data, and
    returns it.

    Never raises. Returns None when enrichment could not be attempted or
    completed (see CatastroResult.status for the reason when a row was
    still persisted).

    Requires listing.id to be set (post-insert).
    """
    if listing.id is None:
        return None

    cached = _get_cached(db, listing.id)
    if cached is not None:
        return cached

    location = _resolve_provincia_municipio(portal_url)
    if location is None:
        logger.warning(
            "Catastro enrichment skipped: could not resolve Provincia/Municipio "
            "from portal_url %r",
            portal_url,
        )
        return None

    parsed_address = _extract_via(listing.address)
    if parsed_address is None:
        return None

    provincia, municipio = location
    params = {
        "Provincia": provincia,
        "Municipio": municipio,
        "Sigla": parsed_address.tipo_via,
        "Calle": parsed_address.nombre_via,
        "Numero": parsed_address.numero,
        "Bloque": "",
        "Escalera": "",
        "Planta": "",
        "Puerta": "",
    }

    try:
        xml_body = _backoff_retry(lambda: _query_ovc(params, timeout))
    except CatastroTransientError:
        logger.warning(
            "Catastro OVC service unavailable for listing %s after %d attempts",
            listing.id,
            _MAX_ATTEMPTS,
        )
        # service_unavailable is never cached (see DESIGN.md) — return
        # without persisting so the next scan retries from scratch.
        return CatastroResult(listing_id=listing.id, status="service_unavailable")

    parsed = _parse_response(xml_body)
    result = (
        CatastroResult(
            listing_id=listing.id,
            status="ok",
            superficie_catastro=parsed.superficie_catastro,
            uso=parsed.uso,
            antiguedad=parsed.antiguedad,
            rc=parsed.rc,
        )
        if isinstance(parsed, _ParsedCatastroData)
        else CatastroResult(listing_id=listing.id, status=parsed)
    )
    _persist(db, result)
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_cached(db: DuckDBConnection, listing_id: int) -> CatastroResult | None:
    """Return the cached CatastroResult for listing_id, or None if absent."""
    row = db.conn.execute(
        "SELECT listing_id, status, superficie_catastro, uso, antiguedad, rc, fetched_at "
        "FROM catastro_data WHERE listing_id = ?",
        [listing_id],
    ).fetchone()
    if row is None:
        return None
    return CatastroResult(
        listing_id=row[0],
        status=row[1],
        superficie_catastro=row[2],
        uso=row[3],
        antiguedad=row[4],
        rc=row[5],
        fetched_at=row[6],
    )


def _persist(db: DuckDBConnection, result: CatastroResult) -> None:
    """Insert the lookup outcome into catastro_data (idempotent)."""
    db.conn.execute(
        """
        INSERT INTO catastro_data (
            listing_id, superficie_catastro, uso, antiguedad, rc, status, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (listing_id) DO NOTHING;
        """,
        [
            result.listing_id,
            result.superficie_catastro,
            result.uso,
            result.antiguedad,
            result.rc,
            result.status,
            result.fetched_at,
        ],
    )


def _resolve_provincia_municipio(portal_url: str) -> tuple[str, str] | None:
    """Resolve (Provincia, Municipio) from the idealista search URL slug."""
    segments = [s for s in portal_url.split("/") if s]
    for segment in segments:
        location = _SLUG_TO_LOCATION.get(segment)
        if location is not None:
            return location
    return None


def _extract_via(address: str) -> ParsedAddress | None:
    """Regex-extract TipoVia/NombreVia/Numero from a free-text address."""
    match = _ADDRESS_RE.match(address)
    if match is None:
        return None
    tipo = match.group("tipo").lower()
    sigla = _VIA_ABBREVIATIONS.get(tipo)
    if sigla is None:
        return None
    return ParsedAddress(
        tipo_via=sigla,
        nombre_via=match.group("nombre").strip(),
        numero=match.group("numero"),
    )


def _query_ovc(params: dict[str, str], timeout: float) -> str:
    """Raw HTTP GET to the OVC service; raises CatastroTransientError on failure."""
    url = f"{_OVC_URL}?{urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            status = getattr(response, "status", 200)
            if status >= 500:
                raise CatastroTransientError(f"OVC returned HTTP {status}")
            body: bytes = response.read()
            return body.decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code >= 500:
            raise CatastroTransientError(f"OVC returned HTTP {exc.code}") from exc
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CatastroTransientError(f"OVC network failure: {exc}") from exc


@dataclass
class _ParsedCatastroData:
    """Successful single-match economic data extracted from the response."""

    superficie_catastro: float | None
    uso: str | None
    antiguedad: int | None
    rc: str | None


def _parse_response(xml_body: str) -> _ParsedCatastroData | CatastroStatus:
    """Parse the OVC XML response into typed data or a terminal status."""
    try:
        root = ElementTree.fromstring(xml_body)  # noqa: S314
    except ElementTree.ParseError:
        return "not_found"

    error_desc = root.findtext("./lerr/err/des")
    if error_desc is not None:
        return "not_found"

    cudnp_text = root.findtext("./control/cudnp")
    try:
        cudnp = int(cudnp_text) if cudnp_text is not None else 0
    except ValueError:
        cudnp = 0

    if cudnp > 1:
        return "multi_candidate"

    bi = root.find("./bico/bi")
    if bi is None:
        return "not_found"

    debi = bi.find("debi")
    rc_el = bi.find("rc")
    if debi is None or rc_el is None:
        # Rústico / unexpected shape — out of scope, treated as not_found.
        return "not_found"

    pc1 = rc_el.findtext("pc1") or ""
    pc2 = rc_el.findtext("pc2") or ""
    car = rc_el.findtext("car") or ""
    cc1 = rc_el.findtext("cc1") or ""
    cc2 = rc_el.findtext("cc2") or ""
    rc = f"{pc1}{pc2}{car}{cc1}{cc2}"

    sfc_text = debi.findtext("sfc")
    ant_text = debi.findtext("ant")

    return _ParsedCatastroData(
        superficie_catastro=float(sfc_text) if sfc_text else None,
        uso=debi.findtext("luso"),
        antiguedad=int(ant_text) if ant_text else None,
        rc=rc or None,
    )


def _backoff_retry(
    fn: Callable[[], str],
    max_attempts: int = _MAX_ATTEMPTS,
    base_delay: float = _BASE_DELAY,
) -> str:
    """Retry fn() with exponential backoff on CatastroTransientError."""
    delay = base_delay
    for attempt in range(max_attempts):
        try:
            return fn()
        except CatastroTransientError:
            if attempt == max_attempts - 1:
                raise
            time.sleep(delay)
            delay *= _BACKOFF_MULTIPLIER
    raise CatastroTransientError("unreachable")  # pragma: no cover
