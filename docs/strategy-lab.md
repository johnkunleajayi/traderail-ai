# Strategy Lab foundation â€” Milestone 9A and 9B Parts 1â€“3

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

## Part 2 â€” immutable research cost assumptions

`ResearchCostModel` requires explicit Decimal `trading_fee_rate`, `spread_rate` and `slippage_rate`; each is a finite fraction in [0, 1]. No real Binance/Quidax fees or exchange defaults are assumed. `execution_delay` is a nonnegative `timedelta` (default zero), retained as an assumption only. It does not shift timestamps, find a future candle, create fills or simulate execution.

`costs_for_notional` accepts a caller-supplied nonnegative Decimal notional for one hypothetical execution leg. Fees = notional Ã— fee rate. Spread is explicitly the **full bid/ask spread fraction**; spread cost = notional Ã— spread rate Ã— 0.5 per leg. Slippage cost = notional Ã— slippage rate. Entry and exit notionals must be supplied separately by a future consumer; nothing infers orders, side, fill price, volume or realized slippage. CostBreakdown preserves fees, spread and slippage separately. All are nonnegative; rebates are outside this model.

`configuration()` returns a detached mapping, including cost-model version `1.0.0`, spread convention and exact integer delay microseconds. It is compatible with Part 1 configuration snapshots. Part 1 experiment/schema/fingerprinting code is unchanged; costs are not silently injected into existing identities. Part 3â€™s `create_research_experiment` explicitly binds these cost assumptions, validation and benchmarks into existing configuration snapshots.

## Part 2 â€” metric semantics

Pure functions in `app.strategy_lab.metrics` accept supplied realized accounting observations/equity/durations/notionals. They do not calculate a strategy, process candles or maintain an execution state. `RealizedTrade` records signed gross P&L and a CostBreakdown; net P&L = gross âˆ’ fees âˆ’ spread âˆ’ slippage. All monetary observations must use one consistent accounting currency. There is no FX conversion or currency inference.

| Primitive | Exact convention |
| --- | --- |
| `total_return(initial, ending_equity)` | (ending equity âˆ’ initial capital) / initial capital; simple equity return, no deposit/withdrawal adjustment |
| `gross_return(initial, trades)` | Sum realized gross P&L / initial capital |
| `net_return(initial, trades)` | Sum realized net P&L / initial capital |
| `realized_pnl_aggregation(trades)` | Separate totals: gross P&L, fees, spread, slippage, net P&L |
| `drawdown_series(equity)` | At each supplied point: (running peak âˆ’ equity) / running peak; nonnegative fraction, using only that point and earlier points |
| `maximum_drawdown(equity)` | Largest defined drawdown in the supplied sequence |
| `trade_count(trades)` | Number of supplied realized observations, including breakeven outcomes |
| `win_rate(trades)` | Count strictly positive **net** outcomes / total observations |
| `loss_rate(trades)` | Count strictly negative **net** outcomes / total observations |
| `profit_factor(trades)` | Sum positive net P&L / absolute sum negative net P&L |
| `average_win(trades)` | Mean strictly positive net P&L |
| `average_loss(trades)` | Mean absolute magnitude of strictly negative net P&L (positive monetary amount) |
| `expectancy(trades)` | Mean net P&L across all observations, including breakeven; money per trade, not a prediction |
| `exposure_percentage(exposed, evaluation_duration)` | 100 Ã— exposed duration / evaluation duration; exact microsecond durations, no sample-count assumption |
| `turnover(notionals, initial)` | Sum unsigned executed notionals / initial capital; every leg counts, no halving or netting |
| `total_fees`, `total_spread_cost`, `total_slippage_cost` | Sum the corresponding explicit cost components |

Returns, drawdowns, win/loss rates and turnover are ratios, not percentage points. Only `exposure_percentage` returns 0â€“100 percentage points. Turnover can exceed 1. Trade statistics use net outcomes so transaction costs can turn a gross win into a net loss. Breakeven observations are neither wins nor losses, but remain in the rate/expectancy denominators. Win rate is not evidence of profitability and is not optimized here.

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

## Part 3 â€” chronological validation definitions

`TimeWindow(start, end)` is an immutable UTC-normalized, aware interval with inclusive start and exclusive end. Empty, reversed and naive intervals are invalid. Chronology is compared after UTC conversion, including repeated local-hour offsets. Nothing is shuffled or silently sorted.

`ValidationSplit` pairs an explicit in-sample window with an optional later OOS window. Their intervals cannot overlap. Its `configuration_cutoff` is the exclusive end of the in-sample window: only completed observations before this cutoff are eligible to inform this splitâ€™s earlier parameter/configuration selection. OOS data must not inform that earlier selection. The timestamp is a data-availability boundary, not proof that a human selected parameters without hindsight; informational experiment creation time is not such proof either.

`ValidationPlan` supports exactly:

| Kind | Definition | Declared evaluation windows |
| --- | --- | --- |
| IN_SAMPLE | One in-sample-only window | That in-sample window |
| OUT_OF_SAMPLE | One in-sample window followed by one OOS window | OOS only |
| WALK_FORWARD | At least two explicitly supplied in-sample/OOS pairs | Each pairâ€™s OOS window, chronologically |

Walk-forward is deliberately conservative: each complete fold must end at or before the next foldâ€™s in-sample start. No in-sample or OOS candle is reused across folds. Expanding or rolling overlapping training windows are not supported in this version. Forward-only order is required; reverse or overlapping input is rejected rather than reordered. Adjacent half-open windows are valid. Explicit unused intervals are permitted, remain visible in configuration boundaries, and are never silently filled or evaluated. There is no automatic fold generation, optimization or retraining.

`validate_for(dataset)` requires all windows to lie within the datasetâ€™s declared evaluation span and use actual candle boundaries. Warm-up is excluded from both split views. `in_sample_candles(dataset, index)` exposes only the declared in-sample rows; `out_of_sample_candles` exposes only the corresponding OOS rows. Changing later OOS candles does not change the earlier in-sample view. No hidden history is fetched or interpolated. Consumers must use the explicitly supplied warm-up/causal history for indicator initialization and must freeze parameter choices using only permitted earlier data. No runner yet enforces arbitrary external consumersâ€™ behavior.

Validation version is `1.0.0`, with explicit `inclusive_start_exclusive_end` and `strictly_disjoint_chronological` conventions. Its deterministic configuration contains all windows, cutoffs and evaluation periods.

## Part 3 â€” comparison benchmarks

`BenchmarkKind` has exactly `CASH` and `BUY_AND_HOLD`. Immutable `BenchmarkDefinition` exposes Decimal market exposure:

- CASH: `0`, meaning no market exposure. It does not assert any interest yield, cash return or execution outcome.
- BUY_AND_HOLD: `1`, meaning passive full, unlevered exposure over each declared validation evaluation period. It does not generate entries, exit prices, trades, returns or recommendations.

`BenchmarkConfiguration` requires an explicit non-empty set of definitions, rejects duplicates, and canonicalizes membership order. Both benchmarks may be selected together. Benchmark version is `1.0.0`.

When bound through `ResearchConfiguration`, benchmark evaluation windows are taken exactly from `ValidationPlan.evaluation_windows`: IN_SAMPLEâ€™s window, the OOS window, or each walk-forward OOS window. They do not accidentally extend into warm-up or the paired training period. Costs are recorded alongside benchmark definitions, but are not applied or simulated. Future comparison execution must use the same period/currency assumptions; no pooled fold results or benchmark equity curves are calculated here.

## Part 3 â€” strategy lifecycle labels

`StrategyState` represents exactly `EXPERIMENTAL`, `BACKTESTED`, `VALIDATED`, `PAPER`, `LIVE_CANDIDATE`, `SHADOW`, `LIMITED_LIVE`, `LIVE`, `SUSPENDED`, `RETIRED`.

`StrategyLifecycle` is an immutable record requiring an explicitly supplied enum state. There is no promotion/transition method, scheduler, approval workflow or automatic mutation. Creating experiments or metrics never changes lifecycle state. The labels do not grant execution permission and do not certify evidence of validation. No transition policy or live functionality is implemented.

## Part 3 â€” research identity integration

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

## Milestone 9B Part 1 â€” pure strategy contract

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

