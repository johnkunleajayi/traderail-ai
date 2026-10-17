# Strategy Lab foundation — Milestone 9A and 9B Parts 1–3

This is a pure backend research foundation: dataset/experiment specifications, cost assumptions, metric primitives, validation definitions, comparison benchmarks and lifecycle labels. It is not a strategy or backtester. It imports the existing immutable Market Intelligence candle/context types and their pure completed-candle validator. It does not access SQLAlchemy, FastAPI, external providers, clocks, storage or the frontend. Existing intelligence and regime calculations are untouched.

## Explicit research dataset

Use `app.strategy_lab.dataset.create_dataset` with a canonical `DatasetContext`, the complete supplied candle sequence, explicit aware `warmup_start`, `evaluation_start`, `evaluation_end` and observational `as_of` timestamps.

Boundaries are interval boundaries: warm-up start and evaluation start are inclusive candle opens; evaluation end is exclusive (the boundary immediately after the final candle). The sequence must exactly cover the declared span. Evaluation start must match an actual supplied candle. At least one evaluation candle is required; zero warm-up candles is allowed explicitly. Counts are derived, never trusted from caller metadata.

All candles must be completed at `as_of`, belong to one instrument/provider/timeframe, have valid finite Decimal OHLCV, and be in strict contiguous chronological order. Gaps, duplicates, reversed rows, mixed contexts, impossible OHLC and incomplete candles are rejected. No sorting, interpolation, skipping, repair, automatic ingestion or hidden history occurs.

The immutable snapshot exposes separate `warmup_candles` and `evaluation_candles`. Warm-up can initialize indicators/regimes but must never enter future performance evaluation. The foundation does not prescribe a sufficient warm-up length or run either engine. Consumers must choose it explicitly. `history_through(T)` returns only supplied candles at or before T; unknown timestamps are rejected. A future consumer must use this causal prefix for decisions and must not inspect later evaluation/OOS observations. The dataset factory runs no evaluation or training. Part 3 adds explicit split definitions and in-sample/OOS views; the module cannot police arbitrary future consumer code.

## Dataset and configuration fingerprints

SHA-256 hashes versioned, namespace-separated UTF-8 canonical JSON. Namespaces are `strategy-lab.dataset/v1`, `strategy-lab.configuration/v1` and `strategy-lab.experiment/v1`; fingerprint values use `sha256:<64 lowercase hexadecimal characters>`. Python `hash()` is never used.

Canonical serialization uses tagged nodes for maps, lists, strings, integers, booleans, null and Decimal. Map keys must be strings and are sorted; list order matters. Tags prevent accidental identity collisions between Decimal, integer, string and boolean values. Floats, non-finite Decimals and unsupported objects are rejected. Decimal coefficients/exponents are canonicalized directly from `as_tuple()` without arithmetic or ambient precision rounding. Numerically equivalent trailing-zero representations and signed zero have equal identities. Timestamps serialize as UTC with fixed microsecond precision.

Dataset identity includes canonical symbol, provider, timeframe, all three boundaries, derived candle count, and every candle's open/close timestamps, exact OHLCV and retrieval timestamp. Retrieval provenance changes therefore change the full snapshot identity even if prices are unchanged. Database-local UUIDs are validated for consistent context but excluded from the hash, permitting equivalent canonical snapshots in different databases. Equivalent timezone representations do not change identity. Changing data, context or boundaries changes identity. This is an in-memory content fingerprint, not a persisted dataset/versioning system or a security signature.

Configuration snapshots preserve the full immutable canonical tagged payload alongside its fingerprint. Caller mutations cannot change the snapshot. Dictionary insertion order is irrelevant; meaningful value/type/list-order changes affect identity. No strategy configuration is interpreted or executed.

## Experiments and manifests

`create_experiment` produces an immutable specification with a deterministic experiment ID, dataset fingerprint, Market Intelligence and Regime Engine versions (current defaults: `1.0.0` / `1.0.0`), strategy family/version placeholders and canonical strategy configuration. Family/version default to `None`; when supplied they must be paired non-empty metadata strings. This is not a strategy registry or lifecycle.

Experiment identity hashes the dataset fingerprint, both engine versions, strategy family/version and configuration fingerprint. `created_at` is a required aware informational timestamp supplied by the caller and excluded from identity. Identical research definitions created at different times retain identical IDs.

`create_manifest` checks that the experiment references the supplied dataset and separates an `identity` section from informational `created_at`. It also records format versions, canonical context, explicit boundaries and warm-up/evaluation/total counts. It does not embed all candle rows, persist anything or reconstruct data from a fingerprint alone.

Engine version labels must identify immutable definitions in future research workflows. The foundation does not hash engine source code or build a full environment/package/dataset archival system. Fingerprints identify inputs, not profitability, scientific validity or ownership.

## Invariants and limitations

- No look-ahead: observations at T are restricted to T and earlier history; prefix access supports this contract.
- No hidden history: the supplied span is the dataset boundary; there is no extra fetch for warm-up.
- Warm-up is excluded from evaluation through explicit separate views.
- Chronology and continuity are validated without modification.
- Future out-of-sample observations cannot influence earlier decisions. Explicit split definitions exist, but no decision, optimization or OOS execution runner is implemented.
- No API, frontend, migrations, database writes, indicator changes, strategy logic, benchmark execution, backtesting, signals, recommendations, TradeIntent or trading.

