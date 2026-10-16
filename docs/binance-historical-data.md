# Binance public historical OHLC research provider

This adds historical research data only. Quidax remains TradeRail's first venue and public ticker source; its intended future execution role is unchanged and no execution is implemented. Binance is not a TradeRail venue, user account connection or execution destination. The Quidax historical investigation remains unchanged in [ohlc-contract-investigation.md](ohlc-contract-investigation.md); its unresolved contract questions remain blocking. This implementation does not modify or reinterpret that investigation.

## Official contract

Verified current official documentation:

- [Spot Kline endpoint](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints#klinecandlestick-data)
- [Official Spot REST reference source](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md#klinecandlestick-data)
- [Market-data-only host](https://developers.binance.com/docs/binance-spot-api-docs/faqs/market_data_only)

The adapter calls only:

~~~text
GET https://data-api.binance.vision/api/v3/klines
~~~

This is Binance's official unauthenticated market-data-only host. No key, signature, account endpoint or alternative host is used. Query parameters are symbol, interval, limit, timeZone=0 and endTime (UTC milliseconds). The official maximum limit is 1000. With no startTime, this retrieves recent history ending at the requested endTime.

The documented response is an array of 12-element rows. A controlled live response matched this contract:

| Index | Meaning | Handling |
| --- | --- | --- |
| 0 | Open time, epoch milliseconds | UTC open_time |
| 1 | Open price, decimal string | Decimal open |
| 2 | High price, decimal string | Decimal high |
| 3 | Low price, decimal string | Decimal low |
| 4 | Close price, decimal string | Decimal close |
| 5 | Volume, decimal string | Decimal volume |
| 6 | Close time, epoch milliseconds | UTC close_time |
| 7 | Quote asset volume | Not exposed |
| 8 | Trade count | Not exposed |
| 9 | Taker-buy base asset volume | Not exposed |
| 10 | Taker-buy quote asset volume | Not exposed |
| 11 | Unused field | Not exposed |

The response does not contain a symbol field. Identity comes from the canonical/provider mapping and the exact symbol sent in the fixed-host request. Raw rows never cross the adapter boundary.

## Architecture and symbol mappings

Canonical Instrument -> historical research mapping -> HistoricalMarketDataProvider protocol -> Binance adapter -> normalized candles -> canonical TradeRail API.

The existing ticker MarketDataProvider protocol remains intact. A separate historical capability in the same provider module avoids forcing Quidax to claim unsupported candle behavior or Binance to implement tickers. Historical symbols live in a single immutable research registry, separate from database VenueInstrumentMapping records:

| Canonical symbol | Provider | Provider symbol |
| --- | --- | --- |
| BTC/USDT | BINANCE | BTCUSDT |
| ETH/USDT | BINANCE | ETHUSDT |

Resolution first validates the actual active canonical Instrument in PostgreSQL. It does not derive symbols by concatenation or use Quidax's venue symbols. No BINANCE Venue row is seeded. Unsupported instruments are never converted to another quote currency.

Timeframes are exactly 15m, 1h, 4h and 1d. Their Binance interval values are respectively 15m, 1h, 4h and 1d; adapter-only duration metadata defines UTC bucket boundaries.

## Normalization and validation

Each response item contains canonical_symbol, provider, timeframe, open_time, close_time, open, high, low, close, volume and retrieved_at. Provider identity is explicitly BINANCE, never QUIDAX.

Financial fields are parsed directly from documented strings to Decimal and serialized to plain decimal strings. Float and boolean financial inputs are rejected. Prices must be positive and finite; volume must be nonnegative and finite. Size/magnitude guards reject pathological payloads without truncation or rounding. Timestamps must be positive millisecond integers and normalize to UTC-aware datetimes.

Every row must contain the documented 12 positions. High must contain both open and close; low must not exceed either; these rules also enforce high >= low. Interval starts must align to their requested UTC timeframe, and close_time must match the final millisecond of that interval. Duplicate timestamps, invalid/future/unfinished intervals, empty/error payloads and oversized batches reject the whole request. No partial results or fallback candles are returned.

The request ends at the inclusive final millisecond of the previous UTC timeframe bucket. Consequently only completed candles are returned, even though Binance's unbounded latest-Kline request can include a forming candle. Results are sorted ascending by open_time; this changes ordering only. Missing intervals are not filled, resampled or repaired. Retrieved_at is the UTC response retrieval time, not candle time.

## TradeRail API

~~~text
GET /api/market-data/candles/BTC/USDT?timeframe=1h&limit=100
GET /api/market-data/candles/ETH/USDT?timeframe=4h&limit=3
~~~

Canonical symbols can include the slash or its URL encoding. No authentication or provider credentials are required for this read-only market-data endpoint. The consumer does not supply provider symbols or URLs.

- 200: list of normalized completed candles, Cache-Control: no-store.
- 422: unsupported timeframe or limit outside 1..1000. Default timeframe=1h, limit=100.
- 404: unknown canonical instrument.
- 409 historical_provider_unsupported: known instrument without an active supported research mapping, including BTC/NGN, ETH/NGN, USDT/NGN, BTC/USD and ETH/USD.
- 503 historical_data_unavailable: provider/network/rate-limit failure or invalid data, with no invented financial values and no raw private error details.

Provider HTTP calls have bounded connect/read timeouts, a 1 MiB response bound, no redirects, no automatic retry and no proxy credential inheritance. No data is cached or persisted. Upstream restrictions or outages remain controlled failures rather than a reason to switch hosts or substitute another market.

## Live verification and limits

Controlled checks on 2026-10-16 around 10:59 UTC invoked the new TradeRail endpoint against real canonical database records. Each returned HTTP 200 with three successfully normalized real Binance candles:

- BTC/USDT: 15m, 1h, 4h, 1d.
- ETH/USDT: 1h.

Provider identity and canonical/timeframe identities were checked in every returned candle. Unsupported NGN/USD requests returned 409 without historical provider calls. The existing Quidax ticker live check succeeded. The seven canonical instruments and all asset metadata were unchanged; no BINANCE execution venue exists. Alembic remains 0005_expanded_market_mappings (head).

The scope is recent completed-bar retrieval, at most 1000 bars per call. Date-range APIs, pagination, local history, charts, indicators, analysis, signals, strategies, backtesting and execution are not implemented. Data is Binance history and must not be treated as Quidax price/execution history. Frontend source, auth, Paper Portfolio and approved brand assets are unchanged. The original Quidax investigation SHA256 remains CB77DDE9EB03251C16F24BA84ECE80703D50A7E39F372E5878A77E154C0402EE.

Automated tests use mocked HTTP and do not require internet. They cover all four intervals, both mappings, documented field positions, Decimal precision, UTC times, invalid OHLC, duplicates, chronological sorting, failures, unsupported markets, protocol substitution and API provenance. No dependency or database migration was added. Nothing is staged, committed or pushed.