## Milestone 9B Part 2 â€” ADAPTIVE_TREND v1.0.0

`AdaptiveTrendStrategy` is the first concrete, stateless strategy. It identifies analytical opportunities and directional-thesis invalidation from a single validated StrategyInput. It does not execute, size positions, predict profit or calculate indicators. No parameters were selected/tuned against the current stored BTC/ETH observations. Every initial threshold below is an unvalidated research hypothesis for later empirical evaluation.

### Minimal thesis-state extension

`PositionState` contains exactly `CASH`, `LONG`, `SHORT`. StrategyInput adds only `position_state`, defaulting to CASH for constructor compatibility. It records the caller-declared analytical thesis; it is not an account holding or a position database model and contains no size, P&L or execution information. Callers evaluating an existing directional thesis must explicitly supply LONG/SHORT.

Input identity changes from `strategy.input/v1` to **`strategy.input/v2`** and includes state. Consequently even legacy/default-CASH inputs receive a new identity namespace; decision identities change transitively. Configuration/decision serialization formats are otherwise unchanged. Identical snapshots with different thesis states have different input identities. Existing Part 1 tests remain unmodified; no other Part 1 production code changes.

### Central configuration (defaults)

All configurable hypotheses reside in immutable `AdaptiveTrendConfiguration`; all values below except confirmation counts are Decimal. The full configuration enters the existing StrategyDefinition identity under family `ADAPTIVE_TREND`, version `1.0.0`.

| Parameter | Default | Convention |
| --- | --- | --- |
| minimum_regime_confirmations | 3 | Required streak is max(this, source confirmation_required) |
| entry_min_ema_spread | 0.0015 | Signed EMA20â€“EMA50 separation normalized by close |
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

Configurations reject non-Decimal, non-finite or non-positive numeric thresholds, invalid RSI bands, non-positive/bool confirmation counts and exit dimension counts outside 2â€“3. Indicator windows and slope/baseline conventions are inherited from the supplied intelligence/regime output and not recalculated. These defaults are uniform across instruments/timeframes; suitability is not established. Normalized thresholds avoid raw dollar cutoffs. All comparison logic uses Decimal values, with no float conversion or context-dependent financial arithmetic.

### Deterministic decision precedence

1. Require the matching typed StrategyDefinition and validated StrategyInput.
2. UNSAFE â†’ NO_ACTION / DATA_UNSAFE. HIGH_VOLATILITY â†’ NO_ACTION / HIGH_VOLATILITY_REGIME. Both gates apply in all thesis states. Operational risk liquidation is not this strategy's responsibility.
3. From CASH, RANGE/UNKNOWN/BREAKOUT â†’ NO_ACTION / REGIME_NOT_TRENDING. BREAKOUT alone never creates an entry.
4. Require existing normalized spread, fast/slow slopes, normalized MACD histogram, normalized ATR, volatility ratio and ATR ratio. Missing values produce ANALYTICAL_EVIDENCE_INCOMPLETE; negative ATR/relative volatility ratios produce ANALYTICAL_EVIDENCE_INVALID. No fallback values are invented.
5. CASH â†’ evaluate entry agreement; LONG/SHORT â†’ evaluate only existing-thesis invalidation. An exit cannot return a simultaneous opposite entry.

### Entry rules â€” all six dimensions required

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

### Exit rules â€” trend invalidation, not profit taking

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

## Milestone 9B Part 3 â€” adversarial validation guarantees

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

This is a fixture-specific matrix, not a regime-only rule table. In particular the upward BREAKOUT invalidates SHORT through two independently agreeing deterioration dimensions; the label alone neither enters nor exits. Different valid evidence can change an existing-thesis exit under RANGE/UNKNOWN/BREAKOUT, as Part 2 documents. All 21 combinations have explicit expected outputs; an additional full state Ã— regime Ã— bull/bear/flat evidence cross-product checks allowed-decision invariants.

CASH can produce only ENTER_LONG, ENTER_SHORT or NO_ACTION. LONG can produce only EXIT_LONG or NO_ACTION. SHORT can produce only EXIT_SHORT or NO_ACTION. Caller state is unchanged. No decision simultaneously exits and reverses, and no existing matching thesis receives a duplicate entry.

### Determinism, causality and dependency checks

Tests confirm equal decision identities for repeated inputs, reordered parameter/evidence dictionaries and equivalent Decimal scales across configuration, candles, measurements and regime evidence/configuration. Every configuration field is independently perturbed with a valid test-only value and changes configuration/input/decision identity. Distinct thesis states produce distinct identities even when all decisions are NO_ACTION. Ambient Decimal precision/rounding changes do not alter already-constructed input decisions.

Appending and inspecting future synthetic observations on the same strategy instance cannot rewrite the earlier decision at T. A separate integration test calculates the existing intelligence/regime engines over a prefix and over that same prefix plus extreme future candles: earlier analytical rows and decision identities remain identical. Substituting a later analytical row into the input at T is rejected. These are causality checks, not a backtest: no fills, account state, exposure accounting or returns are generated.

During all named scenario decisions, network/provider entry points and database/session methods are patched to raise if touched. Fresh-process imports load no provider/database/API dependencies. A source AST audit checks for venue literals/imports, order/TradeIntent constructors and execution, sizing, balance/P&L access. The input schema does not accept balance/P&L fields. These tests establish the current trusted implementation's behavior; they do not sandbox arbitrary Python or prove arbitrary future code safe.

### Remaining limitations

Synthetic boundary/adversarial coverage is not a mathematical proof for every possible input, a statistical validation, an execution-safety authorization or evidence of profitability. Static declared snapshots test the strategy's agreement gates; only the separate prefix test derives measurements from synthetic raw candles. Upstream analytical provenance and caller-supplied thesis state remain trusted inputs. Exits use agreement at T rather than a new multi-candle persistence model. Backtesting, cost-adjusted evaluation, parameter sensitivity research, execution and empirical testing remain outside Part 3.


## Milestone 9C Part 1 â€” causal replay foundation v1.0.0

`app.strategy_lab.replay.replay_dataset(dataset, strategy, *, as_of, initial_state=CASH,
regime_configuration=REGIME_CONFIG)` is a pure synchronous research operation. It accepts an
existing immutable `ResearchDataset` and a strategy implementing the shared `Strategy` protocol,
including ADAPTIVE_TREND v1.0.0. It has no database, provider, HTTP, execution or metrics dependency.

### Validation and dataset boundaries

Before evaluating any strategy observation, replay re-runs the existing dataset factory validation
and verifies the frozen fingerprint. The supplied tuple must exactly cover the declared warm-up
start through exclusive evaluation end. Unordered rows, duplicates, internal gaps, incomplete
candles, mixed context, invalid OHLCV, out-of-bound observations, changed contents/boundaries and
future retrieval relative to the caller's archival cutoff are rejected. Nothing is sorted, repaired,
interpolated, fetched or silently prepended. Errors reject the run rather than returning partial results.

Evaluation must begin no earlier than `candidate_first_index(regime_configuration)`, derived from
the existing intelligence and regime configuration. Currently that index is **52**: 52 warm-up
candles followed by the first evaluation candle (53 observations inclusive). This accommodates
all strategy-required measurements plus regime slope/structure/volatility history. It does not
require a confirmed directional regime: the first eligible candidate can still be UNKNOWN while
confirmation accumulates. Additional explicitly supplied warm-up is allowed and affects identity.
No warm-up candle creates a replay decision or changes thesis state.

### Exact causal evaluation sequence

For each evaluation candle T, in supplied chronological order:

1. Slice only the frozen prefix from the declared warm-up start through T, inclusive.
2. Call the existing Market Intelligence calculation engine on that prefix. Its original
   dataset-start initialization rules are retained; there is no hidden stabilization history.
3. Pass only those feature rows through the existing Regime Engine, preserving its historical
   confirmation state and UNKNOWN/UNSAFE semantics. No indicators or regime rules are duplicated.
4. Construct the shared validated `StrategyInput` from T's candle, T's feature row, T's regime,
   the declared strategy definition and the current research thesis state.
5. Invoke `evaluate_strategy` through the shared contract. Apply only the explicit thesis transition.
6. Append an immutable provenance record. Proceed to the next supplied evaluation candle.