Focused offline tests cover canonical hashes, precision, context/boundary/data changes, immutable snapshots, causal prefixes, validation, configuration identity and timestamp-independent reproducibility.

## Part 2 — immutable research cost assumptions

`ResearchCostModel` requires explicit Decimal `trading_fee_rate`, `spread_rate` and `slippage_rate`; each is a finite fraction in [0, 1]. No real Binance/Quidax fees or exchange defaults are assumed. `execution_delay` is a nonnegative `timedelta` (default zero), retained as an assumption only. It does not shift timestamps, find a future candle, create fills or simulate execution.

`costs_for_notional` accepts a caller-supplied nonnegative Decimal notional for one hypothetical execution leg. Fees = notional × fee rate. Spread is explicitly the **full bid/ask spread fraction**; spread cost = notional × spread rate × 0.5 per leg. Slippage cost = notional × slippage rate. Entry and exit notionals must be supplied separately by a future consumer; nothing infers orders, side, fill price, volume or realized slippage. CostBreakdown preserves fees, spread and slippage separately. All are nonnegative; rebates are outside this model.

`configuration()` returns a detached mapping, including cost-model version `1.0.0`, spread convention and exact integer delay microseconds. It is compatible with Part 1 configuration snapshots. Part 1 experiment/schema/fingerprinting code is unchanged; costs are not silently injected into existing identities. Part 3’s `create_research_experiment` explicitly binds these cost assumptions, validation and benchmarks into existing configuration snapshots.

## Part 2 — metric semantics

Pure functions in `app.strategy_lab.metrics` accept supplied realized accounting observations/equity/durations/notionals. They do not calculate a strategy, process candles or maintain an execution state. `RealizedTrade` records signed gross P&L and a CostBreakdown; net P&L = gross − fees − spread − slippage. All monetary observations must use one consistent accounting currency. There is no FX conversion or currency inference.

| Primitive | Exact convention |
| --- | --- |
| `total_return(initial, ending_equity)` | (ending equity − initial capital) / initial capital; simple equity return, no deposit/withdrawal adjustment |
| `gross_return(initial, trades)` | Sum realized gross P&L / initial capital |
| `net_return(initial, trades)` | Sum realized net P&L / initial capital |
| `realized_pnl_aggregation(trades)` | Separate totals: gross P&L, fees, spread, slippage, net P&L |
| `drawdown_series(equity)` | At each supplied point: (running peak − equity) / running peak; nonnegative fraction, using only that point and earlier points |
| `maximum_drawdown(equity)` | Largest defined drawdown in the supplied sequence |
| `trade_count(trades)` | Number of supplied realized observations, including breakeven outcomes |
| `win_rate(trades)` | Count strictly positive **net** outcomes / total observations |
| `loss_rate(trades)` | Count strictly negative **net** outcomes / total observations |
| `profit_factor(trades)` | Sum positive net P&L / absolute sum negative net P&L |
| `average_win(trades)` | Mean strictly positive net P&L |
| `average_loss(trades)` | Mean absolute magnitude of strictly negative net P&L (positive monetary amount) |
| `expectancy(trades)` | Mean net P&L across all observations, including breakeven; money per trade, not a prediction |
| `exposure_percentage(exposed, evaluation_duration)` | 100 × exposed duration / evaluation duration; exact microsecond durations, no sample-count assumption |
| `turnover(notionals, initial)` | Sum unsigned executed notionals / initial capital; every leg counts, no halving or netting |
| `total_fees`, `total_spread_cost`, `total_slippage_cost` | Sum the corresponding explicit cost components |

Returns, drawdowns, win/loss rates and turnover are ratios, not percentage points. Only `exposure_percentage` returns 0–100 percentage points. Turnover can exceed 1. Trade statistics use net outcomes so transaction costs can turn a gross win into a net loss. Breakeven observations are neither wins nor losses, but remain in the rate/expectancy denominators. Win rate is not evidence of profitability and is not optimized here.

### Undefined and invalid inputs

- Empty trade sets have count zero, P&L/cost sums zero and gross/net returns zero when starting capital is positive. Win/loss rates, averages, expectancy and profit factor are `None` without observations.
- Profit factor is `None` when total net losses are zero, including all-win or all-breakeven sets; it is zero when losses exist but wins do not. No fabricated infinity is returned.
- Average win/loss is `None` when its respective subset is empty.
- Zero initial capital yields `None` for all return and turnover ratios. Negative initial capital is invalid.
- Equity must be nonnegative finite Decimal. Empty drawdown sequences are empty and their maximum is `None`. Leading zero equity produces `None` until a positive peak exists; subsequent zero equity means 100% drawdown. All-zero series have no defined maximum. No synthetic initial equity is inserted: callers must include starting equity explicitly if required.
- Zero evaluation duration yields `None` exposure; negative durations or exposure greater than evaluation duration are invalid. The caller supplies already consolidated exposed duration and must not double-count overlapping exposure.
- Floats, non-finite values, negative costs/notionals and malformed observations are rejected with `ValueError`. No missing value is repaired or invented.

### Numeric and evaluation boundaries

