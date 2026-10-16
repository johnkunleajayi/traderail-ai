# Venue abstraction and verified Quidax mappings

Canonical instruments are venue-neutral. Venue-specific identifiers live only in venue_instrument_mappings. No canonical model fields, user connections, provider credentials, market prices or trading behavior were added. Frontend source remains unchanged; its API-driven Markets section now receives the expanded universe.

## Schema and seeds

- venues: UUID id, unique uppercase code, name, extensible venue_type, active/disabled status, UTC-aware timestamps.
- venue_instrument_mappings: UUID id, venue/instrument foreign keys, opaque case-sensitive venue_symbol, status and UTC-aware timestamps. Unique (venue_id, instrument_id) and (venue_id, venue_symbol) prevent duplicate or ambiguous mappings.
- Canonical assets: BTC, ETH, USD, USDT, NGN. USDT is STABLECOIN; NGN and USD are FIAT. USD and USDT have separate IDs and meanings.
- Canonical SPOT instruments: BTC/USD, ETH/USD, BTC/USDT, ETH/USDT, BTC/NGN, ETH/NGN, USDT/NGN. USD/NGN was not added.
- Precision is integer decimal-place metadata, not a price or provider trading rule.

0004_venues_and_mappings creates the venue tables and seeds QUIDAX / Quidax / CRYPTO_EXCHANGE. It was already applied before the requested canonical expansion, so its historical contents are unchanged. 0005_expanded_market_mappings adds the new reference assets/instruments and verified mappings. Existing metadata is preserved using conflict-do-nothing inserts and actual referenced IDs. IDs are deterministic UUIDv5 values.

0005 deliberately blocks downgrade to avoid silently deleting reference rows that subsequent data may reference; rollback requires an explicit reviewed data-removal plan.

From backend, repeat seeds in this order:

~~~powershell
.\.venv\Scripts\python.exe -m app.instruments.seed
.\.venv\Scripts\python.exe -m app.venues.seed
~~~

Both commands use transactions and make no external requests. Existing metadata and balances are never reset. Canonical seeds are independent of mappings and remain present whether or not a venue supports them.

## Official catalogue verification

Consulted on 2026-10-16:

- [List all markets](https://docs.quidax.io/reference/list-api)
- [Markets introduction](https://docs.quidax.io/reference/introduction-markets)
- [Official public catalogue](https://openapi.quidax.io/exchange-open-api/api/v1/markets)

The public reference catalogue returned 110 entries. Exact base_unit, quote_unit and id values were checked; no ticker/price/account endpoints were called. The mapping count is derived from exact catalogue matches to the approved canonical universe, not an assumed count.

| Canonical instrument | Exact returned Quidax id |
| --- | --- |
| BTC/USD | No exact market; no mapping |
| ETH/USD | No exact market; no mapping |
| BTC/USDT | btcusdt |
| ETH/USDT | ethusdt |
| BTC/NGN | btcngn |
| ETH/NGN | ethngn |
| USDT/NGN | usdtngn |

Five exact matches were found. In particular, usdtngn was read from the catalogue, not inferred from naming conventions. USDT/NGN means USDT priced in Nigerian Naira and is distinct from USD/NGN. No quote currency was substituted. Catalogue availability may change; future seed edits require fresh verification. Application API reads do not contact Quidax.

## Public read-only API

- GET /api/venues
- GET /api/venues/QUIDAX
- GET /api/venues/QUIDAX/instruments

Unknown venue lookup/listing returns 404. Venue responses contain code, name, venue_type and status. Mapping responses contain venue metadata, canonical instrument information, exact venue_symbol and mapping status. Internal IDs, prices and trading fields are omitted. An active reference record is not an assertion of a connected user account.

## Verification

Complete backend suite: 80 passed, with two existing dependency deprecation warnings. Real Neon checks confirmed 0005_expanded_market_mappings (head), five assets and seven canonical instruments exactly once, QUIDAX exactly once, and the mapping set matching the fresh official catalogue. Repeated market and venue seeds preserved rows and timestamps. Existing BTC/USD, ETH/USD and asset metadata were unchanged. PostgreSQL-backed API reads passed, including USDT/NGN lookup. See final handoff for production build, dependency and secret/asset audits. No dependencies changed, and no files were staged, committed or pushed.