No full-dataset analytics are passed to the strategy. It receives a single validated observation,
not the frozen snapshot, future rows or future dataset boundaries. Prefixes are recalculated
transparently for this first foundation: work is quadratic in dataset length; no incremental
optimization or large-history performance claim is made. Strategies remain trusted pure Python
implementations; structural typing is not a security sandbox against malicious side effects or
external closure state.

### Archival time versus candle time

`as_of` is an explicit validation cutoff, never a call to the system clock. Historical candles may
have been retrieved after their market interval ended. Their original `retrieved_at` values are
preserved, not rewritten to suggest real-time availability. For each prefix, the analytical/input
cutoff is the later of T's completion boundary and the latest retrieval timestamp **in that prefix**.
The prefix itself strictly limits market information through T. This is retrospective historical
research, not an exchange-publication-latency or real-time delivery simulation. The informational
caller cutoff does not enter replay identity; changing it without changing admissible data cannot
change analytical results. Event timestamps remain the original completed candle timestamps.

### Thesis-state transitions (not fills)

| State before | Decision | State after |
| --- | --- | --- |
| CASH | ENTER_LONG | LONG |
| CASH | ENTER_SHORT | SHORT |
| LONG | EXIT_LONG | CASH |
| SHORT | EXIT_SHORT | CASH |
| Any | NO_ACTION | Unchanged |

Every other transition is rejected, including duplicate entries, exits from CASH, opposite-direction
exits and automatic reversal. The default initial state is CASH; a caller can explicitly declare
LONG or SHORT without supplying quantity, balance, price or P&L. An entry decision updates a research
thesis immediately for the next observation; it does **not** imply an order, fill or executable price.
The cost model's delay assumption is not applied by this stage.

### Reproducibility and immutable provenance

The `ReplayResult` envelope retains the **exact full dataset fingerprint**, canonical context,
explicit boundaries/counts, strategy definition/configuration identity, complete feature/regime
configurations, replay version, initial/final thesis states, immutable records and a run identity.
Each `ReplayRecord` retains its exact **causal-prefix dataset fingerprint**, canonical context,
strategy family/version/configuration identity, candle times, state before/after, full intelligence
row plus intelligence identity, full regime result/configuration/version, and the original
StrategyDecision (including input and decision identities). No order/execution fields are added.

Full snapshot identity necessarily changes when future history or evaluation end changes. Therefore
that future-sensitive identity belongs to the envelope, while earlier records use prefix identity.
Appending future observations preserves earlier record objects/identities exactly, provided original
prefix contents, retrieval metadata, evaluation start, configuration and initial state are unchanged.
The envelope binds those records back to the full frozen snapshot without leaking future provenance
into earlier record identity. Record timestamps and analytical rows remain inspectable.

All identities use the existing type-tagged canonical serialization and SHA-256 namespaces, never
Python `hash()`. The feature identity binds prefix, complete feature configuration and exact feature
row. Record identity binds replay version, prefix, feature identity, original decision identity and
resulting thesis state. Decision identity already binds the exact input, full regime evidence and
strategy configuration. Run identity additionally binds the full dataset, both analytical
configurations, strategy configuration, initial state and ordered record identities. Decimal scale
and dictionary order do not alter numeric/configuration identity. No binary floats are introduced.

### Tests and exclusions

Synthetic tests verify causal prefix arguments at both engines and the shared strategy contract,
actual Adaptive Trend reuse, exact frozen/prefix provenance, warm-up exclusion/pending confirmation,
all 15 state Ã— decision transitions, deterministic replay, future append/price-change invariance,
input immutability, configuration/state identity changes, supported timeframes, small Decimal prices,
archival retrieval semantics, malformed/tampered snapshots and fresh-process dependency isolation.

There are no fills, simulated orders, entry/exit prices, cost application, P&L, equity curves,
performance/benchmark calculations, optimization, walk-forward execution, APIs, frontend changes,
persistence or migrations. This stage proves causal decision replay, not profitability or execution
realism. Later 9C/9D work must make those assumptions explicit rather than infer them from thesis states.


## Milestone 9C Part 2 â€” deterministic historical execution simulation v1.0.0

`app.strategy_lab.execution.simulate_execution(dataset, replay, *, quantity, cost_model, as_of)`
consumes the frozen research snapshot and its existing causal replay. It creates immutable
historical **research fills**, not orders or TradeIntents. It does not call or recalculate a strategy,
Market Intelligence or regimes, and has no provider/database/API/frontend integration. Part 1 is unchanged.

### Next-open timing and explicit delay

A decision based on completed candle T cannot fill at T's close. With zero additional delay,
the reference is the **next completed stored candle's OPEN**. For a decision at dataset index i:

`eligible_index = i + 1 + additional_delay_candles`

The fill event timestamp is that candle's UTC open time. The historical row must be completed
at the explicitly supplied archival validation cutoff. High, low and close validate the source row
but never select/improve the price. The timing rule is versioned as `NEXT_COMPLETED_CANDLE_OPEN`.
A bar open is a simplified historical reference, not a claim that it was a guaranteed executable quote.

The existing ResearchCostModel stores `execution_delay` as a nonnegative `timedelta`. The immutable
HistoricalExecutionConfiguration bridges it using exact integer microseconds:

`additional_delay_candles = execution_delay_microseconds / timeframe_microseconds`

A nonzero remainder is rejected; there is no rounding, interpolation or fractional-bar execution.
For 1h candles, `timedelta(0)` means the next candle, `timedelta(hours=1)` means one additional
candle later, and `timedelta(hours=2)` means two additional candles later. For 15m candles an
additional delay of one candle is `timedelta(minutes=15)`. Negative/non-duration values are rejected
by the existing cost model. Delays exceeding representable timestamps are controlled errors.
No conflicting second delay setting is maintained. The execution configuration fingerprint includes
the timeframe, derived integer delay, timing rule, pricing/fee conventions and full cost configuration.

### Exact adverse pricing formulas

Let P be the eligible candle open, q the caller's positive **base-asset quantity**, s the configured
full bid/ask spread fraction, l the slippage fraction and f the trading fee fraction.
The existing cost model convention is retained: each action pays half the declared full spread.
Rates are caller-declared research assumptions, never hard-coded Binance/Quidax fees.

- Per-unit spread magnitude: `a = P Ã— s Ã— 0.5`
- Per-unit slippage magnitude: `b = P Ã— l`
- ENTER_LONG or EXIT_SHORT: `fill_price = P + a + b`
- ENTER_SHORT or EXIT_LONG: `fill_price = P âˆ’ a âˆ’ b`
- Reference notional: `q Ã— P`
- Adjusted fill notional: `q Ã— fill_price`
- Spread cost: `q Ã— a`
- Slippage cost: `q Ã— b`
- Separate trading fee: `(q Ã— fill_price) Ã— f`, in quote currency

Spread and slippage are additive adjustments against the same reference P, not sequentially
compounded. Their signed per-unit adjustments and positive quote-currency costs are retained.
Fees do not alter the fill price. Spread/slippage are already embedded in price; later P&L work
must not subtract them twice. A non-positive adjusted price is rejected rather than clamped or
repaired. Adverse prices are not capped to the candle's high/low to invent favorable execution.
All arithmetic uses existing exact Decimal sums/products, independent of ambient Decimal precision.
No binary financial floats, tick rounding, quantity rounding or unspecified precision changes occur.

### Quantity and state safety

The caller must supply one positive finite Decimal quantity in base-asset units for each action
in the run. The simulator never derives a quantity from capital, equity, risk, confidence or a
strategy. It does not accept an ambiguous quote-notional value or decide a notional-to-quantity
conversion policy. Both reference and adjusted notional are explicit outputs of the given quantity.
Quantity is fixed across this foundation's actions, so matching entries/exits carry the same units.
An explicitly declared initial LONG/SHORT thesis assumes those caller-specified units already exist;
no prior entry price, value or profit is invented.

Only valid replay transitions can fill. Replay context, boundaries, counts, complete ordered rows,
frozen/prefix fingerprints, reconstructed shared StrategyInput/StrategyDecision identities,
analytical versions, record identities, thesis transitions and run identity are checked before
any result is returned. This validates existing replay artifacts without duplicating analytical or
strategy calculations. The accepted replay identity schema is the existing v1 contract.