Finite sums/differences and products use sufficient coefficient precision to preserve exact Decimal results, including cancellation and very small values. Division uses an isolated Decimal context with **50 significant digits, ROUND_HALF_EVEN**, matching the precision convention used by Market Intelligence. Recurring ratios necessarily have finite Decimal precision. Ambient process Decimal precision/rounding does not affect results; no floats or cent quantization are used. Input observations are never mutated.

Metrics operate only on supplied sequences. Equity sequence order must already be chronological; these numeric primitives contain no timestamps and therefore cannot independently verify chronology. They do not sort or infer missing observations. Future consumers must pass only evaluation observations/durations/notionals from Part 1, excluding warm-up, and must handle cash flows, unrealized P&L and currency consistency explicitly. Return primitives do not calculate a full portfolio equity curve.

Part 2 primitives do not run strategies, validation or benchmarks. There is no backtesting, TradeIntent, Risk Engine, execution, API, frontend, database write or migration in Milestone 9A. Deterministic tests use hand-verifiable accounting examples and cover undefined denominators, precision, cost conventions, drawdowns and ambient-context independence.

## Part 3 — chronological validation definitions

`TimeWindow(start, end)` is an immutable UTC-normalized, aware interval with inclusive start and exclusive end. Empty, reversed and naive intervals are invalid. Chronology is compared after UTC conversion, including repeated local-hour offsets. Nothing is shuffled or silently sorted.

`ValidationSplit` pairs an explicit in-sample window with an optional later OOS window. Their intervals cannot overlap. Its `configuration_cutoff` is the exclusive end of the in-sample window: only completed observations before this cutoff are eligible to inform this split’s earlier parameter/configuration selection. OOS data must not inform that earlier selection. The timestamp is a data-availability boundary, not proof that a human selected parameters without hindsight; informational experiment creation time is not such proof either.

`ValidationPlan` supports exactly:

| Kind | Definition | Declared evaluation windows |
| --- | --- | --- |
| IN_SAMPLE | One in-sample-only window | That in-sample window |
| OUT_OF_SAMPLE | One in-sample window followed by one OOS window | OOS only |
| WALK_FORWARD | At least two explicitly supplied in-sample/OOS pairs | Each pair’s OOS window, chronologically |

Walk-forward is deliberately conservative: each complete fold must end at or before the next fold’s in-sample start. No in-sample or OOS candle is reused across folds. Expanding or rolling overlapping training windows are not supported in this version. Forward-only order is required; reverse or overlapping input is rejected rather than reordered. Adjacent half-open windows are valid. Explicit unused intervals are permitted, remain visible in configuration boundaries, and are never silently filled or evaluated. There is no automatic fold generation, optimization or retraining.

`validate_for(dataset)` requires all windows to lie within the dataset’s declared evaluation span and use actual candle boundaries. Warm-up is excluded from both split views. `in_sample_candles(dataset, index)` exposes only the declared in-sample rows; `out_of_sample_candles` exposes only the corresponding OOS rows. Changing later OOS candles does not change the earlier in-sample view. No hidden history is fetched or interpolated. Consumers must use the explicitly supplied warm-up/causal history for indicator initialization and must freeze parameter choices using only permitted earlier data. No runner yet enforces arbitrary external consumers’ behavior.

Validation version is `1.0.0`, with explicit `inclusive_start_exclusive_end` and `strictly_disjoint_chronological` conventions. Its deterministic configuration contains all windows, cutoffs and evaluation periods.

## Part 3 — comparison benchmarks

`BenchmarkKind` has exactly `CASH` and `BUY_AND_HOLD`. Immutable `BenchmarkDefinition` exposes Decimal market exposure:

- CASH: `0`, meaning no market exposure. It does not assert any interest yield, cash return or execution outcome.
- BUY_AND_HOLD: `1`, meaning passive full, unlevered exposure over each declared validation evaluation period. It does not generate entries, exit prices, trades, returns or recommendations.

`BenchmarkConfiguration` requires an explicit non-empty set of definitions, rejects duplicates, and canonicalizes membership order. Both benchmarks may be selected together. Benchmark version is `1.0.0`.

When bound through `ResearchConfiguration`, benchmark evaluation windows are taken exactly from `ValidationPlan.evaluation_windows`: IN_SAMPLE’s window, the OOS window, or each walk-forward OOS window. They do not accidentally extend into warm-up or the paired training period. Costs are recorded alongside benchmark definitions, but are not applied or simulated. Future comparison execution must use the same period/currency assumptions; no pooled fold results or benchmark equity curves are calculated here.

## Part 3 — strategy lifecycle labels

`StrategyState` represents exactly `EXPERIMENTAL`, `BACKTESTED`, `VALIDATED`, `PAPER`, `LIVE_CANDIDATE`, `SHADOW`, `LIMITED_LIVE`, `LIVE`, `SUSPENDED`, `RETIRED`.

`StrategyLifecycle` is an immutable record requiring an explicitly supplied enum state. There is no promotion/transition method, scheduler, approval workflow or automatic mutation. Creating experiments or metrics never changes lifecycle state. The labels do not grant execution permission and do not certify evidence of validation. No transition policy or live functionality is implemented.

## Part 3 — research identity integration

`ResearchConfiguration(validation, benchmarks, cost_model)` holds the three immutable typed definitions. Its detached canonical-compatible mapping records research configuration version `1.0.0`, validation version/policy/windows, benchmark membership/exposure/evaluation windows, and Part 2 cost-model version/rates/spread convention/execution-delay microseconds.

