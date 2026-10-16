# Historical Quidax K-line contract investigation — blocked

Milestone 5B Part 2 could not safely proceed to normalization. Only this investigation document was added; application code, provider interfaces, frontend, canonical domain and database schema remain unchanged. No commit or push was made.

## Official documentation

- [K-line with pending trades](https://docs.quidax.io/reference/fetch-k-line-data-with-pending-trades-for-a-market)
- The same official page with .md appended exposes its OpenAPI definition and response example.
- [Recent trades](https://docs.quidax.io/reference/fetch-recent-trades-for-a-given-market-pair)

Target endpoint:

~~~text
GET https://openapi.quidax.io/exchange-open-api/api/v1/markets/{currency}/k_with_pending_trades/{trade_id}
~~~

The documented periods support the requested canonical mapping: 15m -> 15, 1h -> 60, 4h -> 240, 1d -> 1440. The docs permit a timestamp filter expressed in seconds since the Unix epoch. They require a string trade_id but do not explain its semantics, a valid initial value, or a supported sentinel. The curl example uses the literal placeholder trade_id.

## Actual unauthenticated observations

Research was limited to small requests (limit=3 or 5) and used no credentials. Zero in the path was an explicitly experimental research probe, not a production sentinel or an application default.

1. With trade_id=0 and period/limit, but no timestamp query, BTC/USDT at all four periods and each other mapped instrument returned HTTP 200 with data.k=[] and data.trades=[]. HTTP success alone does not prove usable history.
2. Adding the documented timestamp=0 query produced non-empty arrays. The same actual venue symbols were read from Neon for the final requested market/timeframe matrix, rather than introducing adapter symbol assumptions.
3. A recent-trades request to the documented endpoint returned HTTP 401 / Invalid api key without credentials. No key was requested or introduced.
4. A real tid from the successful pending-trade response was replayed as trade_id. The returned historical rows and 100 pending trades were unchanged, including tids less than or equal to that cursor. This observation does not establish a reliable cursor contract.
5. The literal trade_id placeholder in the documented curl example did not yield a usable JSON response in the research check.

Actual envelope:

~~~json
{"status":"success","message":"Successful","data":{"k":[],"trades":[]}}
~~~

In non-empty responses, k contains descending six-element arrays. An actual BTC/USDT 1h row was:

~~~text
[1792141200000, "82196.62", "82015.4", "82344.47", "81846.99", "0.03632901"]
~~~

A preceding interval was:

~~~text
[1792137600000, "82183.53", "82196.62", "82431.95", "82104.71", "0.90587965"]
~~~

The first field is observed in milliseconds and adjacent 1h rows differ by 3,600,000. The documentation example instead contains ten-digit second-scale timestamps. Remaining values are strings in these live rows. Position 3 (zero-based) is the maximum price, and position 4 is the minimum. Price continuity suggests open/close at positions 1/2, but the official schema does not label positions, and this is **not promoted to a verified financial contract**. Applying a presumed timestamp/open/high/low/close/volume ordering would create impossible OHLC relationships for these live rows. Position 5 resembles volume, but its units are not sufficiently established to expose it.

Pending trades are objects with tid, type, date, price, amount, base_volume and quote_volume. In an actual returned entry, tid and date were both 46109600, while price/amount/volumes were JSON numbers. That date cannot safely be treated as a current Unix timestamp. The adapter must not infer trade time from it or aggregate these objects into candles. These records were never returned through an application endpoint or persisted to PostgreSQL.

## Preserved boundary and missing contract

No candle schema, candle provider method, normalized candle endpoint or frontend feature was added. No empty response was relabelled as verified history, no test encoded a guessed ordering, and no replacement candles were generated.

Before implementation, official clarification or a sufficiently explicit official contract is required for:

- Exact positional meanings of the live six-element row, especially open versus close and volume units.
- Timestamp units and whether rows are completed candles or may include an unfinished interval.
- Valid initial trade_id and its interaction with timestamp; whether zero is supported rather than accidentally accepted.
- Pending-trade cursor/filter behavior and usable timestamp semantics.

Once established, normalization should use Decimal parsed directly from JSON, UTC-aware times, chronological ascending output, duplicate rejection, timeframe/limit validation and OHLC relationship validation. Higher layers should retain canonical identifiers and resolve actual venue mappings. These are planned validation requirements, not implemented behavior.

## Verification boundary

Complete existing backend suite: 128 passed, with the existing two Starlette/Authlib HTTPX deprecation warnings. pip check passed. Frontend production build passed. Alembic remains 0005_expanded_market_mappings (head); no migration was required. Existing authentication/portfolio/canonical/ticker code and frontend remain unchanged. See handoff for the final live raw-data matrix, existing ticker liveness and secret/brand audit.

The research scripts and raw responses are local ignored work files. They are evidence gathering only and are not imported by application code. No application candle persistence, API credentials, orders, execution, charts, indicators or strategy behavior was introduced.
## Final raw live matrix

All requests below used the actual stored mapping symbol, limit=3, timestamp=0 and experimental trade_id=0. Every response was HTTP 200 with 100 pending-trade objects. No result was exposed as a normalized candle.

| Canonical instrument | Timeframe / provider period | Raw k rows |
| --- | --- | --- |
| BTC/USDT | 15m / 15 | 3 |
| BTC/USDT | 1h / 60 | 3 |
| BTC/USDT | 4h / 240 | 0 |
| BTC/USDT | 1d / 1440 | 0 |
| ETH/USDT | 1h / 60 | 3 |
| BTC/NGN | 1h / 60 | 3 |
| ETH/NGN | 1h / 60 | 3 |
| USDT/NGN | 1h / 60 | 3 |

The empty 4h and 1d results are a further limitation: HTTP 200 does not verify historical availability at those advertised periods. No shorter-period resampling was attempted. BTC/USD and ETH/USD failed existing mapping resolution as expected, with no substitute venue request. A live existing ticker call using the stored BTC/USDT mapping succeeded with a positive Decimal price.

Final audit confirmed no backend/frontend changes, unchanged approved brand hashes, ignored private environment/root source PNGs, no credential matches in versionable files, no staged files and no commit/push. Only this document is a versionable addition.