Part 1 thesis state changes immediately after a decision; Part 2 filled state changes only at
available fill events. A single fixed delay preserves actionable-decision order, so available fills
are a prefix of those decisions. There are no duplicate entries, unmatched filled exits, simultaneous
long/short theses or automatic reversals. NO_ACTION creates no fill and incurs no cost.

When an eligible index falls beyond the explicit evaluation end, the action is represented in
`unfilled_decisions` with `END_OF_DATASET`, the original decision/record/prefix identity, decision time,
expected eligible time, replay thesis state and unchanged filled state. A pending final entry cannot
create an actual filled position; a pending final exit cannot erase one. Multiple pending actions
can refer to replay thesis states not yet realized as fills; they remain unfilled and do not bypass
transition validation. An internal missing candle is corruption and rejected, not an end-of-data case.
No candles outside the frozen snapshot are sought. Extending a dataset can make a previously pending
action eligible but cannot change a fill already available from an unchanged prefix.

### Provenance, reproducibility and limitations

Each immutable fill carries the original StrategyDecision/decision identity and replay record
identity, canonical context, strategy family/version/configuration identity, decision timestamp,
eligible UTC open timestamp, filled states before/after, action, explicit quantity, exact price/cost
breakdown and immutable execution/cost configuration (including delay, cost-model identity and
version). Fill identity uses SHA-256 canonical serialization of the prefix through the eligible
completed candle, original decision/record identities, execution configuration, timestamps,
states, quantity and price breakdown. Full dataset/replay identities belong to the simulation
envelope; earlier fills retain stable causal identities when future history is appended.

These are frozen-data retrospective assumptions, not a closed-loop execution-aware strategy replay:
Part 1 decisions remain unchanged and are not reevaluated with pending fill state. There is no
liquidity, queue, partial-fill, cancellation, market-impact, latency-distribution, funding, borrow,
margin, tick-size or quote-availability model. Initial SHORT research support implies no venue
shorting capability. Original archival retrieval metadata remains intact; it is not simulated
real-time market delivery. Configured rates have no empirical profitability or execution-quality claim.

Tests cover all four actions, NO_ACTION, distinct next-open versus same-close references, whole-bar
delay/timeframe conversion, exact last-candle eligibility, final/pending decisions, adverse costs
independently, separate fees, zero costs, exact Decimal/positive-price boundaries, repeated identity,
future append invariance, immutable inputs, corrupted data/replay provenance, illegal transitions,
and absence of provider/database access or analytical/strategy recalculation.

No portfolio sizing policy, performance metrics, realized P&L aggregation, equity curve, benchmarks,
optimization, walk-forward evaluation, Risk Engine, TradeIntent, venue orders, paper/live execution,
API, frontend, persistence, migrations or dependency additions are introduced.


## Milestone 9C Part 3 â€” deterministic accounting v1.0.0

`app.strategy_lab.accounting.account_simulation(dataset, replay, execution, *, initial_capital, as_of)`
accounts for an existing frozen Part 2 simulation. It starts in CASH and requires caller-supplied
finite nonnegative Decimal initial capital denominated in the canonical instrument's **quote asset**.
For BTC/USDT the capital and P&L are USDT units, not an implied fiat USD conversion. This capital is
independent of the user's Paper Portfolio: no database/user/account state is read or modified.
Zero initial capital is allowed for a no-fill CASH experiment; a positive-sized entry cannot be funded.
A pre-existing LONG/SHORT start is rejected because Part 2 provides no original entry basis/capital
inventory; the accountant never fabricates one.

### Input integrity and chronological ledger

The original Parts 1 and 2 remain unchanged. Accounting reuses `simulate_execution` to validate the
provided simulation against the frozen dataset, replay, explicit quantity and exact execution/cost
configuration. Its output must equal the supplied immutable artifact. This revalidates pricing and
provenance without recalculating strategies, indicators or regimes, querying a provider or changing data.

The run-local ledger applies actual fills at their eligible candle OPEN in chronological order.
It then creates one immutable equity observation at each **completed evaluation candle CLOSE**.
Warm-up candles initialize upstream analytics only: they create no equity/performance observations.
An observation uses only fills available by that close and that candle's exact stored close; no later
close, high/low-selected price, inferred price or synthetic closing fill is used. The explicit evaluation
end remains exclusive. For exposure duration an open thesis spans the full entered candle interval
through the next exit open or evaluation end, avoiding a spurious one-millisecond timestamp deduction.

### Long and short P&L and exact reconciliation

For quantity q, entry/exit reference opens R_e/R_x, adjusted simulated fill prices F_e/F_x,
and separately recorded fees:

- LONG gross movement: `(R_x âˆ’ R_e) Ã— q`
- SHORT gross movement: `(R_e âˆ’ R_x) Ã— q`
- LONG net outcome from fills: `(F_x âˆ’ F_e) Ã— q âˆ’ entry_fee âˆ’ exit_fee`
- SHORT net outcome from fills: `(F_e âˆ’ F_x) Ã— q âˆ’ entry_fee âˆ’ exit_fee`
- Both directions: `gross movement âˆ’ total spread cost âˆ’ total slippage cost âˆ’ total fees = net outcome`

Every matched trade enforces this equality **exactly**, using existing exact Decimal arithmetic,
not a floating tolerance. `metrics.RealizedTrade` supplies the existing gross-minus-costs definition.
Spread/slippage are already embedded adversely in adjusted fill prices: they are not deducted a
second time from fill-based P&L. Costs remain separately inspectable to explain the difference from
reference-price market movement. Matched direction, quantity, strategy/configuration, canonical context
and entry-before-exit chronology are enforced. The immutable completed trade retains both original
fills, timestamps, reference/adjusted prices, gross/cost/net decomposition and reproducibility identities.

### Cash, realized capital and open positions

Cash is **free spendable quote cash**, not total equity. A funded long entry subtracts adjusted entry
notional plus its fee; an exit adds adjusted exit proceeds minus its fee. Long equity is
`cash + q Ã— current completed close`.

Fees are realized expenses at the fill that incurs them, including an entry whose position remains
open. Let C be completed-trade net P&L (including all costs on those closed trades), and f_open the
entry fee of the single remaining open position, or zero:

- Ledger realized P&L: `C âˆ’ f_open`
- Realized capital: `initial_capital + C âˆ’ f_open`
- Open LONG unrealized P&L: `(mark_close âˆ’ entry_fill_price) Ã— q`
- Open SHORT unrealized P&L: `(entry_fill_price âˆ’ mark_close) Ã— q`
- Total equity: `realized_capital + unrealized_pnl`

Open spread/slippage remain embedded in the entry fill basis and therefore in unrealized P&L.
They are not prematurely realized. Equity observations separately expose completed-trade net P&L,
gross realized P&L, fee recognition, incurred cost totals, free cash, realized capital, unrealized
P&L, equity, state, quantity and exact causal mark provenance. The fee ledger must equal closed-trade
fees plus any open-entry fee. Cash/asset and realized/unrealized equity representations must reconcile
exactly on every observation.

At the dataset end, an open position retains its original entry fill/basis, quantity, direction,
last mark and unrealized result. There is **no liquidation**, estimated exit fee or invented exit.
An unfilled entry/exit remains the original Part 2 unfilled decision and incurs no fill cost or
additional accounting state change. Completed-trade statistics never include an open position.

### Explicit unleveraged capital and simplified short collateral

The caller's quantity is never resized. Every long entry must fit free cash including its actual
fee; loss-reduced capital must also fund later entries. No deposits, withdrawals, lending, interest,
implicit credit, financing rate or automatic margin transaction is invented.

For research SHORT only, v1 reserves **100% of the entry reference notional from the caller's own
capital**, pays the entry fee from free cash, and segregates adjusted sale proceeds. Short proceeds
cannot fund another action and are never reported as spendable cash. While open:

`short_equity = free_cash + reserved_own_capital + restricted_sale_proceeds âˆ’ q Ã— mark_close`

The ledger enforces `q Ã— price <= total_equity` at entry's reference open and every completed close
while SHORT, imposing a conservative one-times-equity exposure ceiling. Insufficient entry funding,
negative settlement cash, or an open short exceeding that ceiling rejects the experiment. At an exit,
reserved capital and restricted proceeds are released only to settle actual cover notional and fee;
any remainder becomes free cash. A gap that makes settlement require borrowing is rejected. No automatic
resizing, liquidation, margin call, collateral top-up or forced trade is introduced to rescue a run.