`create_research_experiment` validates dataset binding and delegates to the unchanged Part 1 `create_experiment`, supplying the existing configuration snapshot with separate `strategy` and `research` namespaces. Existing direct Part 1 experiments retain their original identity behavior. Existing `create_manifest` includes the same immutable identity/configuration snapshot, so no new manifest schema or alternate hashing system is needed.

Changing cost rates/delay, validation kind/boundaries/folds, benchmark membership or strategy parameters changes identity. Dictionary order, equivalent Decimal scales, equivalent UTC timestamp representations, benchmark presentation order and informational `created_at` timestamps do not. Caller mutation of detached configuration mappings does not alter captured experiment identity. Boundary timestamps are meaningful research inputs and therefore affect identity; creation time remains informational. Lifecycle state is descriptive metadata and is not inserted into research identity.

A complete dataset fingerprint includes OOS content, as Part 1 requires. It identifies the full research snapshot; it must not be used as a future optimizer seed or input that allows future OOS contents to influence earlier parameter choices. No optimizer or stochastic selection exists here.

### Pure usage pattern

```python
from app.strategy_lab.validation import ValidationKind, TimeWindow, ValidationSplit, ValidationPlan
from app.strategy_lab.benchmarks import BenchmarkKind, BenchmarkDefinition, BenchmarkConfiguration
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.research import ResearchConfiguration, create_research_experiment
from app.strategy_lab.experiment import create_manifest

# dataset is an existing validated Part 1 ResearchDataset.
# Boundaries are supplied aware timestamps; rates are supplied Decimals.
plan = ValidationPlan(ValidationKind.OUT_OF_SAMPLE, (
    ValidationSplit(TimeWindow(in_sample_start, in_sample_end),
                    TimeWindow(oos_start, oos_end)),
))
comparisons = BenchmarkConfiguration((
    BenchmarkDefinition(BenchmarkKind.CASH),
    BenchmarkDefinition(BenchmarkKind.BUY_AND_HOLD),
))
research = ResearchConfiguration(plan, comparisons,
    ResearchCostModel(fee_rate, spread_rate, slippage_rate, execution_delay))
experiment = create_research_experiment(dataset, research=research,
    strategy_configuration={}, created_at=informational_created_at)
manifest = create_manifest(dataset, experiment)
```

This creates definitions and fingerprints only. It executes no strategy or benchmark and reads/writes no database or provider.

## Complete 9A module and verification scope

```text
backend/app/strategy_lab/
  __init__.py
  canonical.py       # Stable tagged serialization and SHA-256
  dataset.py         # Explicit completed-candle research snapshot
  experiment.py      # Immutable identity and reproducibility manifest
  numeric.py         # Decimal arithmetic conventions
  costs.py           # Research assumptions and explicit cost components
  metrics.py         # Pure supplied-observation measurement primitives
  validation.py      # Chronological partitions and causal data views
  benchmarks.py      # Passive comparison definitions
  lifecycle.py       # Explicit immutable state labels
  research.py        # Typed integration using existing experiment identity
```

Offline tests cover all three parts, including chronology, overlap/leakage rejection, warm-up exclusion, immutable definitions, benchmark period binding, lifecycle non-promotion and identity sensitivity to research assumptions. Existing Intelligence/Regime engines, API contracts, authentication, portfolio, provider adapters, frontend, approved assets and database schema are outside this implementation.

Remaining work for subsequent milestones includes explicitly scoped strategy definitions, backtest execution/accounting and empirical evaluation/reporting. Metric primitives and validation/benchmark definitions alone do not implement those capabilities. No approved allocation of that work among 9B/9C/9D is assumed here. Actual strategy/Adaptive Trend, parameter optimization, order simulation, TradeIntent, Risk Engine, paper/live execution, APIs, frontend, migrations and ML are absent.

## Milestone 9B Part 1 — pure strategy contract

A new `backend/app/strategies/` module supplies immutable analytical inputs and decisions plus a reusable protocol. It reuses 9A canonical serialization/configuration snapshots without modifying 9A, Market Intelligence or Regime Engine code. Part 1 supplies the contract only. Part 2 adds the concrete Adaptive Trend family below; no backtest runner or execution behavior exists.

### Explicit immutable strategy definition

`StrategyDefinition(family, version, parameters)` requires an uppercase family identifier, an explicit version and a configuration mapping. Parameters are deeply copied into read-only mappings/tuples. Existing tagged canonical serialization preserves exact Decimal values, rejects float/non-finite/unsupported values, and makes dictionary ordering and equivalent Decimal scales irrelevant to identity. The full snapshot remains available alongside read-only parameters.

`configuration_identity` is SHA-256 over versioned `strategy.configuration/v1` canonical data containing family, version and the existing configuration fingerprint. Family/version/meaningful parameter changes alter identity. No registry, automatic versioning, lifecycle promotion or parameter selection is performed.

### Single-observation strategy input

`StrategyInput(context, candle, intelligence, regime, definition, as_of, position_state=PositionState.CASH)` uses the existing pure `DatasetContext`, `Candle`, `FeatureRow` and `RegimeResult` types. All are immutable. This contract exposes one current analytical observation, not an unrestricted history collection. Existing indicators and regime evidence already summarize permitted past data; future raw rows cannot be appended through an input history field.

