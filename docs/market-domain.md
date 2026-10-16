# Market Domain and Instrument foundation

TradeRail stores venue-neutral reference data only. There are no prices, candles, volume, provider connections, orders, holdings, execution, strategies or LIVE functionality in these modules. Frontend code is intentionally unchanged.

## Schema
assets: PostgreSQL UUID id, unique uppercase symbol, name, extensible asset_type string, active/disabled status, timezone-aware created_at/updated_at.

instruments: PostgreSQL UUID id, base_asset_id and quote_asset_id foreign keys to assets (delete restricted), unique canonical_symbol, extensible market_type string defaulting to SPOT, active/disabled status, integer price_precision and quantity_precision, timezone-aware created_at/updated_at.

Constraints reject identical base/quote assets, duplicate base/quote/market combinations, malformed canonical symbols, and precision counts outside 0..18. Precision fields are integer decimal-place metadata, not prices or floats. Initial domain precision is 2 price decimals and 8 quantity decimals; it is not a claim about any venue's supported increments or order rules.

Initial reference data is exactly:
- BTC: Bitcoin, CRYPTO
- ETH: Ethereum, CRYPTO
- USD: US Dollar, FIAT
- BTC/USD: SPOT
- ETH/USD: SPOT

## Migration and seeds
0003_market_domain follows 0002_paper_portfolios. It creates both tables and inserts frozen initial data within the migration transaction. UUIDv5 values use a fixed namespace and domain-qualified names, so the seeded IDs are identical across databases and executions.

The application seed helper uses PostgreSQL INSERT ... ON CONFLICT DO NOTHING. It resolves actual existing asset IDs by symbol, never overwrites existing metadata/status/precision, and does not run during application startup or API reads.

From backend, explicitly rerun the reference seeds with:
~~~powershell
.\.venv\Scripts\python.exe -m app.instruments.seed
~~~
The command owns a single transaction. No external API is contacted. Migration seeds are frozen inside the migration rather than imported from mutable application seed definitions.

## Read-only public API
- GET /api/assets: assets sorted by symbol
- GET /api/instruments: instruments sorted by canonical symbol
- GET /api/instruments/BTC/USD: exact canonical lookup
- GET /api/instruments/ETH/USD: exact canonical lookup

Canonical symbols contain a slash; the FastAPI path converter accepts it. An encoded slash is accepted as well. Unknown symbols return 404. Lookup is case-sensitive; lowercase venue-like names are not normalized into canonical instruments.
No authentication is required. No write endpoint or seed endpoint is exposed.

Example asset:
~~~json
{"symbol":"BTC","name":"Bitcoin","asset_type":"CRYPTO","status":"active"}
~~~
Example instrument:
~~~json
{
  "canonical_symbol":"BTC/USD",
  "base_asset":{"symbol":"BTC","name":"Bitcoin","asset_type":"CRYPTO","status":"active"},
  "quote_asset":{"symbol":"USD","name":"US Dollar","asset_type":"FIAT","status":"active"},
  "market_type":"SPOT",
  "status":"active",
  "price_precision":2,
  "quantity_precision":8
}
~~~
Internal IDs/timestamps and all price/venue data are omitted.

## Future venue mappings
The canonical Instrument model has no exchange, provider, venue or provider-symbol columns. A future mapping module can reference instrument.id and venue.id, with its own venue_symbol and uniqueness rules. It must not rename canonical instruments to match provider naming.

market_type uses a bounded uppercase string rather than a SPOT-only enum. Only SPOT is seeded or implemented now. If a future market type shares a base/quote pair, its canonical naming policy must maintain global symbol uniqueness; the syntax allows a colon qualifier for that future policy without adding any such instrument now.
No venue model, mapping table, adapter or provider connection is implemented in this task.

## Verification boundary
Unit tests mock SQLAlchemy boundaries. A separate real-Neon verification applies the migration, reruns the seeds, checks exact row counts/deterministic IDs, exercises public API reads against PostgreSQL, and attempts invalid rows inside a rolled-back transaction. No user, session or portfolio records are changed.
## Implementation verification results
- Complete backend tests: 62 passed, with the two existing Starlette/Authlib legacy-httpx deprecation warnings.
- pip check and unchanged frontend production build: passed.
- Neon revision: 0003_market_domain (head).
- Exactly three assets (BTC, ETH, USD) and two SPOT instruments (BTC/USD, ETH/USD).
- Repeated seeding preserved record IDs, metadata and timestamps.
- Real PostgreSQL uniqueness, distinct-asset and precision constraints passed rollback-only invalid-data checks.
- API listing, canonical lookup including encoded slash, and unknown-symbol 404 passed against Neon.
- No provider connections, market-data fields or extra instruments were introduced.
- Frontend, authentication/portfolio code and existing tests, favicon and approved brand assets remain unchanged.
- Secret scan passed. No new dependencies, staging, commits or pushes.