This is an explicit restrictive research convention, **not a venue-realistic margin or borrow model**.
Short exposure inherently represents a hypothetical borrowed-asset position; no actual borrowing or
venue support exists here. No claim is made that Quidax spot supports shorts. The model checks entry
and completed marks/settlement, not hypothetical intrabar margin/solvency conditions between those points.

### Reusing 9A metrics without changing definitions

`performance.py` is an adapter to the unchanged existing `metrics.py`, not a second formula library.
Completed outcomes project to `metrics.RealizedTrade` for:

- Gross/net returns: completed gross/net P&L divided by initial capital.
- Trade count, win/loss rates, profit factor, average win/loss and expectancy: **completed trades only**.
  Wins/losses use net outcomes; breakevens remain in the denominator for win/loss rates and expectancy.
  Average loss is the positive loss magnitude. No-loss profit factor stays None, including all-win runs.
- Total return: final **marked equity** versus explicit initial capital, including open P&L and paid fees.
- Maximum drawdown: the primitive receives explicit initial capital followed by evaluation equity values,
  so the first paid cost is not hidden. This baseline is the supplied starting capital, not a warm-up price.
- Exposure: exact filled-position duration as a percentage of declared evaluation duration.
- Turnover: all unsigned **adjusted fill notionals**, each entry/exit leg included, divided by initial capital.

All-fill fee/spread/slippage totals include costs on an open entry. Cost-only projections with zero
gross movement reuse the existing cost aggregation primitives; those projections are **never** passed
to trade count, win/loss, expectancy or return statistics. Both completed-trade totals and incurred
all-fill costs are retained separately. Gross/net returns can therefore differ from total equity
return while a position remains open. Undefined values remain None under the established 9A rules;
zero capital does not produce fabricated returns, turnover or drawdown. Exposure is percentage units;
returns, turnover and drawdown are fractions. No metric is annualized or interpreted as profitability evidence.

### Immutable composed result and deterministic identity

`ResearchResult` composes canonical context/full dataset identity, explicit initial capital, versioned
accounting configuration, the original replay (including strategy and analytical configurations and
all records), original execution (including cost model, fills and unfilled decisions), immutable completed
trades, optional open position, chronological equity observations, completed-trade totals, incurred costs
and the shared performance results. No wall-clock inspection timestamp enters identity.

Each trade identity binds its two causal fill identities, accounting version/configuration and exact
P&L/cost decomposition. Each equity observation binds its frozen prefix through that candle, accounting,
strategy and execution configurations, initial capital, exact ledger/mark values, causal fill identities
and the previous observation identity. Appending future history cannot rewrite earlier equity or trade
records. Full-run result identity additionally binds full dataset/replay/execution identities, initial
capital, accounting configuration, ordered trade/equity identities and performance summary, using the
existing canonical serialization and SHA-256 namespaces. Decimal representation scale and dictionary
ordering retain the established identity semantics; the explicit archival validation clock is informational.

### Coverage and limitations

Deterministic hand-checkable tests cover profitable/losing long and short trades, breakevens, sequential
trades, fees/spread/slippage independently and together, zero costs, high-precision cost reconciliation,
open positions, pending final decisions, cash-only/zero-capital runs, marked drawdown, denominator and
profit-factor edge cases, actual exposure/turnover, entry/cover funding, exact capital boundaries,
short collateral limits, immutable/provenance integrity, repeated execution, future append invariance
and delegation to every existing metric primitive. No external network/database is needed by these tests.

This is research accounting over already-frozen replay decisions, not a closed-loop strategy reevaluation
with pending fills. Historical execution simplifications from Part 2 remain: no partial fills, liquidity,
borrow availability, market impact, venue margin, funding, taxes, FX conversion or intrabar risk simulation.
Per-prefix validation/replay remains unsuitable for unbounded large-history optimization. No parameter
optimization, walk-forward scoring, strategy promotion, benchmark comparison, Risk Engine, TradeIntent,
real/paper/venue orders, exchange accounts, API, frontend, persistence, migration or dependency is added.


## Final integrated Milestone 9C audit

`tests/test_integrated_9c_audit.py` exercises the complete frozen-dataset â†’ causal Intelligence â†’
Regime Engine â†’ **unchanged default ADAPTIVE_TREND v1.0.0** â†’ StrategyDecision â†’ replay thesis â†’
next-open simulated fills/costs â†’ accounting/equity â†’ shared 9A metrics pipeline. No scripted
strategy decisions, fabricated analytical rows or adjusted strategy/regime thresholds are used.
Synthetic prices use repeating +1/+1/âˆ’1 drift/pullbacks and their downward mirror, then explicit
trend reversals. The frozen datasets declare 128 warm-up candles, with evaluation starting before
the first strategy entry. All prices, history boundaries and cost assumptions are synthetic test inputs.

Coverage includes actual profitable/losing long and short trades, sequential directional trades,
flat no-trade history, final open LONG/SHORT, final unfilled entry, delayed unfilled exit, a gapped
next OPEN distinct from the decision CLOSE, and a small gross winner made net-negative by costs.
The default strategy decides whether/when to act; tests never force a production decision branch.
These patterns are adversarial test fixtures, not evidence that the strategy is profitable.

### Independent accounting oracle

Selected expectations are literal hand-checkable values derived with independent rational cash
arithmetic, without importing production accounting/pricing/numeric helpers. Quantity is one base
unit; initial capital is 10000 quote units. Research assumptions for the ordinary oracle cases are
fee 0.001, full spread 0.002 (half per leg), and slippage 0.001. They do not claim venue-specific fees.

| Synthetic case | Entry/exit reference | Entry/exit adverse fills | Fees | Spread | Slippage | Gross P&L | Net P&L / ending realized capital |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Long winner | 177 / 194 | 177.354 / 193.612 | 0.370966 | 0.371 | 0.371 | 17 | 15.887034 / 10015.887034 |
| Long loser | 177 / 174 | 177.354 / 173.652 | 0.351006 | 0.351 | 0.351 | -3 | -4.053006 / 9995.946994 |
| Short winner | 223 / 206 | 222.554 / 206.412 | 0.428966 | 0.429 | 0.429 | 17 | 15.713034 / 10015.713034 |
| Short loser | 223 / 226 | 222.554 / 226.452 | 0.449006 | 0.449 | 0.449 | -3 | -4.347006 / 9995.652994 |
| Small winner, fee 0.01 | 177 / 179 | 177.354 / 178.642 | 3.559960 | 0.356 | 0.356 | 2 | -2.271960 / 9997.728040 |

Closed scenarios end in CASH with zero unrealized P&L and equity equal to ending realized capital.
The two-trade scenario has gross P&L 35 and final cash/equity 10032.801070 after explicit costs.
Independent open-position expectations also assert cash, fee timing, collateral, restricted proceeds,
realized capital, mark and unrealized P&L: open LONG equity is 10000.468646 (realized capital
9999.822646 + unrealized 0.646); open SHORT equity is 10000.331446 (9999.777446 + 0.554).
The entry-open-gap test marks a 180.360 fill against a completed close of 178, producing equity
9997.459640 rather than a misleading same-close fill. No final liquidation is invented.

For every completed audit trade, exact independent `Fraction` arithmetic confirms both reference
gross minus explicit spread/slippage/fees and adjusted-fill cash P&L minus fees equal the same net
result. No tolerance is used for monetary reconciliation. Only non-terminating percentage/drawdown
divisions use explicit tight tolerances justified by the existing Decimal-50 division convention.

### Causality, states, capital and identity guarantees

A longer synthetic dataset shares an exact earlier prefix with a shorter completed-trade dataset.
The audit compares Intelligence rows, regime results, StrategyDecisions, available fills, completed
trades and equity observations directly: all earlier objects/identities remain equal. Full-run
identities correctly change with the extended frozen boundary. A pending final entry can become a
new available fill after extension without rewriting any previously available observation.
Instrumentation passes through real calculations and the shared strategy contract while checking
that each analytic call receives exactly the prefix through its evaluation candle. Network/HTTP
and database entry points are forbidden in a fresh composed pipeline test.