Validation requires:

- A valid canonical instrument/provider/timeframe and correctly linked candle instrument UUID.
- A completed valid Decimal OHLCV candle at the supplied aware `as_of` boundary, with retrieval time no later than `as_of`.
- All current Market Intelligence numerical measurements present as finite Decimals. Individual indicator warm-up nulls are rejected; callers must wait for complete intelligence rather than substituting zeros.
- Matching canonical symbol/provider/timeframe across candle context, intelligence and regime.
- Exactly matching UTC open/close timestamps; shifted, future, naive or missing analytical timestamps are rejected.
- Intelligence close equal to candle close; available mirrored regime measurements equal to the supplied intelligence values.
- Typed regime/version/evidence metadata, immutable reason codes, valid numeric types/counts and confirmation structure. Existing confirmation streaks can exceed their required count and are preserved without capping.

The existing pure candle and intelligence validators are reused. Intelligence validation runs in the existing isolated Decimal context; indicators and regimes are not recomputed. Complete `UNKNOWN` and `UNSAFE` regime rows can be supplied honestly. This contract does not force either into a decision or reinterpret classification semantics. Malformed/incomplete rows are rejected before strategy evaluation.

`as_of` is a caller-supplied observational completion/availability boundary, not an implicit clock call. Historical candle retrieval can occur after candle completion. It does not allow a later analytical row to be substituted for T. The contract cannot prove the mathematical provenance of an externally forged finite measurement; consumers must supply trusted existing engine output, calculated causally at T. It neither fetches history nor audits/recalculates upstream formulas.

`input_identity` now fingerprints `strategy.input/v2` (Part 2 adds thesis state): canonical context, complete candle snapshot including retrieval provenance, intelligence row, complete regime evidence/configuration/engine versions strategy configuration identity and explicit thesis state. Database-local UUIDs are validated but excluded from canonical content identity, consistent with 9A. Equivalent UTC representations preserve identity. `as_of` is excluded because later inspection of the same completed snapshot is not a new analytical input.

### Analytical decision model

`DecisionType` contains exactly `ENTER_LONG`, `EXIT_LONG`, `ENTER_SHORT`, `EXIT_SHORT`, `NO_ACTION`. There is no `HOLD`. NO_ACTION means no analytical action; it does not assert that an existing position should be held or that the account is in cash.

`StrategyDecision(observation, decision, reason_codes, evidence)` derives family/version/configuration/input identity and its UTC timestamp from the validated input. Timestamp is the current candle's close time, never generated wall-clock time. It contains:

- strategy family/version;
- timestamp and decision type;
- an explicit ordered tuple of distinct deterministic reason codes;
- deeply immutable canonical-compatible evidence with Decimal values intact;
- configuration, input and decision identities.

`decision_identity` is SHA-256 over versioned `strategy.decision/v1` data covering all these semantic fields and the full input identity. Identical analytical snapshots/output/configuration reproduce the same identity. Changing a decision, reason codes, evidence, configuration or analytical snapshot changes it. No profit probability is included.

The closed, frozen, slotted schema has no order, TradeIntent, quantity, position size, venue account or execution authority fields. Configuration/evidence also reject reserved execution/order/sizing/credential/profit-probability keys, including nested mappings. These checks enforce the intended data contract; they are not a semantic or security sandbox for arbitrary Python or free-text values.

Long and short entry/exit descriptions are independent of execution venue capability. A SHORT decision can exist for an instrument whose initial Quidax spot execution would not support shorting. No venue lookup or execution eligibility is evaluated here. Future consumers can represent LONG/SHORT/CASH exposure separately; this contract neither infers current exposure nor turns NO_ACTION into a CASH/HOLD assertion.

### Reusable strategy protocol

`Strategy` requires an explicit immutable `definition` and `decide(StrategyInput) -> StrategyDecision`. Implementations must be deterministic and may use only the supplied current/past canonical analytical information. They must not call providers/databases/venues, place orders, size positions, use clocks/randomness or obtain future observations.

`evaluate_strategy` validates one input/definition pair, invokes the supplied contract once and verifies the returned decision's type, context identity, configuration and timestamp binding. It does not select a family, calculate thresholds, run a backtest, authorize execution or call external services. Structural typing cannot stop a malicious implementation from performing external I/O; runtime isolation is not implemented. Determinism is an implementation obligation, verified with repeated-input tests, not inferred from an interface alone.

The unconditional NO_ACTION implementation remains a test fixture. Part 2 supplies Adaptive Trend directly, without a strategy registry.

### Module and test scope

```text
backend/app/strategies/
  __init__.py
  configuration.py  # Immutable strategy metadata/parameters and reason-code validation
  inputs.py         # Canonical single-observation validation and identity
  decisions.py      # Five analytical decision types and deterministic identity
  contract.py       # Protocol and single-input/output boundary checks
```

Focused offline tests cover deep immutability, five decision types, NO_ACTION, configuration identity, malformed/mismatched/incomplete/future inputs, exact timestamps/Decimal values, execution-field rejection, confirmation-streak compatibility, repeated identities and fresh-process imports without providers/database/API modules. Existing 9A tests remain unchanged. Part 1 itself adds no family rules. No API, frontend, migration, persisted decision, profit model, sizing, TradeIntent, Risk Engine or order/paper/live execution is added by either contract or family.

