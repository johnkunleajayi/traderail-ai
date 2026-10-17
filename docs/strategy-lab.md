# Strategy Lab foundation — complete Milestone 9A

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