State consistency is checked on each layer's own event clock. Replay thesis changes at the completed
decision candle; executed/accounted state changes at the eligible future open. Equating those clocks
would be an incorrect audit. A delayed final exit can leave replay thesis CASH while actual filled
and accounted state remain LONG; the final unrealized loss remains visible and no exit is fabricated.
Entries/exits alternate legally, quantities remain caller-supplied, completed trades require actual
matching fills, and equity/cash/short collateral reconcile independently at each evaluated close.

Unaffordable actual Adaptive Trend entries fail without resizing. Nominally fundable short entries
also fail if adverse costs breach the explicit one-times-equity short exposure limit. Short proceeds
remain restricted, not spendable cash. Independent rational calculations verify running-peak drawdown,
actual exposure intervals, adjusted-notional turnover and warm-up exclusion. The audit preserves the
existing distinction between closed-trade return and marked total equity return.

Two fresh complete replays with equivalent Decimal representations and different ambient precision
produce identical decisions, fills, trades, curve, metrics and final result identity. Meaningful
fee/delay/quantity/capital changes alter the appropriate execution/accounting identities while leaving
canonical strategy decisions unchanged. No strategy threshold is changed to obtain these outcomes.

No financial defect requiring production changes was identified by this audit. Parts 1â€“3 and the
strategy/analytical/metric implementations remain byte-for-byte unchanged; only audit tests and this
evidence documentation are added. Existing limitations remain: retrospective archival provenance,
fixed thesis replay rather than pending-fill-aware strategy reevaluation, simplified next-open and
short collateral assumptions, completed-close valuation without intrabar solvency/liquidity, and no
empirical profitability or venue execution claim. This audit adds no product functionality.


### Database-preservation verification caveat

The final audit regression suites passed (1077 backend tests, including 42 integrated audit tests,
and 112 frontend tests), as did pip check and the production build. Alembic remained
`0006_market_candles (head)`, with no dependency, migration or frontend/production source changes.

The **live database preservation check failed**: the pre-audit snapshot contained 503 candles,
whereas the final read-only check found 526. A subsequent read-only diagnostic confirmed that all
original 503 full rows exactly match their pre-audit fingerprint and every other application table
also matches. The difference is 23 additional BTC/USDT / BINANCE / 1h rows, created in one batch at
2026-10-17 10:40:37 UTC, after the baseline snapshot. The source of that insertion was not established.
No rows were removed or changed by the audit to force the expected count; the baseline was not reset.
Therefore the requested exact-503/full-database-unchanged condition cannot be certified as passed.
The synthetic research pipeline has no ingestion path and its composed network/database-forbidden
test passes, but that does not establish which separate process inserted the live batch.


### Automated database safety after the 9C audit

The investigation found a legitimate 23-candle refresh through the normal ingestion path; its exact caller was not established. The accepted stored-candle baseline is now 526, preserving the original 503 rows.

Backend pytest installs a fail-closed connection guard before test-module collection. It replaces inherited database settings with a non-application sentinel and blocks physical SQLAlchemy and psycopg connections, including explicit URLs. Existing explicitly injected mocked sessions/dependency overrides remain supported. Python subprocess tests inherit a test-only bootstrap that blocks physical network connections without importing application/database modules. Real database integration tests are not enabled: a test database name or local host alone is insufficient proof of isolation. A future real integration harness would need separately verified disposable database isolation.

This guard applies only to pytest and its ordinary inherited Python subprocesses; production runtime, operational helpers and manual verification commands are unchanged. It is an accidental-access safeguard, not a sandbox against code deliberately removing the guard or launching Python with environment/site initialization disabled. Read-only operational fingerprint verification remains separate from pytest.


## Milestone 9D Part 1 â€” paired out-of-sample validation

`app.strategy_lab.out_of_sample` composes the existing immutable 9A dataset/windows and unchanged 9C replay, historical execution and accounting engines. Version 1.0.0 is research validation only: no training, optimization, parameter selection, scoring verdict or strategy promotion.

### Explicit plan and period boundaries

`OutOfSamplePlan` freezes the complete dataset, strategy definition/configuration, historical execution/cost configuration, supported accounting configuration, caller-supplied positive Decimal quantity and initial capital. The declared starting state is CASH. A shared 9A `ValidationSplit` identifies IS and OOS, and the paired `ValidationPlan` validates candle boundaries. V1 requires an exact contiguous partition: warm-up starts at the dataset's first candle and ends at IS start; IS ends at OOS start; OOS ends at the dataset's exclusive end. Intervals are inclusive-start/exclusive-end, so the first OOS observation occurs strictly after the final IS observation even though adjacent windows share an exclusive boundary. Gaps, overlaps, reversed windows, omitted/trailing history, insufficient warm-up and forged dataset fingerprints are rejected. V1 intentionally does not support separated windows with unused intervening periods.

### Independent state and causal execution

IS runs on a snapshot truncated at IS end. OOS runs on a snapshot from the same explicit warm-up start through OOS end, with evaluation beginning only at OOS start. The plan explicitly declares this larger OOS analytical-context window: warm-up and IS initialize indicators/regimes, but generate no OOS strategy decisions, fills, equity observations or metrics. No older history is fetched. Both periods use the same frozen strategy/configuration and a fresh implementation supplied by a factory. Both instances are constructed and checked against the plan before either run; the factory receives no data/results. The strategy contract still requires deterministic pure implementations.

Each period starts independently in CASH with the full declared initial capital, not the previous period's ending equity. IS positions and pending decisions never carry into OOS. Open IS positions are retained in the IS result and marked using existing accounting rules, without fabricated liquidation. An IS final-candle decision remains unfilled even if its eligible timestamp exists in OOS. Open OOS positions likewise remain explicit. The existing unleveraged capital checks, short research-collateral convention, costs, next-open timing and delay are unchanged. Only accounting conventions already supported by the unchanged v1 engine are accepted; an alternative configuration is rejected rather than silently ignored.

### Results, comparison and reproducibility

`OutOfSampleResult` retains the complete immutable plan, separate IS/OOS `ResearchResult` artifacts, a tuple of descriptive `MetricComparison` rows and a deterministic validation identity. Metrics are never pooled. Comparisons report OOS minus IS for total/net return, maximum drawdown, trade count, win rate, profit factor, expectancy, exposure, turnover and each fee/spread/slippage total. Undefined inputs produce an undefined (`None`) delta; no missing value becomes zero. Financial differences use existing exact Decimal arithmetic. Changes are descriptive, not evidence of profitability or a PASS/FAIL decision.

The plan identity uses the existing typed canonical serialization plus namespaced SHA-256 over the frozen dataset identity, windows, explicitly declared OOS context, state convention, strategy/configuration identity, execution/cost/accounting assumptions, quantity/capital and Intelligence/Regime configurations. The validation identity binds this plan identity to both complete backtest identities and the comparison. No wall-clock inspection timestamp participates. Dictionary ordering and equivalent Decimal representations cannot change identity. Full-run identity changes when future data changes, while earlier causal replay/fill/equity artifacts retain their prefix identities.

### Guarantees and limitations

Tests exercise unchanged Adaptive Trend and test-only causal contract fixtures using real Intelligence/Regime/replay/execution/accounting. They challenge OOS-to-IS leakage, later/appended OOS data, boundary positions/unfilled decisions, explicit warm-up exclusion, separate performance, fixed configurations, deterministic identities, costs, unaffordable quantity and invalid windows. This foundation does not establish statistical generalization, account for selection bias, compare benchmarks, optimize parameters, perform walk-forward evaluation or promote a strategy. A caller must provide a genuinely deterministic factory; arbitrary user code is not sandboxed. Computation retains the existing transparent prefix replay cost. There is no API, persistence, scheduling or provider access.


## Milestone 9D Part 2 â€” walk-forward evidence and parameter sensitivity

### Disjoint independent folds