## Milestone 9B Part 2 — ADAPTIVE_TREND v1.0.0

`AdaptiveTrendStrategy` is the first concrete, stateless strategy. It identifies analytical opportunities and directional-thesis invalidation from a single validated StrategyInput. It does not execute, size positions, predict profit or calculate indicators. No parameters were selected/tuned against the current stored BTC/ETH observations. Every initial threshold below is an unvalidated research hypothesis for later empirical evaluation.

### Minimal thesis-state extension

`PositionState` contains exactly `CASH`, `LONG`, `SHORT`. StrategyInput adds only `position_state`, defaulting to CASH for constructor compatibility. It records the caller-declared analytical thesis; it is not an account holding or a position database model and contains no size, P&L or execution information. Callers evaluating an existing directional thesis must explicitly supply LONG/SHORT.

Input identity changes from `strategy.input/v1` to **`strategy.input/v2`** and includes state. Consequently even legacy/default-CASH inputs receive a new identity namespace; decision identities change transitively. Configuration/decision serialization formats are otherwise unchanged. Identical snapshots with different thesis states have different input identities. Existing Part 1 tests remain unmodified; no other Part 1 production code changes.

### Central configuration (defaults)

All configurable hypotheses reside in immutable `AdaptiveTrendConfiguration`; all values below except confirmation counts are Decimal. The full configuration enters the existing StrategyDefinition identity under family `ADAPTIVE_TREND`, version `1.0.0`.

| Parameter | Default | Convention |
| --- | --- | --- |
| minimum_regime_confirmations | 3 | Required streak is max(this, source confirmation_required) |
| entry_min_ema_spread | 0.0015 | Signed EMA20–EMA50 separation normalized by close |
| entry_fast_slope | 0.0002 | Minimum directional EMA20 slope from regime evidence |
| entry_slow_slope | 0.0001 | Minimum directional EMA50 slope from regime evidence |
| long_rsi_min / long_rsi_max | 55 / 70 | Inclusive long-entry RSI band |
| short_rsi_min / short_rsi_max | 30 / 45 | Inclusive short-entry RSI band |
| entry_min_normalized_histogram | 0.0001 | Minimum directional MACD histogram / close |
| max_normalized_atr | 0.02 | Maximum ATR14 / close |
| max_realized_volatility | 0.02 | Maximum existing unannualized realized volatility |
| max_volatility_ratio | 2 | Maximum current volatility / prior baseline from regime evidence |
| max_atr_ratio | 2 | Maximum normalized ATR / prior baseline from regime evidence |
| exit_confirmation_dimensions | 2 | Two or three deterioration dimensions permitted |
| exit_min_ema_spread | 0.0005 | Minimum reversed normalized EMA spread magnitude |
| exit_fast_slope / exit_slow_slope | 0.0001 / 0.00005 | Minimum reversed EMA slope magnitudes |
| exit_long_rsi / exit_short_rsi | 50 / 50 | Directional deterioration thresholds |
| exit_min_normalized_histogram | 0.00005 | Minimum reversed normalized histogram magnitude |

Configurations reject non-Decimal, non-finite or non-positive numeric thresholds, invalid RSI bands, non-positive/bool confirmation counts and exit dimension counts outside 2–3. Indicator windows and slope/baseline conventions are inherited from the supplied intelligence/regime output and not recalculated. These defaults are uniform across instruments/timeframes; suitability is not established. Normalized thresholds avoid raw dollar cutoffs. All comparison logic uses Decimal values, with no float conversion or context-dependent financial arithmetic.

### Deterministic decision precedence

1. Require the matching typed StrategyDefinition and validated StrategyInput.
2. UNSAFE → NO_ACTION / DATA_UNSAFE. HIGH_VOLATILITY → NO_ACTION / HIGH_VOLATILITY_REGIME. Both gates apply in all thesis states. Operational risk liquidation is not this strategy's responsibility.
3. From CASH, RANGE/UNKNOWN/BREAKOUT → NO_ACTION / REGIME_NOT_TRENDING. BREAKOUT alone never creates an entry.
4. Require existing normalized spread, fast/slow slopes, normalized MACD histogram, normalized ATR, volatility ratio and ATR ratio. Missing values produce ANALYTICAL_EVIDENCE_INCOMPLETE; negative ATR/relative volatility ratios produce ANALYTICAL_EVIDENCE_INVALID. No fallback values are invented.
5. CASH → evaluate entry agreement; LONG/SHORT → evaluate only existing-thesis invalidation. An exit cannot return a simultaneous opposite entry.

### Entry rules — all six dimensions required

A directional regime is confirmed only when the published regime and candidate both equal the required BULL_TREND/BEAR_TREND, `retained_previous` is false, source confirmation_required is positive, confirmation_count meets both source and strategy requirements, and available_candles meets the positive candidate_history_required. A retained old regime, conflicting candidate or pending confirmation cannot create an entry.

ENTER_LONG from CASH requires:

1. Confirmed BULL_TREND as defined above.
2. EMA20 > EMA50 and normalized spread >= 0.0015.
3. EMA20 slope >= 0.0002 and EMA50 slope >= 0.0001.
4. RSI in [55,70], MACD > 0, MACD > signal, histogram > 0 and normalized histogram >= 0.0001.
5. Close strictly above both EMAs.
6. Normalized ATR <= 0.02, unannualized realized volatility <= 0.02, volatility ratio <= 2 and ATR ratio <= 2.

