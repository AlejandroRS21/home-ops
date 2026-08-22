# SPEC: Catastro OVC Enrichment

## Status
Draft — no implementation yet.

## Summary
Enrich each newly inserted `listing` with cadastral data (surface area,
usage, age, cadastral reference) fetched from the free public OVC
Catastro web service (`Consulta_DNPLOC`), stored in a new `catastro_data`
table. Runs as a best-effort step between `db.insert_listing()` and
`scorer.score()`. Opt-in, off by default.

## Motivation
`Consulta_DNPLOC` returns official cadastral surface (`sfc`), usage
(`luso`), construction age (`ant`), and cadastral reference (`rc`) for a
property given its structured address (province, municipality, street
type/name/number). This is independent, government-sourced ground truth
that can cross-check the portal-declared `m2` and support future
scoring/fraud-detection dimensions. It is out of scope for this change
to *use* the data in scoring — only to fetch and persist it.

## Requirements

### R1 — Opt-in via config
- New config key `catastro.enabled: bool`, default `false`.
- When `false`, the enrichment step is skipped entirely (no network
  calls, no DB writes to `catastro_data`).
- Loaded via `home_ops.config.loader.load_config()` into `Config`.

### R2 — Province/Municipality resolution from the search URL, not from address
- `Provincia` and `Municipio` (required `Consulta_DNPLOC` params) MUST be
  derived from `config.portal_url` (the `idealista_url` search URL),
  never parsed from the free-text `listing.address` field.
- Idealista search URLs encode the location as a slug segment, e.g.:
  `https://www.idealista.com/venta-viviendas/chiclana-de-la-frontera-cadiz/`
  → municipality slug `chiclana-de-la-frontera`, province slug `cadiz`.
- The slug-to-Provincia/Municipio mapping uses a static lookup table
  (slug → official Catastro denomination) shipped in the module. Only
  the provinces/municipalities actually exercised by the user's
  `idealista_url` need entries initially; the table is designed to grow.
- If the URL cannot be parsed into a known province/municipality slug,
  enrichment is skipped for the whole run (this is a config-level
  precondition, not a per-listing failure) and a single warning is
  logged.

### R3 — TipoVia/NombreVia/Numero extraction from address (best-effort)
- These three params come from `listing.address` (free text), since
  street-level data isn't in the search URL.
- Extraction uses one simple, explicit regex matching the canonical
  Spanish address shape: `<tipo_via> <nombre_via>, <numero>` or
  `<tipo_via> <nombre_via> <numero>` (e.g. `"Calle Real, 12"`,
  `"Avenida de la Constitución 45"`).
- `TipoVia` is matched against a small fixed list of recognized
  abbreviations (Calle, Avenida, Plaza, Paseo, Camino, Carretera, Ronda
  — mapped to Catastro's official abbreviation codes, see Edge Cases).
- If the regex does not produce a confident match (no recognized tipo
  via, or no trailing number), enrichment is skipped for that listing.
  This is explicitly **not** a general NLP address parser — no fuzzy
  matching, no fallback heuristics beyond the one regex.

### R4 — Cache by listing_id, never re-query
- Before calling the OVC service for a given `listing.id`, check
  `catastro_data` for an existing row with that `listing_id`.
- If a row exists (regardless of whether the lookup previously
  succeeded or was recorded as "not found"), skip the network call.
- This means a failed/no-match lookup is cached too (see R6) — the
  service is only ever queried once per listing, ever.

### R5 — Exponential backoff on transient failures
- Network-level failures (timeout, connection error, 5xx) retry with
  exponential backoff: base delay 1s, multiplier 2x, max 3 attempts
  (1s, 2s, 4s ≈ 7s total worst case per listing).
- Non-transient outcomes (province/municipality not found, no address
  match, multiple candidates, zero candidates) do NOT retry — they are
  terminal for that listing/run.
- After exhausting retries, the listing is treated as enrichment-failed
  (see R6) and the pipeline continues — this step never raises to the
  caller in `_run_scan`.

### R6 — Best-effort, non-blocking
- Any failure mode (unparseable address, unresolvable
  province/municipality, service down, multi-candidate ambiguity, zero
  results, malformed XML response) results in `lookup()` returning
  `None` (or a result object with `status != "ok"`, see DESIGN.md) —
  never an exception that propagates to `_run_scan`.
- The listing proceeds to `scorer.score()` unchanged whether or not
  enrichment succeeded.
- A `None`/failed result MAY still be recorded in `catastro_data` with
  the outcome status, to satisfy R4 (never re-query). See DESIGN.md
  "Cache negative results" for the exact schema decision.

### R7 — Data persisted
For each successfully enriched listing, store in `catastro_data`:
- `superficie_catastro` — official cadastral surface (m²), from `<sfc>`.
- `uso` — usage/purpose string, from `<luso>` (e.g. "Residencial").
- `antiguedad` — construction year, from `<ant>`.
- `rc` — full cadastral reference, concatenation of `<pc1><pc2><car><cc1><cc2>`.
- `fetched_at` — timestamp of the successful (or terminal-failed) lookup.

## Out of Scope
- Using `catastro_data` in `scorer.score()` dimensions (future change).
- Parsing rústico (rural) property responses (`<lorus>`) — only urbano.
- Handling `Bloque`/`Escalera`/`Planta`/`Puerta` disambiguation (not
  extracted from address in this change).
- Building a comprehensive province/municipality slug table for all of
  Spain — only entries needed for the configured `idealista_url`(s).
- Retrying non-transient failures (ambiguous/no-match results are final).
- CLI commands to manually trigger/inspect enrichment.

## Acceptance Criteria
- With `catastro.enabled: false` (default), no behavior change: no new
  network calls, no `catastro_data` writes, `scan` behaves exactly as
  before this change.
- With `catastro.enabled: true` and a resolvable `idealista_url` +
  address, a new listing gets exactly one row in `catastro_data` after
  its first scan, and zero additional OVC calls on subsequent scans for
  the same `listing_id`.
- A listing whose address doesn't match the regex is scored normally
  with no enrichment attempt and no error surfaced to the user.
- A transient OVC outage does not crash `scan`; it retries per R5 then
  gives up gracefully for that listing.