`app.strategy_lab.walk_forward` version 1.0.0 binds a frozen parent dataset to an explicit chronological tuple of Part 1 `OutOfSamplePlan` folds. Every fold therefore declares its exact analytical warm-up, historical/IS context, OOS window, strategy/configuration, execution/cost/accounting assumptions, quantity, capital and CASH start. Fold candles must exactly match their parent slice. V1 conservatively forbids overlap of entire fold histories, including warm-up, not just OOS; expanding/reused training windows are not supported. Folds are never sorted to repair an invalid plan. A single fold is supported through the existing paired 9A definition; multiple folds reuse 9A's strict walk-forward definition. Unselected parent intervals, if any, are explicit unused data and never added to a fold's history.

The unchanged Part 1 engine evaluates each fold independently, and all folds must use the same strategy definition and research assumptions. Each IS/OOS period receives a fresh strategy instance; no position, pending decision, capital or mutable strategy instance carries across folds. Each result retains its own Part 1 plan, IS/OOS backtests, dataset/window/configuration identities, metrics, costs, completed trades and open positions. No fold equity curves are stitched into a continuous investment portfolio.

### Descriptive OOS summary

`WalkForwardSummary` uses only OOS performance from each fold: fold count; positive/negative/zero/undefined completed net-return counts; equal-fold mean/median net return; minimum/maximum net return; median/largest drawdown; total completed trades; zero-trade fold count; mean expectancy and profit factor over defined folds; and separately summed incurred fees, spread and slippage costs. Defined-observation counts accompany nullable statistics. If no values exist the statistic is `None`, never zero. Even-sized medians average their two central values. Sums, differences and division use existing deterministic Decimal helpers (50-digit division convention).

`return_sign_consistency` describes only defined net-return signs: POSITIVE, NEGATIVE, ZERO, MIXED, or `None` if none are defined. Undefined-return fold counts remain explicit. Fold sign uses existing completed-trade net return, not unrealized mark-to-market gain; open positions remain visible separately. These are descriptive equal-fold statistics, not compounded/time-weighted returns, significance tests, risk-adjusted rankings or a validation verdict. Unequal fold durations are not silently normalized.

### Caller-declared Adaptive Trend sensitivity

`app.strategy_lab.sensitivity` version 1.0.0 accepts a paired OOS or walk-forward plan, an explicit typed Adaptive Trend baseline, and a finite caller-supplied sequence of configurations that must include that baseline exactly once. The baseline must match the strategy/configuration already frozen in the validation plan. Duplicate/equivalent configurations are rejected, including numerically equivalent Decimal representations. Configurations are canonically ordered by configuration identity, never by returns. The baseline is evaluated once and retained as the baseline regardless of outcomes; every supplied distinct alternative is retained.

OFAT defaults to enabled and requires each alternative to change exactly one meaningful configuration field. Comparison uses numerical Decimal equality, so trailing-zero representation changes do not count. Explicit non-OFAT mode permits caller-declared multiple-field alternatives; it does not generate combinations. Nearby variants and their scientific justification are the caller's responsibility; this infrastructure does not prescribe profitable perturbations or enforce a universal distance between heterogeneous parameters.

Each alternative uses an immutable replacement of only the strategy definition, with the exact same datasets, fold/window boundaries, analytical context, starting state, execution/cost/accounting assumptions, quantity and initial capital. Existing Adaptive Trend, analytics, replay, execution, accounting and comparison functions remain unchanged. Results retain the exact immutable configuration, configuration identity, changed-field names, full validation/backtest identities, OOS metrics and per-fold metric deltas from baseline. Comparison rows expose `baseline`, `variant` and `variant_minus_baseline`, wrapping existing comparison arithmetic without repurposing IS/OOS field labels. Return-sign change is explicit (`None` when undefined); deltas include net return, drawdown, trade count, expectancy, exposure, turnover and each cost total. No configuration is selected, ranked, promoted or written back to production defaults.

### Identities, causal guarantees and limitations

Walk-forward plan identity binds the parent dataset and ordered fold plan identities; a fold identity binds only its own index/plan/result, preserving earlier fold results when future data changes. Walk-forward result identity additionally binds descriptive summary evidence. Sensitivity plan identity binds the original validation plan, explicit baseline, canonical configuration identities and OFAT mode; result identity binds all validation results and differences. Existing typed canonical serialization and namespaced SHA-256 are reused; inspection timestamps and input configuration ordering do not determine identity.

Tests cover independent open/unfilled states, losing and zero-trade folds, literal summary arithmetic and nonfinite/undefined conventions inherited from shared arithmetic, future-fold isolation, repeated execution, equivalent decimals, explicit perturbations, OFAT rejection, default file immutability and absence of automatic selection. This remains frozen historical research, without optimization, walk-forward training/refitting, automatic fold creation, benchmark scoring, cost stress testing, final verdicts, API, persistence or execution authority. The deterministic strategy contract and non-sandboxed factory limitation from Part 1 still apply.


## 9D Part 3 — Passive benchmarks and execution stress evidence

`benchmark_evaluation` executes the existing 9A CASH and BUY_AND_HOLD definitions against completed OOS validation results (paired or independent walk-forward folds). It binds the original frozen dataset/context, exact OOS window, quote currency and initial capital; mismatched research data, window, capital, strategy, accounting or execution assumptions are rejected. It uses the shared 9C adverse pricing helper and unchanged 9A return/drawdown/exposure/turnover primitives. There is no benchmark strategy, order, forced liquidation, optimizer or performance verdict.

CASH retains initial capital at every OOS close: zero costs, exposure, turnover, return and drawdown. BUY_AND_HOLD is a passive allocation declared before the evaluation window. Its reference is the first OOS candle OPEN, plus the explicitly declared whole-candle delay (zero means that first open, unlike a strategy decision which becomes eligible only at the next open). It uses no first-candle close information to choose the allocation. All candles must be completed at the archival evaluation cutoff. Insufficient history for the declared entry delay is an explicit error.

The passive entry applies adverse half of the full spread plus slippage to the reference open, and fees separately on adjusted notional, using the existing pricing function. Quantity = initial capital / (adjusted unit price + unit entry fee), with an explicit 50-significant-digit Decimal ROUND_DOWN allocation convention. Residual cash is preserved; rounding cannot borrow capital. No caller strategy quantity is resized: this benchmark has a separately documented fee-inclusive full-budget allocation, whereas Adaptive Trend retains caller-supplied fixed quantity. This exposure difference is retained, not hidden. Entry costs occur once; equity is residual cash + quantity × each causally available completed OOS close. The last close marks the still-open passive holding; no exit price, exit costs or realized trade is fabricated. Exposure is elapsed time from entry open through window end, divided by the evaluation duration. Turnover counts entry adjusted notional only. Drawdown includes initial capital and completed-close marks; it is not intrabar drawdown.

Immutable benchmark results retain context/window identities, execution assumptions, entry reference/adjusted prices, quantity, residual cash, costs, timestamped close/equity observations and version 1.0.0. Comparisons expose strategy-minus-benchmark total return, maximum drawdown, exposure, turnover and total costs. Strategy turnover and realized-trade returns are not equated to passive full-period exposure: comparisons use total equity return, including open-position marks. Folds stay separate.

`stress_testing` accepts immutable caller-supplied scenarios varying only the existing fee/full-spread/slippage/duration cost model. An explicit uniquely named BASELINE must equal the frozen validation costs. Whole-candle delays are validated before any execution. Scenario codes, cost identities and the original plan bind deterministic identities; results are ordered by code, never profitability. Strategy, data, windows, quantity, initial capital and accounting remain fixed. The unchanged OOS/walk-forward → replay → execution → accounting pipeline runs every scenario with fresh unchanged Adaptive Trend instances. No production default is replaced.

Per-scenario results retain the complete validation artifacts (trades, fills, unfilled decisions, open positions, equity), OOS metrics per fold, exact assumptions, and descriptive differences from BASELINE: net return, drawdown, trade count, expectancy and other existing comparison metrics, total costs and net-return sign changes. Undefined metrics remain None. No scenarios are selected, ranked or promoted. Insufficient capital or any other invalid execution fails explicitly with the existing pipeline error; the caller must correct/redeclare its experiment. There is no automatic resizing, partial success disguised as a valid result or negative-cash workaround.

Tests use synthetic frozen candles and independent literal accounting oracles, including higher fees, wider spread, higher slippage, one additional candle delay and combined adverse assumptions. These are research inputs, not Binance/Quidax fee claims. Costs reconcile both through adverse fills minus fees and gross movement minus explicit costs. Existing causal next-open execution, final unfilled decisions, independent fold boundaries and open-position treatment are unchanged. Sensitivity, OOS validation, walk-forward validation and Adaptive Trend defaults remain unchanged.