ENTER_SHORT is symmetric: confirmed BEAR_TREND; EMA20 < EMA50 with spread <= -0.0015; slopes <= -0.0002/-0.0001; RSI in [30,45]; MACD < 0, MACD < signal, histogram < 0 and normalized histogram <= -0.0001; close below both EMAs; the same volatility ceilings.

RSI above 70 is not an automatic long entry; RSI below 30 is not an automatic short entry. Under default bands both instead fail entry momentum confirmation. A matching existing LONG/SHORT never receives a duplicate entry. SHORT descriptions are not blocked by Quidax spot capability: this is a venue-neutral analytical decision, not permission to short live.

### Exit rules — trend invalidation, not profit taking

For an existing LONG, return EXIT_LONG if either a fully confirmed BEAR_TREND exists, or at least two (configurable to three) of these dimensions agree:

- Structure: EMA20 <= EMA50, normalized spread <= -0.0005, and close below EMA50.
- Slopes/price: EMA20 slope <= -0.0001, EMA50 slope <= -0.00005, and close below EMA20.
- Momentum: RSI <= 50, MACD < signal, histogram < 0, and normalized histogram <= -0.00005.

For an existing SHORT, return EXIT_SHORT on a fully confirmed BULL_TREND or the mirrored two-of-three deterioration: EMA20 >= EMA50, spread >= 0.0005 and close above EMA50; slopes >= 0.0001/0.00005 and close above EMA20; RSI >= 50, MACD > signal, histogram > 0 and normalized histogram >= 0.00005.

An opposite regime is confirmed using the same source/candidate/streak/history check as entry. The alternative deterioration test can invalidate a thesis while the old regime is retained, or while UNKNOWN/RANGE/BREAKOUT has valid evidence; those regimes alone never force an exit. One weak dimension normally produces NO_ACTION. A configured three-dimension requirement demands all three unless the opposite regime is already confirmed. UNSAFE/HIGH_VOLATILITY remain NO_ACTION even here.

Entry volatility ceilings are not standalone exit triggers. Neither extreme RSI nor profit targets/P&L create an exit. There are no stops, risk sizes, position ledger updates or automatic reversals. State remains caller-owned; a returned decision does not change the supplied LONG/SHORT/CASH value.

### Reasons and inspection evidence

Successful entries use LONG_TREND_CONFIRMED / SHORT_TREND_CONFIRMED. Failed entry dimensions appear in deterministic order as REGIME_NOT_CONFIRMED, EMA_STRUCTURE_NOT_CONFIRMED, EMA_SLOPES_NOT_CONFIRMED, MOMENTUM_NOT_CONFIRMED, CLOSE_POSITION_NOT_CONFIRMED, VOLATILITY_TOO_HIGH.

Exit decisions use LONG_TREND_INVALIDATED / SHORT_TREND_INVALIDATED, optionally OPPOSITE_TREND_CONFIRMED, followed by the agreeing EXIT_STRUCTURE_CONFIRMED, EXIT_SLOPES_CONFIRMED and EXIT_MOMENTUM_CONFIRMED codes. Insufficient exit agreement uses LONG_EXIT_NOT_CONFIRMED / SHORT_EXIT_NOT_CONFIRMED. Gate/quality reasons are listed in precedence above.

Evidence preserves current thesis state, regime/candidate/confirmation metadata, relevant existing measurements, entry dimension booleans or exit dimension booleans/counts, and opposite-regime confirmation. StrategyDecision uses the unchanged Part 1 immutable evidence/identity machinery. No profit probability, order fields, execution authority, venue/account information or financial sizing is included.

### Module and limitations

```text
backend/app/strategies/adaptive_trend/
  __init__.py
  config.py     # Explicit v1 hypotheses and validation
  strategy.py   # Stateless opportunity/invalidation decisions
```

Synthetic deterministic tests cover entries, exits, all conservative regimes, candidate/persistence disagreement, weak momentum, price/slope/volatility filters, exact threshold boundaries, extreme RSI behavior, already-active theses, one-vs-multiple deterioration dimensions, state/configuration identity, no look-ahead, repeatability, Decimal context/scale behavior, immutability and fresh-process imports without providers/database modules. Current real BTC/ETH classifications are not used to tune parameters.

This is research logic only. It has no empirical profitability evidence, fee-aware optimization, execution timing model, backtest loop, strategy-level extra persistence counter, position sizing, portfolio integration, API, frontend, persistence/migration, TradeIntent, Risk Engine or paper/live execution. Existing Regime Engine persistence is reused as supplied. Subsequent validation must assess timeframe suitability, data quality, costs and sensitivity without hindsight.

## Milestone 9B Part 3 — adversarial validation guarantees

Part 3 adds tests and this evidence-based documentation only. ADAPTIVE_TREND remains v1.0.0 with byte-for-byte unchanged production strategy, configuration, contract and input code. No threshold tuning, new family, profitability evaluation or run against current BTC/ETH strategy observations is performed. Review found no genuine rule contradiction requiring a production fix.

### Adversarial and boundary coverage

