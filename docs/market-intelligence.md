# Market intelligence measurements (7A Part 1)

The pure Part 1 engine calculates measurements from persisted canonical candles. Part 2 exposes it through a read-only API (documented below). There is no frontend integration, provider/network call, write, indicator persistence, interpretation label or trading decision.

## Layers and input

`primitives.py` contains reusable formulas; `engine.py` orchestrates frozen domain DTOs from `types.py`; `validation.py` validates the entire input before calculation. `service.py` reads one instrument/provider/timeframe from MarketCandle, ordered by open time, then adapts it to the pure engine. No FastAPI response types enter the calculation layer.

Use `calculate_stored_features(db, canonical_symbol, provider, timeframe, as_of=aware_datetime)` for persisted data. The caller supplies the completion boundary explicitly. No provider mapping or external retrieval is needed.

By default the reusable service reads the entire dataset without a limit or completion filter. The API explicitly requests a bounded window (see Part 2 below). An incomplete row rejects the batch rather than silently disappearing. Every interval must be completed by as_of, aligned to its UTC timeframe, have the exact stored close time (next boundary minus one millisecond), and have an aware retrieval time no earlier than close time. Mixed instrument/provider/timeframe, non-Decimal or non-finite values, nonpositive OHLC, negative volume, impossible OHLC, duplicates, reversed order and internal gaps raise InvalidCandleData with a safe code and offending index. Nothing is sorted, filled, repaired or mutated by the engine. Empty input returns an empty result.

A stale but contiguous completed dataset is calculable; freshness remains the existing quality layer's responsibility. as_of is a completion gate, not a point-in-time retrieval/revision archive. This is not a backtesting implementation.

## Numeric convention

All financial inputs and measurements are Decimal. Every public primitive and orchestration call uses a fresh 50-significant-digit, ROUND_HALF_EVEN context independent of the caller's ambient context. Decimal ln and sqrt handle transcendental operations without binary float conversion. Invalid operations, division by zero, overflow and underflow fail clearly; no fallback zeros are substituted. The original stored close value is retained unchanged in each output row. Rounding is deliberate for repeating fractions and transcendental results; tests use high-precision independent expectations and small absolute tolerances where justified.

## Formulas and initialization

Let C[t] be close, N the period. Indexing starts at zero; unavailable values are None.

- EMA(N): first value at N-1 is the arithmetic mean of the first N closes. Thereafter alpha = 2/(N+1); EMA[t] = alpha*C[t] + (1-alpha)*EMA[t-1]. EMA20 first appears on candle 20, EMA50 on candle 50.
- RSI14: delta[t] = C[t]-C[t-1], gain=max(delta,0), loss=max(-delta,0). Initial average gain/loss is the arithmetic mean of the first 14 changes (15 closes). Subsequent averages use (13*previous+current)/14. RSI = 100-100/(1+average_gain/average_loss). Zero loss with positive gain gives 100; zero gain with positive loss gives 0; both zero gives 50 as an explicit flat-price convention. First available on candle 15.
- True Range: first candle uses high-low because no preceding close exists. Later TR = max(high-low, abs(high-previous_close), abs(low-previous_close)). ATR14 seeds with the mean of the first 14 TR values, including the first candle's range, on candle 14. Thereafter ATR=(13*previous_ATR+TR)/14. This initialization differs from conventions that discard the first candle entirely.
- MACD: SMA-seeded EMA12 minus SMA-seeded EMA26; line begins on candle 26. Signal is SMA-seeded EMA9 of the available MACD line values and begins on candle 34. Histogram=line-signal, also first available on candle 34. Only leading warm-up values are omitted when constructing the signal seed, never internal market intervals.
- Simple return: C[t]/C[t-1]-1. Log return: ln(C[t]/C[t-1]). Both begin on candle 2.
- Realized volatility: population standard deviation of the latest 20 log returns, sqrt(sum((r-mean(r))^2)/20). First available on candle 21. The calculation centers returns relative to the first return before taking their mean to avoid rounding residuals for identical returns. Units are per-observation log-return dispersion; no annualization and no percentage scaling. This is demeaned rolling volatility, not root-sum-of-squared returns.

Only the current candle and earlier values enter each result. No warm-up zero filling occurs. A genuinely computed zero remains zero. Each FeatureRow includes canonical_symbol, provider, timeframe, UTC open_time/close_time, original close, ema_20, ema_50, rsi_14, atr_14, macd, macd_signal, macd_histogram, simple_return, log_return and realized_volatility.

## Limitations

Initialization depends on the first stored candle; prepending older history changes SMA-seeded results. Future reproducible consumers will need explicit dataset boundaries and formula-version metadata. No such persistence or future consumer is implemented here. The unbounded service remains useful for whole-dataset verification. API windowing explicitly restarts warm-up and must never conceal internal gaps.

No migration or dependency is required. Existing authentication, portfolio, market domain, provider adapters, storage/quality APIs and frontend are unchanged. Unit tests use synthetic known sequences only as mathematical fixtures; they are never substituted into stored data or product output.


## Read-only API (7A Part 2)

GET /api/market-intelligence/{canonical_symbol}?timeframe=1h&provider=BINANCE&limit=100

Canonical symbols contain a slash (for example BTC/USDT); provider-specific symbols never enter this contract. Reference measurements are public, matching the existing stored-history APIs. Only BINANCE is currently accepted; timeframes are 15m, 1h, 4h and 1d. No requests trigger ingestion or provider calls.

The default limit is 100 and maximum is 1000 (minimum 1). SQL selects exactly the latest limit rows in descending open-time order, then reverses that bounded window before validation/calculation. No extra seed history is loaded; warm-up restarts at the window's first candle. A smaller limit can therefore change subsequent SMA-seeded measurements. Gaps or invalid rows inside the requested window reject the entire window. Earlier data outside the selected window is neither loaded nor claimed valid. The original unbounded service interface remains available for whole-dataset validation.

The response contains canonical_symbol, provider, timeframe, candle_count (actual selected rows), first_open_time, latest_close_time, feature_configuration and features. Features contain UTC times, close and all Part 1 measurements; Decimal values serialize as exact fixed-point strings with no display rounding, and warm-up None remains JSON null. Cache-Control is no-store. There are no interpretation fields.

FeatureConfiguration is immutable and centralized in config.py, consumed by the engine and response metadata. Version 1.0.0 identifies the unchanged Part 1 formulas. Metadata includes EMA20/50, RSI14, ATR14, MACD12/26/9, volatility20, annualization=false, initialization conventions and Decimal precision/rounding. This is formula identity, not a dataset-versioning system. New definitions require an explicit version change.

Errors: unknown canonical instrument gives 404 instrument_not_found; existing instrument with no selected stored dataset gives 409 stored_candles_unavailable (including unsupported historical markets without stored rows); invalid/gapped/incomplete input gives 409 invalid_candle_dataset with reason and candle_index; numeric calculation range failure gives 409 measurement_unavailable. Invalid provider/timeframe/limit gives standard FastAPI 422 validation errors. Existing database failure handling remains sanitized 503. No fallback history or prices are substituted.

Both instrument and candle selects disable SQLAlchemy autoflush; this service performs no writes, flushes, commits or rollbacks. Routes add no persistence and require no migration. The frontend remains unchanged.
