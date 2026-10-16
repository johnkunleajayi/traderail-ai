# Regime classification engine — 8A Part 1

Version 1.0.0 is a deterministic, pure domain interpretation of Market Intelligence 1.0.0 measurements. The only regimes are BULL_TREND, BEAR_TREND, RANGE, BREAKOUT, HIGH_VOLATILITY, UNSAFE and UNKNOWN. These describe conditions, not actions, forecasts or profitability. Thresholds below are initial hypotheses requiring empirical validation in Strategy Lab; they are not validated settings.

## Boundary and invocation

`app.market_regimes.engine.classify_regimes(feature_rows, as_of=aware_datetime, config=REGIME_CONFIG)` consumes existing frozen FeatureRow objects. It returns a frozen RegimeResult per input row, including warm-up and unsafe rows. An empty input returns an empty list. There is no FastAPI, database, HTTP client, provider adapter, API route, scheduling or persistence in this module. Nothing recalculates indicators. A caller may use the existing stored-intelligence service separately; the regime engine never pulls additional history.

`config.py` centralizes immutable versioned thresholds and derives warm-up from the existing intelligence configuration. `validation.py` checks feature rows. `evidence.py` derives normalized measurements from prior/current feature rows. `classification.py` contains precedence and confirmation primitives; `engine.py` orchestrates them over prefixes. Decimal arithmetic uses the existing fresh 50-digit ROUND_HALF_EVEN context; no binary floats enter domain calculations. Numeric overflow/underflow or invalid arithmetic gives UNSAFE and quarantines the suffix.

## Evidence formulas

For current close C and lookback k=3:

- EMA spread = (EMA20 - EMA50)/C.
- EMA slopes = (EMA[t] - EMA[t-k])/(k*C[t]), average per-candle normalized displacement, not an angle.
- Close position relative to each EMA = (C - EMA)/C.
- Normalized histogram = MACD histogram/C; raw RSI14, MACD, signal and histogram are retained too.
- Normalized ATR = ATR14/C. Realized volatility is the unchanged intelligence 20-log-return population standard deviation, unannualized.
- Volatility baseline is the mean of the previous 20 realized-volatility observations; normalized ATR baseline is the mean of the previous 20 ATR/close observations. Neither includes the current candle.
- RV ratio = current RV / max(prior RV mean, 0.001). ATR ratio = current normalized ATR / max(prior normalized ATR mean, 0.001). Floors explicitly avoid division by zero or extreme ratios caused only by a numerically negligible baseline. Raw baselines and ratios are exposed along with the floor parameters in configuration.
- Reference low/high are min/max of the previous 20 completed CLOSES. FeatureRow does not contain intrabar highs/lows: this is a closing-price structure, not an OHLC extreme/channel.
- Upside displacement = (C - prior high)/C; downside = (prior low - C)/C. ATR displacements divide the corresponding numerator by current ATR14; they are None if ATR is zero. Negative displacements mean the close is inside that reference boundary. No values are clamped or interpolated.

## Exact precedence

1. UNSAFE: invalid input/prefix. Immediate, never delayed by persistence.
2. HIGH_VOLATILITY: candidate when either relative volatility ratio reaches 2.5, normalized ATR is at least 0.005, AND realized volatility is at least 0.003. Requires two consecutive qualifying candidates; the first produces UNKNOWN, not a retained ordinary regime. This priority suppresses breakout/trend inference even while awaiting confirmation.
3. BREAKOUT: departure from the prior closing range satisfying both relative-price and ATR displacement thresholds, plus directional momentum AND volatility confirmation. Default confirmation count is one. This is the explicit exception to ordinary three-candle persistence: the same completed candle must carry independent structure, momentum and volatility evidence. It describes an event, not an instruction. Direction is UP or DOWN in evidence; it is not a new regime enum.
4. BULL_TREND / BEAR_TREND: matching structure and momentum, confirmed over three consecutive candidate candles.
5. RANGE: matching contained/weak structure and momentum bounds, confirmed over three consecutive candidate candles.
6. UNKNOWN: insufficient or conflicting valid evidence. A conflict clears ordinary persistence; it is never treated as unsafe data.