The test corpus challenges clean/weak bull and bear structures, flat/range conditions, unestablished breakout, independently conflicting RSI/MACD, inadequate EMA separation/slopes, excessive absolute volatility, baseline-ratio shock, retained candidates, short confirmation streaks, incomplete evidence, continuing directional theses, partial deterioration, multi-dimensional invalidation and confirmed opposite trends. Some tests intentionally supply conflicting analytical snapshots to verify defensive agreement checks; they are not claims about feasible market performance or reconstructed price histories.

Every requested entry/exit threshold is tested at the exact configured Decimal plus the adjacent representable Decimal below and above it in an explicit **50-significant-digit ROUND_HALF_EVEN context**. Decimal numbers have no universal immediate neighbor; the guarantee is relative to that declared precision. Test construction uses enough precision to preserve the neighbors rather than rounding them back onto the threshold.

Results match v1 semantics:

- Entry separation, directional slopes and directional normalized histogram minimum magnitudes are inclusive. The immediately smaller magnitude fails; equality and the immediately larger magnitude pass when other dimensions agree.
- Both RSI entry endpoints are inclusive. The adjacent value outside each band fails momentum confirmation.
- ATR, realized-volatility and both baseline-ratio ceilings are inclusive. The adjacent value above each ceiling blocks entry.
- Exit spread, reversed slopes and reversed histogram minimum magnitudes are inclusive. Each exit test anchors exactly one other deterioration dimension and suppresses the third, proving that crossing the tested boundary changes the decision, not just an internal flag.
- Exit RSI thresholds are inclusive in their respective directions. Price beyond the fast EMA is strict: equality cannot create an entry.
- Confirmation streak 2/3 is insufficient, 3/3 passes, and a longer streak remains valid.

Both LONG and SHORT mirrors are exercised. Additional reachability tests demonstrate all five decision types, data-quality failure paths and the confirmed-opposite exit branch with a three-dimension configuration even when only two deterioration dimensions agree. Test-only alternate configurations verify identity/branch behavior; the production defaults are never changed or selected based on outcomes.

### Explicit position-state matrix

For the representative synthetic snapshots used in the matrix:

| Published regime | CASH | LONG thesis | SHORT thesis |
| --- | --- | --- | --- |
| BULL_TREND (clean bull) | ENTER_LONG | NO_ACTION | EXIT_SHORT |
| BEAR_TREND (clean bear) | ENTER_SHORT | EXIT_LONG | NO_ACTION |
| RANGE (flat) | NO_ACTION | NO_ACTION | NO_ACTION |
| UNKNOWN (flat/insufficient directional evidence) | NO_ACTION | NO_ACTION | NO_ACTION |
| BREAKOUT (upward structure + momentum, weak slopes) | NO_ACTION | NO_ACTION | EXIT_SHORT |
| HIGH_VOLATILITY | NO_ACTION | NO_ACTION | NO_ACTION |
| UNSAFE | NO_ACTION | NO_ACTION | NO_ACTION |

This is a fixture-specific matrix, not a regime-only rule table. In particular the upward BREAKOUT invalidates SHORT through two independently agreeing deterioration dimensions; the label alone neither enters nor exits. Different valid evidence can change an existing-thesis exit under RANGE/UNKNOWN/BREAKOUT, as Part 2 documents. All 21 combinations have explicit expected outputs; an additional full state × regime × bull/bear/flat evidence cross-product checks allowed-decision invariants.

CASH can produce only ENTER_LONG, ENTER_SHORT or NO_ACTION. LONG can produce only EXIT_LONG or NO_ACTION. SHORT can produce only EXIT_SHORT or NO_ACTION. Caller state is unchanged. No decision simultaneously exits and reverses, and no existing matching thesis receives a duplicate entry.

### Determinism, causality and dependency checks

Tests confirm equal decision identities for repeated inputs, reordered parameter/evidence dictionaries and equivalent Decimal scales across configuration, candles, measurements and regime evidence/configuration. Every configuration field is independently perturbed with a valid test-only value and changes configuration/input/decision identity. Distinct thesis states produce distinct identities even when all decisions are NO_ACTION. Ambient Decimal precision/rounding changes do not alter already-constructed input decisions.

Appending and inspecting future synthetic observations on the same strategy instance cannot rewrite the earlier decision at T. A separate integration test calculates the existing intelligence/regime engines over a prefix and over that same prefix plus extreme future candles: earlier analytical rows and decision identities remain identical. Substituting a later analytical row into the input at T is rejected. These are causality checks, not a backtest: no fills, account state, exposure accounting or returns are generated.

During all named scenario decisions, network/provider entry points and database/session methods are patched to raise if touched. Fresh-process imports load no provider/database/API dependencies. A source AST audit checks for venue literals/imports, order/TradeIntent constructors and execution, sizing, balance/P&L access. The input schema does not accept balance/P&L fields. These tests establish the current trusted implementation's behavior; they do not sandbox arbitrary Python or prove arbitrary future code safe.

### Remaining limitations

Synthetic boundary/adversarial coverage is not a mathematical proof for every possible input, a statistical validation, an execution-safety authorization or evidence of profitability. Static declared snapshots test the strategy's agreement gates; only the separate prefix test derives measurements from synthetic raw candles. Upstream analytical provenance and caller-supplied thesis state remain trusted inputs. Exits use agreement at T rather than a new multi-candle persistence model. Backtesting, cost-adjusted evaluation, parameter sensitivity research, execution and empirical testing remain outside Part 3.
