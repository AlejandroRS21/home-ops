# DESIGN: Catastro OVC Enrichment

## Service contract (external)

Endpoint (GET, no auth, no key):
```
https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/Consulta_DNPLOC
```
Query params: `Provincia`, `Municipio`, `Sigla` (TipoVia abbreviation),
`Calle` (NombreVia), `Numero`, `Bloque`, `Escalera`, `Planta`, `Puerta`
(last 4 optional, sent empty).

Response is XML, one of three shapes:
1. **Single match** — `<consulta_dnp><control><cudnp>1</cudnp>...</control><bico><bi>...` with
   `<rc>` (pc1/pc2/car/cc1/cc2), `<debi><luso>`, `<debi><sfc>`, `<debi><ant>`.
2. **Multiple candidates** — `<consulta_dnp><control><cudnp>N</cudnp>...</control><lrcdnp>` with
   N `<rcdnp>` entries (each has `<rc>` + `<dt>` address but no `<debi>` economic data).
3. **Error** — `<consulta_dnp><lerr><err><cod>.../<des>La provincia no existe|El municipio no existe|...</des></err></lerr></consulta_dnp>`.

No `requests`/`httpx` dependency needed — this is a single unauthenticated
GET with a handful of query params; stdlib `urllib.request` + `xml.etree`
(also stdlib) is sufficient. No new dependency added.

## Module: `src/home_ops/enricher/catastro.py`

### Public function

```python
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
```

Caller site (`_run_scan`, between `db.insert_listing()` and
`scorer.score()`):
```python
if inserted_id is not None:
    listing.id = inserted_id
if config.catastro.enabled:
    catastro.lookup(listing, config.portal_url, db)
score_result = scorer.score(listing, db_conn=db.conn)
```
`lookup()` takes `db: DuckDBConnection` (not raw `duckdb` connection) to
reuse the existing connection wrapper pattern used across the codebase
(`db.conn` for raw SQL, matching `insert_listing`/`get_listing`).

### Internal functions (not part of the public contract, named for clarity)

- `_resolve_provincia_municipio(portal_url: str) -> tuple[str, str] | None`
  — slug lookup table, returns `None` if `portal_url` slug isn't in the
  table.
- `_extract_via(address: str) -> ParsedAddress | None` — the one regex,
  returns `None` on no confident match.
- `_query_ovc(params: dict[str, str], timeout: float) -> str` — raw HTTP
  GET, raises `CatastroTransientError` on network/5xx (caller retries) or
  returns the raw XML body.
- `_parse_response(xml_body: str) -> ParsedCatastroData | CatastroOutcome`
  — parses the three response shapes into a typed result.
- `_backoff_retry(fn, max_attempts=3, base_delay=1.0)` — generic
  exponential backoff wrapper around `_query_ovc`.

### Types

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

@dataclass
class ParsedAddress:
    """Result of regex-extracting TipoVia/NombreVia/Numero from address."""
    tipo_via: str   # Catastro abbreviation, e.g. "CL", "AV", "PZ"
    nombre_via: str
    numero: str

CatastroStatus = Literal[
    "ok",                 # single match, data populated
    "no_address_match",   # R3: address regex didn't match — lookup never attempted
    "location_unresolved",# R2: portal_url slug not in table — lookup never attempted
    "multi_candidate",    # OVC returned >1 candidate, no economic data
    "not_found",          # OVC returned a "no existe" error for calle/numero
    "service_unavailable",# exhausted retries on transient failure
]

@dataclass
class CatastroResult:
    listing_id: int
    status: CatastroStatus
    superficie_catastro: float | None = None
    uso: str | None = None
    antiguedad: int | None = None
    rc: str | None = None
    fetched_at: datetime = field(default_factory=datetime.now)