An immediate breakout does not reserve a fixed reference for later bars. Every candidate is assessed against its own strictly prior rolling 20 closes. A prior breakout can thus age into ordinary structure or UNKNOWN; no breakout event history is persisted.

## Parameters and conditions

All bounds are inclusive except close above/below EMAs and the sign of the MACD line, which are strict. Downward structure uses the negative mirror of the trend thresholds.

| Configuration | Default | Meaning |
|---|---:|---|
| engine_version | 1.0.0 | Regime definitions; source feature version also included in each result |
| persistence_window | 3 | Consecutive ordinary candidates |
| high_volatility_confirmation | 2 | Consecutive elevated-volatility candidates |
| breakout_confirmation | 1 | Explicit event exception, with all evidence requirements |
| slope_lookback | 3 | Prior EMA comparison distance |
| breakout_lookback | 20 | Strictly prior closing-price reference window |
| volatility_baseline_window | 20 | Strictly prior observations for both baselines |
| trend_spread | 0.0015 | At least 0.15% normalized directional EMA separation |
| trend_fast_slope | 0.0002 | At least 0.02% normalized EMA20 slope per candle |
| trend_slow_slope | 0.0001 | At least 0.01% normalized EMA50 slope per candle |
| momentum_up_rsi | 55 | Upward momentum RSI lower bound |
| momentum_down_rsi | 45 | Downward momentum RSI upper bound |
| range_spread | 0.001 | Absolute spread at most 0.1% |
| range_fast_slope | 0.00015 | Absolute EMA20 slope at most 0.015% per candle |
| range_slow_slope | 0.0001 | Absolute EMA50 slope at most 0.01% per candle |
| range_rsi_low / high | 40 / 60 | Contained momentum interval |
| range_histogram | 0.0005 | Absolute normalized histogram at most 0.05% |
| range_atr_ceiling | 0.02 | ATR/close at most 2% |
| range_volatility_ceiling | 0.01 | Unannualized RV at most 1% |
| breakout_displacement | 0.002 | At least 0.2% of current close beyond prior boundary |
| breakout_atr_displacement | 0.5 | Also at least half current ATR beyond prior boundary |
| breakout_atr_floor | 0.001 | ATR/close at least 0.1% |
| breakout_volatility_ratio | 1.2 | RV ratio confirming breakout, OR ATR ratio condition below |
| breakout_atr_ratio | 1.1 | Normalized ATR ratio confirming breakout |
| high_volatility_ratio | 2.5 | RV OR normalized ATR ratio threshold |
| high_volatility_atr_floor | 0.005 | Required current ATR/close at least 0.5% |
| high_volatility_return_floor | 0.003 | Required current unannualized RV at least 0.3% |
| volatility_baseline_floor | 0.001 | Effective denominator floor for RV ratio |
| atr_baseline_floor | 0.001 | Effective denominator floor for normalized ATR ratio |

Upward trend structure also requires close strictly above both EMAs. Downward structure requires close strictly below both. Upward momentum requires RSI >=55, MACD >0, MACD >= its signal, histogram >=0. Downward momentum requires RSI <=45, MACD <0, MACD <= signal, histogram <=0. RSI above 70 or below 30 does not reverse these rules. A constant-percentage price decline can have shrinking absolute negative MACD and a positive histogram; that is deliberately conflicting evidence and can remain UNKNOWN despite declining EMAs. This conservative consequence needs later empirical challenge.

Range requires every range bound in the table simultaneously; flat prices with genuinely computed zero histogram/RV are valid. Breakout up/down requires displacement beyond the corresponding prior boundary, confirming momentum in that direction, normalized ATR floor, and either the RV or ATR ratio confirmation. HIGH_VOLATILITY cannot be inferred from dollar ATR alone.

## Warm-up and persistence

Indices are zero-based. Intelligence first-available indices are derived from FEATURE_CONFIG: EMA20=19, EMA50=49, RSI14=14, ATR14=13, MACD=25, signal/histogram=33, returns=1, RV=20.

