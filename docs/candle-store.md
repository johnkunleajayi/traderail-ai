# Canonical Candle Store and Basic Data Quality (Milestone 6A)

The candle store adds explicit PostgreSQL persistence for validated historical research data. Binance remains a historical data provider, not an execution venue. Existing live historical retrieval, Quidax ticker behavior, authentication, Paper Portfolio, canonical instruments and frontend are unchanged. No charts, indicators, analysis, execution, scheduler or trading permissions were added.

## Schema and migration

0006_market_candles follows 0005_expanded_market_mappings without rewriting older revisions. It creates market_candles with:

- UUID primary key and instrument_id foreign key to canonical instruments, deletion restricted.
- Provider code, canonical timeframe, open_time, close_time.
- Open, high, low, close and volume as PostgreSQL unconstrained NUMERIC, returned as Decimal by psycopg/SQLAlchemy. No fixed scale is imposed, avoiding rounding of provider precision.
- Retrieved_at, created_at and updated_at as timezone-aware timestamps. App contracts normalize to UTC; PostgreSQL uses timestamptz.
- A database unique constraint on instrument_id + provider + timeframe + open_time. Its index also supports dataset reads.
- Database checks for supported timeframe, provider format, finite positive OHLC, finite nonnegative volume, consistent OHLC relationships, aligned intervals and completed candle times relative to retrieval.

Rows are immutable through application operations. Created/updated/retrieved timestamps remain those of the original successful insert when identical data is ingested again. There is no edit, delete, reset, overwrite or version endpoint. A schema downgrade drops this data table and should only be run as an explicitly reviewed migration operation.

## Explicit ingestion and reproducibility

Active canonical instrument -> existing historical research mapping -> provider fetch -> normalized Decimal candles -> full-batch validation -> atomic persistence.

Only BTC/USDT and ETH/USDT on BINANCE are supported, at 15m, 1h, 4h and 1d. No new instrument or provider-symbol mapping was introduced. Unsupported NGN/USD markets are rejected without substitution.

Ingestion requires an authenticated active application user and exact configured frontend Origin, matching existing cookie-authenticated mutation protection. This development endpoint operates on a shared research dataset; it does not confer trading permissions or connect an exchange account. Reads remain public reference-data operations. No automatic startup ingestion or background jobs exist.

All candles are validated before the first insert. Provider identity, canonical identity, timeframe, Decimal types, OHLC relationships, complete UTC bucket boundaries and duplicate batch identities are checked. Malformed batches never persist. Completed candles alone are accepted.

PostgreSQL INSERT ON CONFLICT DO NOTHING with RETURNING atomically handles identity collisions, including concurrent ingestion. If a row already exists, exact Decimal OHLCV and close_time comparisons determine whether it is unchanged. Equivalent numeric values with different trailing-zero formatting are equal. A changed retrieval timestamp alone does not alter the stored row.

A different OHLCV value for an existing identity is a candle_integrity_conflict. The API returns 409 and rolls back the entire transaction, including any earlier new rows from that request. It never overwrites the existing row or creates another version. The service owns no commit; the ingestion route commits once on success and rolls back on failure. Unexpected database/provider errors are sanitized.

## Endpoints

The existing live endpoint remains intact:

~~~text
GET /api/market-data/candles/BTC/USDT?timeframe=1h&limit=100
~~~

Separate routes avoid ambiguity with the live endpoint's slash-containing canonical-symbol path converter:

~~~text
POST /api/market-data/candle-ingestions/BTC/USDT?timeframe=1h&limit=100
GET  /api/market-data/stored-candles/BTC/USDT?timeframe=1h&provider=BINANCE&limit=100
GET  /api/market-data/candle-quality/BTC/USDT?timeframe=1h&provider=BINANCE
~~~

Timeframe defaults to 1h; ingestion/read limit defaults to 100, bounded to 1..1000. Provider is currently BINANCE only. Consumers never supply Binance symbols. Ingestion reports canonical_symbol, provider, timeframe, fetched, inserted and unchanged. Stored responses retain normalized candle contracts with financial decimal strings, original retrieval time and BINANCE provenance. Stored reads return the latest bounded window in ascending chronological order, without any external request.

Quality covers the whole stored dataset, not only the stored-read limit. Empty datasets return count=0, null range, no gaps and freshness=empty. Unknown instruments return 404; unsupported research mappings return 409; invalid query values return 422. Missing authentication returns 401; untrusted/missing ingestion Origin returns 403. There are no destructive public routes.

## Quality and freshness rule

Quality reports candle_count, earliest_open_time, latest_close_time, expected_interval_seconds, duplicate_count, gap ranges, missing_interval_count, freshness, expected_latest_close_time and evaluated_at.

Freshness is based exclusively on market interval coverage, never retrieved_at. At evaluation time, floor the UTC clock to the timeframe boundary. The expected latest completed candle closes one millisecond before that boundary. A nonempty dataset is fresh only if its latest stored close reaches that expected close; otherwise it is stale. There is deliberately no grace period. At the next boundary it becomes stale until that newly completed interval is stored. No timer or database mutation is needed to calculate this.

For example, at 14:15 UTC, an hourly dataset must reach 13:59:59.999 UTC. A recently downloaded dataset ending earlier is stale. A dataset may be fresh at its tail but still have internal gaps: freshness is not a completeness guarantee.

Gap detection compares consecutive unique open times with the canonical interval. A 10:00, 11:00, 13:00 hourly series reports 12:00 as missing. Large gaps are represented as first_missing_open_time, last_missing_open_time and missing_count, without generating replacement rows. Leading/trailing gaps outside the observed range are not inferred; missing latest coverage is represented by freshness. Duplicate count reports extra rows beyond unique open times; normal stored datasets have zero because PostgreSQL enforces uniqueness. Quality reads currently inspect all timestamp pairs for the dataset; pagination, aggregate optimization and explicit date-range datasets are future work.

## Controlled real-Neon verification

Migration reached 0006_market_candles (head). One public Binance request fetched exactly five completed BTC/USDT 1h candles. In-memory replay of that exact live batch exercised the authenticated ingestion route using a verification-only user dependency override; no new user or session was created and real Google login was not re-run.

- First ingestion: fetched=5, inserted=5, unchanged=0.
- Repeated identical batch: inserted=0, unchanged=5. Every stored column, including UUIDs/timestamps, remained unchanged.
- Stored endpoint returned the same five candles, Decimal values and BINANCE identity.
- At 2026-10-16 11:14:20 UTC, quality reported count=5, earliest open=06:00 UTC, latest close=10:59:59.999 UTC, expected interval=3600 seconds, duplicate_count=0, no gaps, freshness=fresh.
- Real PostgreSQL rollback-only checks verified precision beyond 38 digits, UTC, uniqueness, OHLC/nonfinite rejection and whole-batch conflict rollback. All synthetic verification rows were rolled back; only the five real candles remain.
- Original canonical assets/instruments and Paper Portfolio snapshots were identical before/after. Existing Binance live retrieval and Quidax ticker live checks passed.

Automated tests mock external providers and SQLAlchemy storage boundaries; the separate controlled Neon checks supply actual PostgreSQL evidence. No new dependencies were added. See the handoff for final suite, pip check, build, secret and brand-integrity results. No secrets, credentials, staging, commit or push were introduced.