```

`lookup()` returns `CatastroResult` whenever a cache row was written
(i.e. whenever R2/R3 preconditions were satisfiable enough to attempt or
skip-with-record), and returns bare `None` only for the two "never even
tried, nothing to cache" precondition failures
(`location_unresolved`, `no_address_match`) — see "Cache negative
results" below for why those are NOT persisted.

## Schema: `catastro_data`

```sql
CREATE TABLE IF NOT EXISTS catastro_data (
    listing_id INTEGER PRIMARY KEY REFERENCES listings(id),
    superficie_catastro DOUBLE,
    uso TEXT,
    antiguedad INTEGER,
    rc TEXT,
    status TEXT NOT NULL,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

- `listing_id` is the PK (one row per listing, enforces "never
  re-query" at the schema level — `INSERT ... ON CONFLICT DO NOTHING`
  mirrors the `insert_listing` pattern).
- `status` persists the `CatastroStatus` outcome even for non-`ok` rows,
  so the cache check (`SELECT 1 FROM catastro_data WHERE listing_id = ?`)
  is a single query regardless of prior success/failure.
- `superficie_catastro`/`uso`/`antiguedad`/`rc` are `NULL` for any
  non-`ok` status.
- Added via `DuckDBConnection.init_db()` following the existing
  `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN IF NOT EXISTS`
  idempotent-migration pattern already used for `listings` and
  `pending_approvals`.

### Cache negative results — which statuses get a row?

| Status | Row written? | Why |
|---|---|---|
| `ok` | yes | canonical success |
| `multi_candidate` | yes | OVC was reached; ambiguity is a stable fact about this address, retrying won't resolve it |
| `not_found` | yes | OVC was reached; "no existe" for this calle/numero is stable |
| `service_unavailable` | **no** | transient by definition — caching it would permanently block a listing that might succeed on a later `scan` run once the service recovers |
| `location_unresolved` | **no** | this is a run-level config problem (bad `portal_url`), not a listing-level fact; nothing to cache per-listing, and fixing the slug table should retroactively unblock all listings on the next run |
| `no_address_match` | **no** (see note) | best-effort per R3; however if the address never changes, retrying is pointless — deferred to a future change if this proves to cause repeated wasted regex attempts (cheap, local, no network cost, so not worth caching now) |

Rationale: only statuses reached *after* a network round-trip are
persisted, because those encode a fact learned from the authoritative
source. Precondition failures (address didn't parse, location didn't
resolve) are local/free to re-check and may become resolvable by fixing
config or if this module's slug table/regex is later extended — caching
them would silently and permanently suppress a listing that could later
succeed.

## Config

`Config.catastro: CatastroConfig` (new field, mirrors
`BuyerProtectionConfig`'s "block missing → defaults" pattern):

```python
class CatastroConfig(BaseModel):
    enabled: bool = False
```

`user_profile.yml`:
```yaml
catastro:
  enabled: false
```

Loaded in `load_config()`:
```python
catastro_raw = raw.get("catastro", {}) or {}
catastro = CatastroConfig(**catastro_raw) if catastro_raw else CatastroConfig()
```

## Province/Municipio slug table

Static dict in `catastro.py`, keyed by the idealista URL slug segment
(the part after `/venta-viviendas/` or `/alquiler-viviendas/`, e.g.
`chiclana-de-la-frontera-cadiz`), mapping to the exact Catastro
denominations:

```python
_SLUG_TO_LOCATION: dict[str, tuple[str, str]] = {
    # slug: (Provincia, Municipio) — exact Catastro official names
    "chiclana-de-la-frontera-cadiz": ("CADIZ", "CHICLANA DE LA FRONTERA"),
}
```
Extraction: `portal_url` is parsed with a plain string split on `/` to
get the segment after the search-type prefix, then looked up verbatim.
No fuzzy matching — an unlisted slug is a `location_unresolved` (whole
run, not per-listing, since `portal_url` is shared across all listings
in a `scan`).

## TipoVia regex (R3)

```python
_VIA_ABBREVIATIONS = {
    "calle": "CL", "avenida": "AV", "plaza": "PZ", "paseo": "PS",
    "camino": "CM", "carretera": "CR", "ronda": "RD",
}
_ADDRESS_RE = re.compile(
    r"^\s*(?P<tipo>Calle|Avenida|Plaza|Paseo|Camino|Carretera|Ronda)\s+"
    r"(?P<nombre>[^,\d]+?)[,\s]+(?P<numero>\d+)\b",
    re.IGNORECASE,
)
```
- Matches `"Calle Real, 12"`, `"Avenida de la Constitución 45"`.
- Does NOT match `"Urbanización Los Pinos, chalet 3"`, `"Piso en Cadiz
  centro"`, or anything without a recognized tipo-via prefix + trailing
  number — these fall through to `no_address_match`.

## Edge cases

1. **Multi-candidate** (`multi_candidate`): OVC returns `<lrcdnp>` with
   `cudnp > 1` when Calle/Numero match multiple properties (e.g.
   ambiguous street name, or a building with multiple RC subdivisions
   the address alone can't disambiguate — Bloque/Escalera/Planta/Puerta
   would be needed, which this change doesn't extract). Result: no
   economic data available, row cached with `status="multi_candidate"`,
   `superficie_catastro`/`uso`/`antiguedad`/`rc` all `NULL`.
2. **No street number in address** (`no_address_match`): regex requires
   a trailing number; addresses like `"Chalet en zona residencial"` with
   no number never match. Not persisted (see cache table above) — cheap
   to re-derive every run, no network cost incurred.
3. **Service down** (`service_unavailable`): after 3 attempts with
   exponential backoff (1s/2s/4s) all fail (timeout, connection error,
   or 5xx), give up for this run. NOT cached — next `scan` run retries
   from scratch for this listing.
4. **Province/municipality slug not in table** (`location_unresolved`):
   whole-run precondition failure, logged once, `lookup()` returns
   `None` immediately for every listing in the run without attempting
   per-listing extraction. Not cached (config-level, not listing-level).
5. **"No existe" errors from OVC** (`not_found`): e.g. street exists in
   the table but the specific `Numero` doesn't ("El número no existe").
   Treated the same as `not_found` — cached, not retried (per R5, only
   transient failures retry).
6. **Rústico (rural) property**: response has `<lorus>` instead of
   `<lourb>` and no `<debi>` economic block in the shape this change
   parses. Out of scope (see SPEC.md) — treated as `not_found` if it
   reaches `_parse_response` without the expected `<bi><debi>` fields,
   since this change only targets residential/urbano listings.
7. **`listing.id is None`**: `lookup()` requires a persisted listing
   (caller always calls it after `db.insert_listing()` sets `.id`); if
   somehow `None`, `lookup()` returns `None` immediately without any DB
   or network activity (defensive guard, not a normal path).