First candidate index = max(all measurement first indices, latest EMA first index + slope lookback, breakout lookback, RV first index + baseline window, ATR first index + baseline window). Defaults give max(49,52,20,40,33)=52: 53 supplied candles are needed for one candidate. Initial rows remain UNKNOWN with WARMUP_REQUIRED and INSUFFICIENT_EVIDENCE. They do not seed the confirmation counter. First ordinary confirmed classification is possible at index54 (55 candles); high volatility at index53 (54 candles); the fully confirmed breakout exception at index52 (53 candles).

The state counts consecutive identical candidates (including breakout direction) only. An established ordinary regime is retained while a different ordinary candidate has fewer than three consecutive observations. Evidence explicitly names the new candidate, count, required count, previous regime and retained_previous=true with PERSISTENCE_RETAINED. On the third candidate it changes. This retention lasts at most two consecutive contrary ordinary candidates under defaults; it does not assert that current raw evidence still supports the retained regime. UNKNOWN clears the established regime and run. Pending high volatility does not retain an ordinary regime. An established breakout/high-volatility event is not retained while waiting for a different ordinary candidate. State is local to each call and reconstructed deterministically from the supplied prefix.

## Unsafe data and uncertainty

Every row is checked incrementally without looking ahead: exact canonical context, provider/timeframe, UTC interval alignment, aware times, completed boundary as_of, strict chronological continuity, finite Decimal values, positive close/EMAs, nonnegative ATR/RV, RSI in [0,100], simple return >-1, and histogram = MACD - signal at the same numeric precision. Missing measurements before their individual expected warm-up are normal; missing required values at/after their expected first index are UNSAFE. This validates feature integrity; it cannot reconstruct discarded original OHLC or prove an upstream indicator formula from FeatureRow alone.

The first invalid row is UNSAFE with DATA_UNSAFE and a specific reason (for example INTERNAL_GAP, MIXED_CONTEXT, DUPLICATE_TIMESTAMP, MISSING_REQUIRED_MEASUREMENT). Every subsequent row is UNSAFE with INVALID_PREFIX. Earlier outputs remain unchanged. There is no silent suffix restart, skipping, sorting or repair; the caller must provide a separately identified valid contiguous dataset to resume. A structurally unidentifiable malformed row yields None for unavailable context/timestamps, never invented identity. Invalid caller configuration or naive as_of raises ValueError rather than becoming a market classification.

Valid conflict or absence of a matching category is UNKNOWN, not UNSAFE. Stale but completed contiguous observations remain inspectable; freshness is still the existing candle-quality layer, not an invented age rejection. as_of is a completion gate rather than a point-in-time revision archive.

## Output and limitations

Each result has canonical_symbol, provider, timeframe, UTC open_time/close_time, regime, immutable evidence, complete regime configuration and source feature_engine_version. Numerical evidence remains Decimal. Evidence includes all structure/momentum/volatility/reference measurements, raw candidate, breakout_direction, confirmation count/requirement, previous regime, retention flag, supplied/required history counts and deterministic reason codes. Codes distinguish structural/momentum support, TREND_UP_CONFIRMED, TREND_DOWN_CONFIRMED, RANGE_STRUCTURE, BREAKOUT_UP/DOWN, VOLATILITY_ELEVATED, CONFIRMATION_PENDING, CONFLICTING_EVIDENCE, INSUFFICIENT_EVIDENCE and DATA_UNSAFE.

These definitions have no predictive/return guarantees. Thresholds are shared across current timeframes but slopes, ATR and RV are per-bar, so outcomes are not timeframe invariant. Scale normalization does not remove liquidity, sampling, asset or provider effects. Baseline floors, event exceptions, hard category boundaries, persistence retention and closing-range references all require empirical validation. Truncated/prepended histories can change intelligence initialization and therefore regime results; full configuration plus explicit dataset boundaries are needed for future reproducibility. No full dataset versioning, Strategy Lab, interpretation UI or endpoint is built here.