Limitations: no benchmark scoring/final verdict, exit liquidation, intrabar liquidity/impact, venue minimum lot constraints, optimization, automatic promotion or real execution. The fee-inclusive passive allocation has explicit finite Decimal precision and may retain tiny cash dust. Extremely adverse scenarios can be unaffordable and intentionally abort. Close-mark drawdowns and deterministic cost assumptions are research evidence, not venue execution guarantees.


## 9D Part 4 — Validation evidence and research verdict

`validation_evidence` consumes existing immutable IS/OOS, walk-forward, sensitivity, benchmark and stress artifacts. It does not execute a strategy, rerun analytics/replay/accounting, call a provider, fetch history or write data. Frozen candle snapshots are revalidated for provenance, not recalculated into new research results. Source bindings reject foreign datasets, windows, canonical contexts, quantities, capital, strategy definitions, cost/accounting assumptions, duplicate evidence and inconsistent metric projections. Sensitivity may change only its declared strategy configuration; stress may change only declared execution assumptions. Baselines must anchor to the exact supplied IS/OOS or walk-forward validation.

The only verdicts are INSUFFICIENT_EVIDENCE, FAILED_VALIDATION and VALIDATION_CANDIDATE. Evaluation precedence is exact: any mandatory coverage/undefined-required-measurement insufficiency yields INSUFFICIENT_EVIDENCE; otherwise any mandatory quality criterion FAIL yields FAILED_VALIDATION; otherwise VALIDATION_CANDIDATE. A candidate means only: "The available historical research evidence is sufficient to justify consideration for the next controlled validation stage." It does not authorize another lifecycle stage or execution. Observed failures remain visible even when insufficiency takes precedence. Strong return cannot cancel a failed or insufficient criterion. There is no combined numeric score.

Every criterion records code, category, observed value, declared requirement, status, deterministic reason and readable explanation. Status is SUFFICIENT/INSUFFICIENT for coverage, PASS/FAIL for mandatory quality and NOT_APPLICABLE for informational/non-required criteria. Undefined mandatory metrics are insufficient, never fabricated zero. Categories remain separate: EVIDENCE_COVERAGE, RETURN_QUALITY, DRAWDOWN_CONTROL, WALK_FORWARD_CONSISTENCY, PARAMETER_STABILITY, COST_RESILIENCE and BENCHMARK_CONTEXT. Reports retain all criteria; `passed_criteria` contains quality PASS codes (coverage remains explicitly SUFFICIENT).

### Default development research-policy hypotheses (version 1.0.0)

Every value below is a centralized immutable hypothesis requiring empirical review, not tuned to the accepted stored dataset and not a claim about future results:

| Policy field | Default |
| --- | --- |
| minimum_oos_completed_trades | 30 |
| minimum_walk_forward_folds / minimum_active_folds | 3 / 3 |
| minimum_non_losing_fold_proportion | 0.67 of ACTIVE folds |
| maximum_oos_drawdown / maximum_worst_fold_drawdown | 0.20 / 0.25 |
| minimum_oos_expectancy | 0 quote-currency units per completed trade |
| minimum_oos_profit_factor | 1.10 |
| minimum_stress_scenarios | 4, including BASELINE and explicitly recorded failed attempts |
| maximum_stress_return_degradation | 0.03 absolute return fraction |
| allow_stress_return_sign_flip | false |
| minimum_stress_completed_trades / minimum_stress_trade_retention | 10 / 0.50 |
| require_sensitivity_stability / minimum_sensitivity_variants | true / 2, excluding baseline |
| maximum_sensitivity_return_degradation | 0.02 absolute return fraction |
| allow_sensitivity_return_sign_flip | false |
| minimum_variant_completed_trades / minimum_variant_trade_retention | 10 / 0.50 |
| require_cash_comparison / require_buy_and_hold_comparison | true / true |
| cash_treatment / buy_and_hold_treatment | INFORMATIONAL / INFORMATIONAL |
| minimum_benchmark_excess_return | 0, only gates when treatment is MANDATORY |
| benchmark_scope / fold_consistency_basis | PRIMARY_OOS / ACTIVE_FOLDS |

Minimum/maximum comparisons are inclusive. Return degradation is max(0, baseline net return minus alternative net return), not a percentage of baseline. Return-sign change means any transition among negative/zero/positive; an explicit boolean policy can permit it. Trade retention is alternative completed trades / baseline completed trades. Proportions and drawdowns are fractional (not percentages). Expectancy retains the existing absolute quote-currency convention and therefore depends on declared research quantity; policies must be reviewed for those units. Default 0.67 is exactly 67%, not a rounded claim that two of three folds suffice.

Walk-forward evidence is per independent OOS fold. Non-losing consistency counts active folds (completed trades > 0), preventing inactive zero-return folds from inflating consistency; minimum active/fold counts and metric coverage are separate checks. Worst-fold drawdown uses every provided fold, not a mean. Missing declared folds remain insufficient. No equity curves are stitched or new performance metrics calculated.

Sensitivity checks every provided variant/fold against its original baseline. Required variant count and per-variant trade count are coverage criteria; loss of activity below the declared retention ratio, return-sign changes and excessive degradation are quality failures. No favorable variant can average away an adverse one. Declared but missing variants are insufficient. Setting require_sensitivity_stability=false makes these checks explicitly informational, while preserving source identities and observed per-variant criteria.

Cost resilience likewise evaluates all declared scenarios/folds against BASELINE: sufficient scenario count, complete declared coverage, completed baseline evidence, per-scenario trade coverage, retained activity, sign stability and maximum degradation. Part 3 still raises on unaffordable execution. Part 4's immutable `StressEvidence` adapter accepts an existing StressResult or a caller-recorded set of completed ScenarioResults and explicit `StressFailure(UNAFFORDABLE_EXECUTION)` records with failure-source identities. It never catches/retries execution or invents missing metrics. Failed attempts count as attempted scenario coverage and produce FAIL for cost resilience. Missing attempts/results remain insufficient. Failure records must name a declared non-baseline scenario; contradictory/duplicate or failed-baseline evidence is rejected. A missing baseline result is insufficient (the unchanged supplied validation still anchors comparisons). Recording failures requires a trustworthy caller/audit artifact; this layer does not authenticate exception logs.

CASH and BUY_AND_HOLD are required context by default only for the primary OOS window; optional supplied fold comparisons remain retained and bound to their exact evaluations. Underperformance is informational unless that benchmark's policy treatment explicitly becomes MANDATORY. Mandatory treatment requires strategy-minus-benchmark TOTAL equity return >= minimum_benchmark_excess_return; other return/drawdown/exposure/turnover/cost comparison fields remain context, not hidden targets. Missing required comparisons or required undefined return differences are insufficient. No assumption that Adaptive Trend must beat passive exposure is embedded in logic.

Reports retain strategy family/version/configuration identity, dataset identity, complete declared policy/version/identity, source identities (including IS and OOS backtests, folds, alternative validations, scenario results/failures and benchmarks), canonical evidence identity, verdict, criteria, insufficiency/failure reasons, passed quality criteria and benchmark context. Namespaced SHA-256 identities use existing canonical serialization: Decimal representation and evidence presentation order cannot affect identity; meaningful evidence or policy changes do. No inspection timestamps enter identity. Per-fold/variant/scenario comparisons are deterministic and unranked.

Governance boundary: this report cannot mutate the strategy lifecycle and contains no selection, promotion, execution authority or permission for PAPER/SHADOW/LIVE. A future explicit governance action may consider it separately. No policy is optimized automatically and no Adaptive Trend default changes. Unit tests use explicitly synthetic metric observations to exercise verdict branches, separately from integration checks consuming existing engine-produced research artifacts with engine re-entry blocked.

Limitations: trusted in-process immutable artifacts and caller-recorded failure provenance, not a signed external evidence interchange; finite historical sample/undefined metrics can legitimately leave a verdict insufficient. The defaults are illustrative development hypotheses, not calibrated policy. The layer does not correct source research, resolve venue liquidity limitations, infer missing evidence, assess multiple-testing bias or authorize execution.
