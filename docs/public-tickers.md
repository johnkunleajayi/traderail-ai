# Public Quidax ticker integration (Milestone 5B Part 1)

This is public read-only market data. No Quidax credentials, user exchange connections, trading controls or ticker-history persistence exist. The frontend contacts TradeRail only. Canonical models and the database schema are unchanged.

## Official source and observed response

Consulted [Fetch a market ticker](https://docs.quidax.io/reference/fetch-a-market-ticker). The sole provider URL is:

~~~text
GET https://openapi.quidax.io/exchange-open-api/api/v1/markets/tickers/{market}
~~~

An actual unauthenticated USDT/NGN request on 2026-10-16 returned HTTP 200 with this shape:

- Root object: status = success, message = Successful, data = object.
- data is keyed by the exact requested venue symbol, such as usdtngn.
- data[market].ticker contains high, vol, last, low, buy, sell and open.
- last was a JSON number with 16 fractional digits in the wire response. Other fields mixed strings and JSON numbers.
- data[market].at is an integer epoch timestamp in milliseconds (observed value 1792146935000).

Only last and at are normalized. The timestamp is treated as a source snapshot timestamp; it is not claimed to be the timestamp of the latest individual trade. No volume, spread, change, chart or other statistics are exposed. If at is absent, source_timestamp is null; a present but invalid timestamp rejects the response. Old timestamps remain visible rather than being replaced with retrieval time: these are source-reported snapshots, not a freshness guarantee or executable quote.

## Flow and normalized contract

Canonical symbol -> active Instrument -> active QUIDAX venue/mapping -> stored venue_symbol -> MarketDataProvider protocol -> QuidaxPublicProvider -> normalized ticker -> TradeRail API -> Markets card.

The adapter has no instrument/symbol mapping list. It receives only the exact stored identifier and targets a fixed public HTTPS endpoint. HTTP calls use bounded timeouts, no redirects, no authorization headers and no environment proxy credentials. Responses are bounded to 64 KiB. Nothing is written to PostgreSQL.

Normalized response fields:

| Field | Meaning |
| --- | --- |
| canonical_symbol | TradeRail instrument identity |
| quote_currency | Canonical quote asset symbol; never substituted |
| venue | Source venue code QUIDAX |
| last_price | Positive finite Decimal on the backend; plain decimal string in JSON |
| source_timestamp | Source epoch-millisecond snapshot normalized to UTC, or null if absent |
| retrieved_at | UTC time the response was retrieved |

JSON decimal numbers are decoded directly with Decimal, without an intermediate float. The normalized contract rejects float prices and naive timestamps. UI formatting groups decimal strings and trims insignificant trailing zeros without converting prices to JavaScript Number.

## API and controlled failures

~~~text
GET /api/market-data/tickers/{canonical_symbol:path}
GET /api/market-data/tickers/BTC/USDT
GET /api/market-data/tickers/USDT/NGN
~~~

This is a public reference-data endpoint, with no write route.

- 200: normalized source ticker with Cache-Control: no-store.
- 404: instrument_not_found for unknown canonical instruments.
- 409: venue_mapping_unavailable when no active mapping exists, or the instrument is inactive. No provider fetch occurs.
- 503: ticker_unavailable for network errors, timeouts, non-success HTTP/envelopes, malformed JSON, wrong market identity, missing/invalid price, nonfinite/nonpositive/oversized price, oversized body, or invalid/future source timestamp. No fallback price is returned.

Provider failures are sanitized; raw provider structures and internal error details are not returned. API clients use canonical symbols and never need to know venue symbols.

## Frontend

Existing /api/instruments and /api/venues/QUIDAX/instruments calls still drive the cards and mapping indicators. Only active mapped cards mount the ticker component. Each requests its canonical ticker through TradeRail on mount and explicit refresh. There is no timer, WebSocket, background poll or direct browser-to-Quidax call.

Cards show last reported price, quote currency, Quidax attribution, source snapshot time when present, and retrieval time. Loading and unavailable/retry states show no invented values. Refresh clears the prior result while loading, so a failed refresh cannot leave a prior price presented as a successful new result. BTC/USD and ETH/USD retain No venue mapping and never mount a ticker component.

## Verification

The controlled live check used actual Neon mappings and invoked TradeRail's API with the real public provider. All five returned HTTP 200 from Quidax: BTC/NGN (btcngn), BTC/USDT (btcusdt), ETH/NGN (ethngn), ETH/USDT (ethusdt), USDT/NGN (usdtngn). BTC/USD and ETH/USD returned 409 without provider calls. Live check snapshot times were 2026-10-16 10:39:00-10 UTC; retrieval times were 10:39:06-18 UTC. These observations are point-in-time checks, not stored ticker history.

Automated tests mock every external provider request. They cover provider protocol substitution, full canonical -> stored mapping -> mocked HTTP flow, precision, timestamp normalization, invalid payloads/prices, missing source time, network errors, HTTP failures, no fallback data, and API contracts. Existing auth/portfolio/domain tests remain unchanged. No new dependencies or migrations are required; Neon remains 0005_expanded_market_mappings (head).

Manual browser checks: sign in, confirm the seven cards/five real prices and correct quote currencies; check source/retrieval timestamps and refresh; simulate a failed ticker request and retry; confirm both USD pairs have no price; check mobile/light/dark layouts, unchanged Paper Portfolio and logout. These visual/browser interactions are not claimed as verified by backend checks or the production build.