Automated fixtures represent intentional market shapes and pass through the real intelligence engine; they are never product data. Real BTC/ETH observations are checked only after tests pass, without threshold adjustment or ingestion.


## Read-only API — 8A Part 2

GET /api/market-regimes/{base}/{quote}?timeframe=1h&provider=BINANCE&limit=100

For example: /api/market-regimes/BTC/USDT?timeframe=1h&provider=BINANCE&limit=100.

This is public read-only reference analysis, matching Market Intelligence access. Base and quote must be uppercase canonical asset symbols (one to sixteen letters/digits, beginning with a letter), not venue symbols. Supported provider query: BINANCE. Timeframes: 15m, 1h, 4h, 1d. Limit: minimum 1, default 100, maximum 1000, reusing the intelligence API bounds.

The API integration service resolves the canonical instrument and joined asset metadata with autoflush disabled, invokes the unchanged calculate_stored_features service with the exact requested limit, and passes those returned rows into the unchanged classify_regimes engine exactly once. A single captured UTC as_of completion boundary is shared by both calls. Instrument resolution is repeated internally by the unchanged intelligence service; this deliberate small extra reference lookup avoids changing its established interface. Neither lookup writes or flushes. The pure regime modules have no dependency on these new FastAPI schemas/router/service files.

The response contains:

- canonical_symbol; base_asset and quote_asset (canonical asset symbol strings from instrument relationships); provider; timeframe.
- candle_count: actual returned rows. dataset_start: first candle open_time. dataset_end: last candle close_time. Times are UTC.
- regime_engine_version; feature_engine_version; complete regime_configuration.
- regimes: chronological rows, including every warm-up UNKNOWN and any domain-produced UNSAFE row.
- latest_regime: the identical final serialized regime row, not a second classification or a separately interpreted summary.

Each row contains open_time, close_time, regime, candidate_regime, confirmation, evidence and reason_codes. Confirmation exposes count, required, previous_regime, retained_previous, available_candles and candidate_history_required. Evidence is the existing domain Evidence object in full; its candidate, confirmation fields and reason codes are also retained there for faithful transport. Domain Decimal values in evidence AND configuration are emitted as exact fixed-point JSON strings (no float conversion or display rounding); counters remain integers and flags remain booleans. Warm-up measurements stay null.

The selected latest-N dataset is the complete calculation boundary. No seed history is pulled outside it; no rows are hidden to satisfy warm-up. Therefore smaller limits restart indicator initialization and regime persistence, and can change the latest classification. No ingestion, provider requests, database writes or regime persistence occur. Cache-Control is no-store.

### Error versus classification semantics

- Unknown canonical instrument: 404, instrument_not_found.
- Existing canonical instrument but no stored rows for that provider/timeframe: 409, stored_candles_unavailable. An unsupported historical quote market is never substituted.
- Invalid base/quote shape, provider, timeframe or limit: normal FastAPI 422 validation response.
- Stored candles rejected by the existing intelligence validator (gap, duplicate, incomplete, malformed, etc.): 409, invalid_candle_dataset, with reason and candle_index. In this case the prerequisite feature dataset cannot be produced. The integration does not bypass that validator, recreate partial indicator rows, fabricate UNSAFE classifications or recompute a suffix.
- Intelligence numeric-range failure: 409, measurement_unavailable. No fallback values.
- Existing sanitized database failure response remains 503.
- A successful regime-engine execution yielding UNKNOWN or UNSAFE remains HTTP 200. Domain UNSAFE reasons, valid prefix and quarantined suffix are transported unchanged; the row is not converted into an HTTP error. Domain UNKNOWN remains ordinary valid output, including warm-up and pending confirmation. The final chronological row remains the source of truth for latest_regime.

The API adds no schema migration, frontend behavior or regime-methodology change. Automated tests use the real intelligence/regime pipeline with mocked persistence, and separately inject an invalid feature measurement to verify that domain UNSAFE transport remains 200. Live verification uses existing candles in read-only transactions, compares repeated complete responses and verifies database-wide row fingerprints before/after.
