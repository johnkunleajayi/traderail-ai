# Risk Engine Architecture v1.0 — Milestone 10A

A strategy may propose a trade. Only the Risk Engine may authorize capital exposure. Execution is outside this boundary.

The intended flow is `Strategy → TradeIntent → Risk Evaluation → APPROVED / REJECTED → future Execution`. Milestone 10A defines immutable, reproducible Python domain contracts under `backend/app/risk`; it does **not** implement a risk evaluator, connect the contracts to strategy/runtime routes, or authorize any actual capital exposure. Constructing an approval record in a domain test has no operational effect.

## Contracts

- `TradeIntent` is a proposal, not an order. It binds a `PortfolioScope` (user UUID, portfolio UUID, environment, valuation currency), the existing venue-neutral `DatasetContext`, LONG/SHORT direction, the existing immutable `StrategyDefinition`, exact strategy input/decision identities, completed analytical decision time, reference price, invalidation boundary, descriptive entry evidence, immutable market context and explicit creation/expiry times. There is no quantity, sizing, leverage, exchange, order, credential or execution field. For LONG the invalidation boundary is below the reference; for SHORT it is above. This is analytical geometry, not a guaranteed stop/fill price.
- `MarketRiskContext` binds canonical instrument/provider/timeframe, completed data boundary, observation/expiry times, data/Intelligence/Regime identities and the existing `Regime` enum. The caller supplies verified provenance; 10A does not compute indicators, regimes, data health or history fingerprints. The intent's analytical timestamp must equal this context's data boundary. Observation may occur after candle completion; creation must follow observation. Evaluation may use a newer context for the same canonical dataset context, with its own identity.
- `RiskDecision` references the exact immutable intent, risk configuration, portfolio snapshot and evaluated market context. It records caller-supplied evaluation time, exactly APPROVED or REJECTED, typed findings and bounded approval expiry. Convenience properties expose all bound identities and typed reason codes. A rejected decision can preserve both passing and failing gate evidence; it must contain at least one rejection reason. An approved record cannot contain a rejection reason. These are structural checks, **not** proof that all future gates were evaluated.
- `PortfolioRiskSnapshot` binds explicit ownership/environment/currency, observation/expiry, caller-supplied valuation identity, equity, available cash, risk state and optional positions/risk/exposure/concentration/drawdown evidence. `None` means unknown; `0` means measured zero; `open_positions=None` differs from the known-empty tuple. Positions include observed base quantity and optional mark, exposure and open risk. Position UUIDs must be unique. No aggregates, FX conversion, cash/equity reconciliation or portfolio calculations are performed. Currency-valued snapshot amounts must already be in the scope's base currency; individual marks are in the canonical instrument's quote currency. A USD portfolio and USDT instrument do not imply USD=USDT: valuation provenance is required from a future trusted snapshot builder.

The snapshot is an observation, not enforcement: it can describe exposure above available equity so a future evaluator can reject it. It does not enable borrowing. Fractions used for configuration and drawdown are between zero and one; concentration fractions use the same representation. Negative/non-finite monetary values and non-positive position quantities/marks are invalid. Portfolio/accounting insolvency handling and validation of aggregate consistency remain future work.

## Operating limits and platform hard limits

`OperatingLimits` and `PlatformHardLimits` are distinct frozen types. All values are explicit caller inputs; there are no live numeric defaults or risk-profile presets. Fields are maximum trade-risk, open-risk, gross-exposure, directional-exposure, instrument-concentration, strategy-concentration, correlated-exposure and drawdown fractions, plus position-count cap and maximum approval age. Fraction ceilings cannot exceed one; zero permits reducing a cap to zero. Position caps are nonnegative integers and maximum approval age must be a positive duration.

`RiskConfiguration` binds an explicit version, environment and profile to both limit types. Every operating ceiling must be less than or equal to its platform hard ceiling; violations are rejected, never clamped or silently overridden. Profiles are exactly CONSERVATIVE, BALANCED and GROWTH and have no implied numbers. Test values are synthetic configuration hypotheses, **not validated live limits**. Ownership/authentication of platform configuration is a later integration responsibility; accepting caller-supplied immutable values in a pure domain constructor is not a user-facing configuration API.

## Vocabulary and future gates

Risk states are exactly NORMAL, CAUTION, DEFENSIVE and SUSPENDED. No transition rules are implemented. Kill-switch scopes are exactly USER, PORTFOLIO, STRATEGY, MARKET, DATA and SYSTEM. No operational switches or persistence are implemented.

`RiskCategory` defines DATA_HEALTH, STRATEGY_ELIGIBILITY, MARKET_REGIME, POSITION_COMPATIBILITY, INSTRUMENT_ELIGIBILITY, TRADE_RISK, PORTFOLIO_EXPOSURE, CONCENTRATION, CORRELATION, DRAWDOWN, STRATEGY_HEALTH, ENVIRONMENT, KILL_SWITCH and APPROVAL_EXPIRY. Each category has typed affirmative and rejection `RiskReason` codes, with an immutable category/outcome registry. `RiskFinding` binds one code to deeply immutable structured evidence (for example observed/required values and analytical identities). Evidence is not free-text-only authorization. Floats, unsupported objects and explicit credential/order/sizing authority fields are rejected. This is contract hygiene, not a general secret-detection or authentication system.

No gate coverage, sizing calculation, correlation algorithm, drawdown policy, strategy eligibility rule, lifecycle promotion, kill-switch evaluation or risk-state transition is implemented in 10A. A future evaluator must enforce mandatory gate coverage, unknown-data rejection policy, trusted configuration/snapshot sources and all platform limits before producing operational approvals.

## Environments

Environments are exactly LAB, PAPER, SHADOW and LIVE. All use identical context, identity and expiry constraints. There is no bypass flag. A context/configuration/snapshot environment mismatch is invalid. These domain labels do not create accounts or modify the existing PAPER-only SQL portfolio model. LONG and SHORT are architecture-level analytical directions; this does not enable leverage or live shorting. Future initial Quidax spot execution capabilities remain separate and LONG/CASH only. No execution capability is added for any environment.

## Identity, Decimal and UTC

Canonical hashing reuses TradeRail's existing tagged canonical JSON and SHA-256 implementation, with versioned `risk.* /v1` namespaces. `DOMAIN_VERSION` is 1.0.0. Nested contract identities and all material fields are bound; root derived identity fields are excluded from their own hash. No Python `hash()`, random IDs or wall-clock defaults are used. User/portfolio/instrument UUIDs and source analytical identities must be supplied explicitly.

Equivalent Decimal encodings (including signed zero), dictionary ordering and equivalent timezone representations yield the same identity. Position and finding sets are normalized by stable identifiers/codes, and duplicates are rejected. Descriptive evidence lists preserve order because that order may be material. Changed scope, prices, evidence, source identities, configuration, snapshots or validity context change the relevant identity. Functional decision/creation/evaluation/expiry timestamps are material, unlike informational research inspection timestamps elsewhere in Strategy Lab.

Financial fields require finite Python `Decimal`, not binary floats, integers or strings. No financial calculations or rounding are introduced. `to_wire` returns detached JSON-compatible values with exact fixed-point Decimal strings and explicit UTC `Z` timestamps, preserving original trailing zeros if present. Canonical semantic identity is separate from wire representation. Aware timestamps in any offset are normalized to UTC; naive timestamps are rejected. No local timezone or current time is inferred.

## Approval expiry

Approval is for one exact intent, configuration, portfolio snapshot and market/data context. It must expire strictly after evaluation and no later than **all** of: intent expiry, original proposal market-context expiry, evaluated market-context expiry, portfolio snapshot expiry and configured maximum approval age. Equality with an upper bound is allowed; equality with evaluation time is not. Rejected records have no approval expiry and can represent stale inputs with typed rejection evidence. Evaluation cannot precede creation or any input observation.

An expired context cannot be used to construct an approval. Execution-time revalidation, single-use consumption, replay prevention, revocation and authoritative evaluator identity are not implemented; they must be designed before any future execution consumer. An approval is never indefinite instrument-level permission.

## Tests and explicit exclusions

Synthetic tests exercise immutability, Decimal/UTC handling, identity semantics, all environments, reason categories, operating/hard-limit boundaries, validity intervals and dependency isolation. They require no database or provider. The existing pytest-wide database guard remains unchanged.

10A adds no API, strategy integration, database model/migration, provider/private API access, credentials, orders, execution adapter, sizing, risk evaluations, promotion, switch controls or frontend. No dependency is added. Before 10B, define trusted snapshot/configuration assembly and valuation provenance, mandatory gate coverage and unknown-data policy, actual conservative operating/hard-limit hypotheses and profile relationships, strategy/market/environment eligibility and the evaluator interface. None of these decisions is inferred from the current research dataset.

## Milestone 10B — Position sizing and trade-level risk v1.0.0

10B adds pure domain code under `backend/app/risk/trade_risk`. It builds on the unmodified 10A intent/configuration/snapshot/decision contracts. Only the shared typed reason vocabulary is extended for trade-level findings. There is still no final multi-gate Risk evaluator, execution authority, public API, persistence, migration or frontend integration.

### Required inputs and trust boundary

`SizingAssumptions` requires an explicit risk entry reference price, immutable `MarketRiskContext` for that reference, requested risk fraction, `TradeRiskCosts` and `TradeSizeConstraints`. The strategy's reference price remains separate in the result. Neither reference is an eventual execution fill. All rates, prices, quantities, budgets and costs are finite `Decimal`; no financial float conversion is used. There are no default production fees, risk presets or venue-specific claims.

Callers must supply TradeRail-owned snapshots and platform limits from trusted backend sources. These pure constructors are not a frontend trust mechanism. No user-facing route accepts these objects. Equity and available cash cannot be unknown. Cost rates must be explicitly supplied, including zero when zero is the deliberate research assumption. Missing required contracts produce a typed `TradeRiskCalculationError`; malformed contracts raise deterministic validation errors before calculation. Unknown values are never converted to zero.

Snapshot scope, configuration environment and risk entry canonical context must match the intent. The trade-level evaluator checks causal observation times and rejects expiry at or before evaluation for the intent, original market context, risk entry context and snapshot. It uses a caller-supplied aware evaluation timestamp, normalized to UTC; no wall-clock default is used.

**Currency limitation:** this v1 calculation requires the canonical quote currency to equal the snapshot valuation currency. It rejects a USD portfolio / USDT quote combination rather than assuming parity. There is no FX fetch, implicit substitution or invented conversion. An authoritative conversion/valuation contract is needed before cross-currency sizing can pass.

### Potential loss and exact cost convention

Let `E` be the explicit risk entry reference and `S` the intent invalidation boundary. Require `0 < S < E` for LONG and `0 < E < S` for SHORT. A valid strategy proposal can become invalid for sizing if its new risk entry reference crosses the boundary; this is rejected explicitly.

For each side independently, let `s` be the declared **full spread fraction**, `l` slippage fraction and `f` trading fee fraction. Adverse displacement is `a = s / 2 + l`. Rates are nonnegative finite fractions; `a < 1` is required so modeled prices remain positive. Spread and slippage are additive against that side's unadjusted reference, not compounded and not favorable.

- LONG: modeled adverse entry `Pe = E * (1 + ae)`; modeled adverse invalidation exit `Px = S * (1 - ax)`.
- SHORT: modeled adverse entry `Pe = E * (1 - ae)`; modeled adverse invalidation exit `Px = S * (1 + ax)`.
- Entry spread/slippage costs per base unit are `E * se / 2` and `E * le`.
- Exit spread/slippage costs per base unit are `S * sx / 2` and `S * lx`.
- Fees per base unit are calculated separately on the modeled adverse prices: `Pe * fe` and `Px * fx`.
- Base adverse price loss is `E - S` for LONG and `S - E` for SHORT.
- `L = base adverse price loss + entry spread + entry slippage + entry fee + exit spread + exit slippage + exit fee`.

Thus LONG `L = Pe - Px + entry fee + exit fee`, and SHORT `L = Px - Pe + entry fee + exit fee`. These are alternative reconciliation expressions, not extra cost charges. `UnitLossModel` exposes every component. Stop/invalidation prices and costs are hypotheses; actual gap losses, liquidity and fills are not guaranteed by this model.

### Budget and maximum quantity

`effective_fraction = min(requested_fraction, operating.max_trade_risk_fraction, platform.max_trade_risk_fraction)`

`permitted_monetary_risk B = snapshot.equity * effective_fraction`

The requested fraction may reduce risk or be capped by the explicit limits; the effective fraction and binding constraints are returned. 10A still rejects operating configuration above platform hard limits rather than silently clamping configuration. A zero fraction/equity produces a known zero budget and a trade-risk rejection, never approval.

`risk_quantity_bound = floor_decimal(B / L)`

The theoretical quotient may recur. `floor_decimal` produces a finite, conservative 50-significant-digit Decimal using a local `ROUND_FLOOR` context, independent of the ambient Decimal context. Existing exact finite sum/product primitives are reused. Numerical failures reject explicitly rather than yielding Infinity, NaN or fallback values.

### Notional and capital constraints

A mandatory individual exposure ceiling is `equity * min(operating.max_gross_exposure_fraction, platform.max_gross_exposure_fraction)`. The configuration caps this fraction at one. Optional additional platform/operating absolute notional caps can only tighten it. An additional operating cap cannot exceed its corresponding declared platform cap. These caps are in the snapshot/quote currency, which must match.

For conservative entry exposure, `Pn = max(E, Pe)`. Projected notional is `quantity * Pn`, so adverse LONG entry prices cannot overrun a notional cap; adverse SHORT proceeds do not create extra capacity. Reference notional `quantity * E` is exposed separately. The notional quantity bound is `floor_decimal(notional_ceiling / Pn)`.

The available unleveraged capital ceiling is `C = min(snapshot.available_cash, snapshot.equity)`. LONG requires reference notional plus all modeled entry costs, including fees: `required_cash = quantity * (E + total_entry_cost_per_unit)`. This equals modeled LONG purchase value plus entry fee; spread/slippage must not be counted again by adding costs to the already-adverse notional. The cash quantity bound is `floor_decimal(C / (E + total_entry_cost_per_unit))`.

SHORT is supported only in LAB/PAPER contextual sizing. V1 reserves the same reference notional plus entry-cost amount as conservative research collateral, and never reuses short-sale proceeds or assumes borrowing/leverage. This is not venue-realistic margin. SHORT contextual sizing rejects SHADOW/LIVE; the unit-loss primitives remain venue-neutral. Future environment/capability gates remain mandatory. No Quidax spot short capability is implied.

The unconstrained risk bound is reduced by notional, cash and any explicitly declared maximum base quantity. 10A's aggregate exposure/position/open-risk/concentration limits are **not** deemed satisfied by this individual ceiling; future portfolio gates must evaluate their remaining headroom.

### Quantity constraints and rounding

`TradeSizeConstraints` separates additional platform and operating notional/quantity caps, optional minimum quantity and optional positive quantity increment. All fields are explicit. `None` for an optional additional rule means no such rule has been declared; the mandatory 10A ceiling still applies. It does not assert that missing venue lot metadata is safe for execution. Unknown mandatory instrument metadata must fail the future instrument gate.

The constrained maximum is the smallest risk/notional/cash/quantity bound. If an increment is supplied, integer-ratio arithmetic floors to an exact multiple of that increment (for example a 0.03 increment is not merely two decimal places). It never rounds up. If the result cannot meet a declared minimum, the result is zero with a rejection. No exchange increment or precision is invented. Without an increment, the conservative finite quotient representation applies; this is numerical resolution, not an exchange lot-size claim.

`proposed_quantity=None` explicitly requests maximum-sizing evaluation. A supplied positive proposed quantity is inspected exactly as supplied: it is never silently resized or rounded. A proposal above a risk, notional, cash, maximum-quantity or increment constraint is rejected, while the computed constrained maximum remains available as evidence.

### Result, identity and gate boundary

`TradeSizingResult` is immutable and binds exact input identities, strategy/risk reference distinctions, unit-loss decomposition, effective fraction/budget, risk-only and constrained maximum quantities, notional/cash ceilings, maximum projection and typed binding constraints. `SizeProjection` preserves quantity, reference notional, conservative notional, entry/exit costs, modeled loss and required cash. Post-construction invariants prevent the maximum projection exceeding the computed limits.

`evaluate_trade_risk` returns immutable `TradeRiskEvaluation` with scope `TRADE_RISK_ONLY`, exactly `TRADE_RISK_PASS` or `TRADE_RISK_REJECTED`, explicit evaluation time, sizing/projection where available and typed `RiskFinding` evidence. Positive findings reuse 10A's affirmative reason metadata; they are not global approvals. The evaluator **never constructs `RiskDecision`**, exposes approval expiry, authorizes execution or runs strategies. Unknown portfolio/regime/strategy/health/kill-switch evidence is not treated as zero or as satisfied. Those unimplemented mandatory gates remain outstanding; trade-level mathematical viability does not bypass them.

New reasons cover risk budget availability, invalid invalidation geometry, binding notional/cash/quantity/increment constraints, missing required risk data, zero safe quantity, explicit notional/cash/quantity violations, context mismatch, unsupported currency valuation, research-only SHORT and numerical calculation failure. Existing trade-risk-limit and expiry codes are reused.

Versioned canonical SHA-256 identities reuse 10A serialization. Equivalent Decimal encodings and timezone offsets preserve semantic identity; meaningful entry/boundary/equity/configuration/cost/constraint/provenance changes alter it. Mathematical calculation identity binds inputs; evaluation identity additionally binds evaluation time and findings. No random IDs or current-time reads are introduced. Exact fixed-point strings and UTC timestamps are preserved by existing `to_wire` serialization.

### Hand-calculated examples and remaining boundaries

Synthetic examples, not production settings:

- Equity 10,000, risk fraction 0.005, entry 100, LONG boundary 90 (or SHORT boundary 110), zero costs: budget 50, loss per unit 10, risk maximum quantity 5, modeled loss 50.
- Entry 100, LONG boundary 90, full spread 0.02 and slippage 0.005 on both sides, fee 0.001 on both modeled prices: entry 101.5, exit 88.65, entry costs 1.6015, exit costs 1.43865, loss per unit 13.04015. A separate maximum quantity 2 gives modeled loss 26.08030 and required cash 203.2030.
- Same assumptions for SHORT boundary 110: modeled entry 98.5, exit 111.65, entry costs 1.5985, exit costs 1.76165, loss per unit 13.36015. Quantity 2 gives modeled loss 26.72030 and research cash reserve 203.1970.

No production/live numeric limits, exchange lot metadata, trusted snapshot builder, FX valuation, aggregate portfolio gates, correlation, strategy eligibility, risk-state transitions, operational kill switches, final approval, execution-time revalidation, orders, execution adapters, API, frontend or persistence are added. Before 10C, define trusted snapshot/configuration assembly and remaining portfolio headroom/compatibility gates; keep execution-time revalidation and authoritative instrument constraints mandatory before any future exposure authorization.

## Milestone 10C — Portfolio risk v1.0.0

The pure `backend/app/risk/portfolio_risk` module answers whether an exact successfully sized proposal fits the evaluated aggregate portfolio constraints. It produces `PORTFOLIO_RISK_PASS` or `PORTFOLIO_RISK_REJECTED`, with scope `PORTFOLIO_RISK_ONLY`. Neither outcome is a final global `RiskDecision`; no execution, ledger mutation, automatic resizing, API, frontend or persistence is added. All existing 10A/10B contracts and calculations remain unchanged; their shared typed reason vocabulary is extended.

### Inputs, trust and upstream binding

`evaluate_portfolio_risk` consumes the exact `TradeIntent`, successful `TradeRiskEvaluation`, `RiskConfiguration`, `PortfolioRiskSnapshot`, explicit `PortfolioRiskPolicy`, proposal-specific `PortfolioRiskContext` and an explicit aware evaluation timestamp. The upstream result must match intent/configuration/snapshot identities and the **same evaluation instant**. A failed trade gate is always ineligible. Holding all gates at one declared instant prevents an old trade pass from being reused after its original pricing evidence may have expired. Future execution still needs independent revalidation.

The proposed exposure/risk comes from the upstream **evaluated projection**, not its maximum-size projection. Existing 10B `project_size` is reused solely to check that upstream quantity/loss/cost evidence reconciles; indicators, sizing, strategies and execution are not rerun. Proposal invalidation/reference provenance is checked against the bound intent. Missing required contracts raise typed `PortfolioRiskInputError`; unavailable, stale or inconsistent usable contracts yield a scoped rejection with null projection, never fabricated zero evidence.

The context binds intent, snapshot, policy, valuation currency/identity, source identity and validity interval. It carries explicit strategy attributions, correlations and optional configured exposure groups. Its observation must follow the snapshot and all supplied correlation observations and cannot be in the future. Evaluation rejects expired context/snapshot/intent data. Identical evaluation instants represented in different offsets normalize to UTC.

These contracts require **trusted TradeRail-owned backend assembly**. Hash identities provide reproducibility/integrity, not authentication or proof that a caller is authorized to declare platform limits, positions, groups or strategy attribution. There is no frontend or public endpoint accepting such claims. Trusted assembly and source-quality governance remain integration work.

### Complete inventory and reconciliation

`open_positions`, `open_risk`, `gross_exposure`, `long_exposure` and `short_exposure` must all be known. A known-flat portfolio explicitly supplies an empty position tuple and zero aggregates. `None` never becomes an empty portfolio or zero risk.

Every position needs known positive exposure and known nonnegative modeled open risk. No incomplete position is dropped. Position UUIDs are unique under 10A; canonical instrument UUID/symbol attribution must also agree across the full inventory and proposal. When a mark is supplied, exposure cannot understate quantity times that mark. A mark is not required to redo a valuation when a trusted pre-valued exposure amount is already supplied; this module does not fetch marks or infer values.

Existing open risk and all exposure aggregates must exactly reconcile with the entire inventory. Gross exposure is the unsigned sum of position exposures, and LONG plus SHORT exposure equals gross. Optional snapshot instrument/configuration concentration maps are checked against inventory-derived ratios when supplied, including rejecting unknown entries or incomplete/inconsistent maps. Absent optional ratio maps do not erase known position exposure: ratios are derived from the required complete inventory. Likewise, the proposal-anchored correlated measure is derived from explicit pair evidence; an optional supplied correlated aggregate must match that measure when present.

### Before/after projection and limits

Let `E` be trusted snapshot equity, `R` upstream modeled trade loss, `X` upstream conservative notional exposure and `C` upstream required capital. Counterfactual projection is additive:

- `open_risk_after = open_risk_before + R`
- `gross_exposure_after = gross_exposure_before + X`
- Add `X` to the proposing direction; leave the opposite direction unchanged.
- Add `X` to the proposing canonical instrument and strategy family/version exposure.
- `available_cash_after = available_cash_before - C`
- `position_count_after = position_count_before + 1`

Each proposal conservatively consumes one new position slot. Existing lots are not silently merged, offset or reversed. SHORT keeps the 10B fully funded LAB/PAPER research reserve convention; no sale-proceeds reuse, borrowing or LIVE/SHADOW shorting is introduced. Existing SHORT positions are also rejected in environments unsupported by this capital model.

Equity is held fixed as the **pre-trade risk denominator**, not recalculated as an accounting/P&L result. Cash is a projected commitment/reserve, not a real payment. Supplied unleveraged available cash cannot exceed equity. Projected commitments must fit available cash, and projected gross exposure must satisfy the configured ceiling (at most equity). A projection exceeding any limit is retained for inspection but cannot pass.

Monetary ceilings use exact multiplication: `limit_amount = E * min(operating_fraction, platform_hard_fraction)`. This applies to open risk, gross exposure, each instrument, both directions, each strategy family/version and correlated exposure. Position count uses the smaller configured integer ceiling. Group caps similarly use the stricter explicit operating/platform group fraction. There are no production numeric defaults.

Concentration is unsigned exposure divided by frozen equity. Checks cover every existing/projected instrument and strategy bucket and both directions, including pre-existing breaches outside the proposing bucket. Decimal ratio evidence uses the project's existing 50-significant-digit division convention; **pass/fail compares exact monetary amounts against equity times the limit**, so ratio rounding cannot hide a threshold breach. Binding-limit inspection includes both reached and exceeded limits. Portfolio rejection never resizes the upstream proposal.

Projection contracts enforce required check coverage (aggregate risk, gross, both directions, every instrument/strategy bucket, correlation, capital, count and all declared groups), arithmetic reconciliation and retention of every existing position in correlation evidence. Checks cannot be omitted to manufacture a portfolio pass. Frozen snapshots, inputs and correlation evidence are not mutated.

### Strategy concentration and attribution

10A positions carry strategy configuration identities, not reversible family/version information. New immutable `StrategyAttribution` records explicitly map each existing/proposed configuration identity to its trusted family and version, with source identity. Missing attribution fails closed. The proposing attribution must match the actual intent definition.

Allocation buckets use a deterministic family/version identity. Multiple configurations belonging to the same family/version share one allocation bucket, preventing configuration changes from splitting an allocation. Per-configuration exposure remains separately available for provenance and legacy snapshot ratio reconciliation. Different declared versions remain separate; family-wide cross-version governance/allocation policies are not invented here.

### Correlation evidence and exact conservative policy

`CorrelationEvidence` records an unordered pair of canonical instruments, finite Decimal coefficient in [-1, 1], provider, timeframe, method, observation count, observation start/end, as-of/expiry and source identity. Reversing the pair does not change its identity. Duplicate pair declarations are rejected rather than selecting a favorable/latest row. Rows in a context share an explicit observation window/provider/timeframe/method/count. Windows must be chronological and end no later than as-of; at least two declared observations are structurally required, not asserted statistically sufficient.

`PortfolioRiskPolicy` requires explicit platform and operating absolute-correlation thresholds and maximum evidence ages. The operating threshold can only be lower/equal (counting more relationships is stricter), and the operating maximum age can only be shorter/equal. Every supplied row must be current: no future observations, evaluation strictly before expiry, and age no greater than the effective maximum. Expiry equality is stale; maximum-age equality is allowed. No provider call, correlation estimation, default zero correlation or asset-name heuristic exists.

For the proposed canonical instrument as anchor:

1. Existing positions in the identical canonical instrument count in full with mathematical identity coefficient 1; no empirical self-correlation is required.
2. Every distinct existing instrument requires explicit trusted pair evidence, even if the eventual coefficient is below threshold.
3. If `abs(coefficient) >= effective_threshold`, count the position's entire unsigned exposure. Otherwise retain the contribution/evidence but exclude it from this particular correlated bucket.
4. `correlated_before` is the sum of included existing exposures; `correlated_after = correlated_before + X`. Compare against the configured correlated-exposure ceiling.

Direction-adjusted correlation is recorded (negate the coefficient when directions oppose) for explainability, but **never reduces exposure**. Strong positive or negative correlations count regardless of LONG/SHORT orientation. There is no hedging credit, coefficient weighting, netting, covariance/VaR calculation or transitive clustering. This is a proposal-anchored direct-pair conservative exposure measure, not a complete portfolio covariance model. Missing or stale required evidence rejects; the engine never guesses BTC/ETH or other relationships.

### Explicit exposure groups

`ExposureGroup` binds a caller-defined identifier, explicit canonical membership set, trusted source identity and operating/platform concentration fractions. No business group or symbol-name membership is hard-coded. The context declares the complete set of configured groups; an explicit empty tuple means no additional group rule is configured, not unknown required membership. Trusted backend configuration must own this declaration.

Each configured group sums full member exposures before and after the proposal and applies its own cap. Overlapping groups are evaluated independently; their amounts are not summed into a fake portfolio total. Group order/member order is identity-neutral; duplicate group identifiers or members are invalid. No optional flag disables the mandatory core portfolio gates.

### Currency and exclusions

All monetary amounts must share the snapshot valuation currency and provenance. V1 preserves 10B's same-quote rule for the proposal **and every existing position**. Unsupported cross-currency aggregation fails closed even if a caller supplies an opaque valuation identity; there is no implemented explicit FX conversion contract and no USD/USDT parity assumption. Conversion evidence/assembly is future work, not implicit permission.

Typed findings distinguish upstream failure, unavailable/inconsistent evidence, incompatible valuation, missing/stale correlations, aggregate open risk, gross exposure, instrument/directional/strategy concentration, correlated exposure, groups, capital and position-count limits. A limit rejection retains structured checks with before/observed/required amounts. A missing-data rejection uses a null projection, not invented financial values.

Versioned canonical SHA-256 identities bind all exact intent/trade/configuration/snapshot/policy/context inputs plus evaluation time and projected evidence. Equivalent Decimal encodings, pair direction, input collection ordering and equivalent UTC offsets preserve identity. Changed positions, valuation, boundaries, attribution, coefficients/windows/sources, groups, limits or proposal evidence change the appropriate identity. Exact Decimal strings remain available through existing `to_wire` serialization.

Hand-calculated synthetic oracles include: equity 10,000, existing risk 20/exposure 1,000, and a 10B proposal of risk 10/exposure 100 gives projected risk 30, gross 1,100 and instrument/strategy concentration 0.11. LONG 1,000 plus SHORT 2,000 and new LONG 100 gives gross 3,100, LONG 1,100 and SHORT 2,000, never net 900. With explicit absolute correlation 0.9 to the other instrument, all 3,100 counts in the anchored correlated bucket regardless of sign/direction. These are test assumptions, not empirical correlations or production limits.

10C does not create final Risk approval, operational risk-state transitions, strategy eligibility, kill-switch controls, execution-time revalidation, portfolio ledger writes, orders, private APIs, lifecycle promotion, API/UI, migrations or dependencies. A portfolio pass means only that the proposed exposure fits the evaluated portfolio constraints. Before 10D, trusted snapshot/configuration/attribution/correlation/group assembly and source-quality governance remain essential; remaining global risk gates and revalidation cannot be bypassed.

Deterministic rejection ordering is also enforced: optional concentration evidence is examined in stable key order. Mixed unknown/inconsistent evidence therefore produces identical reason codes and report identities across fresh processes with different Python hash seeds; no safety data is repaired or discarded.


## Milestone 10D — Drawdown and risk states (domain v1.0.0)

Purpose: control permission for **new exposure** as trusted portfolio equity deteriorates.
This is a pure domain gate, not a final Risk approval, order, liquidation instruction,
API, persistence layer, or automatic lifecycle transition. The 10A–10C contracts and
financial calculations are preserved. No production numeric thresholds or presets
are supplied. All thresholds/multipliers are explicit caller-supplied research/configuration
hypotheses requiring review before operational use.

### Drawdown and peak convention

For positive trusted historical peak `P` and nonnegative current equity `E`:

- `amount = max(P - E, 0)`
- `fraction = amount / P`
- `derived_peak = max(P, E)`

A new high produces zero current drawdown and a derived higher peak without mutating
historical evidence. Updating trusted peak history belongs to a future TradeRail-owned
state builder; this gate performs no writes. A historical peak of zero is unknown/invalid
for drawdown permission, **not zero drawdown**: it produces `INVALID_PEAK_EQUITY` rejection,
even if current equity is positive. Negative/non-finite values, floats, naive timestamps
and malformed contracts raise controlled domain `ValueError`s before evaluation.
Zero current equity with a positive peak measures 100% drawdown and is suspended.

Financial arithmetic uses the established Decimal exact-sum/product helpers. Repeating
ratios use the project's deterministic 50-significant-digit Decimal division convention;
finite wire values remain exact strings. Entry/recovery threshold decisions compare
`amount` against `P * threshold` exactly, avoiding rounded-ratio threshold errors.
No ambient Decimal precision, float, implicit FX conversion, provider or wall clock is used.

### Explicit state policy and platform boundary

`RiskStatePolicy` is an immutable platform-owned input with version, three strictly
increasing entry fractions, three strictly increasing recovery fractions, four state
multipliers and a positive maximum evidence age. Each recovery fraction must be strictly
below that state's entry fraction. Entry fractions are in `(0, 1]`; recovery fractions
are in `[0, 1]`. Multipliers satisfy `1 >= NORMAL >= CAUTION >= DEFENSIVE >= SUSPENDED = 0`.
No user policy endpoint exists. Future trusted construction must enforce policy ownership.

At evaluation, the suspension entry threshold must not exceed the stricter existing
operating/platform `max_drawdown_fraction`; otherwise the gate rejects `INVALID_STATE_POLICY`.
The base new-risk capacity may not exceed equity multiplied by the stricter existing
trade-risk fraction. Thus neither a state policy nor a supplied base allowance can bypass
existing hard ceilings. This is an additional reduction, not a recalculation of 10B/10C.

### Transition semantics and hysteresis

The exact states remain `NORMAL`, `CAUTION`, `DEFENSIVE`, `SUSPENDED`.

1. Determine the highest entry threshold reached (`drawdown >= entry`, inclusive).
2. If this is more severe than the trusted previous state, escalate directly to it.
3. Otherwise, if it is less severe and `drawdown <= previous state's recovery` (inclusive),
   recover exactly one severity step.
4. Otherwise retain the previous state.

Escalation therefore has priority over recovery. Full equity recovery does not jump from
SUSPENDED directly to NORMAL. Each further recovery requires a subsequent explicitly
supplied trusted state evaluation; there is no hidden process memory. Recovery uses a
strictly lower threshold, so microscopic oscillation around an entry level cannot cause
state flapping. There is no time-based cooldown or minimum dwell-period policy in 10D.

### Evidence and reconciliation

`DrawdownEvidence` binds scope (user, portfolio, environment, valuation currency), exact
snapshot identity, valuation identity, observed equity, historical peak and its observation
time, previous state, observation/expiry times and source identity. The peak observation
must not follow the current observation. Current equity, previous state and observation
time must match the supplied `PortfolioRiskSnapshot` exactly. Optional snapshot drawdown,
when present, must also reconcile; absent optional drawdown is derived from trusted
peak/equity, not replaced with zero.

The explicit evaluation instant must be timezone-aware. All instants normalize to UTC.
Evidence must be non-future, unexpired and no older than the policy maximum age. Age equal
to the maximum is accepted; expiry equal to evaluation time is rejected. Snapshot expiry
is independently enforced. Missing inputs, mismatched portfolio/environment/valuation,
stale/future evidence, equity mismatch or invalid policy fail closed. Unknown evaluation
state is left unavailable on rejection rather than fabricated as NORMAL or SUSPENDED.

`NewRiskCapacity` binds exact scope, snapshot/configuration/intent/upstream identities,
base permitted monetary risk and proposed modeled monetary risk, in the portfolio valuation
currency. These are **trusted orchestration inputs**, not authoritative frontend claims.
Digests establish reproducibility, not source authentication or proof of upstream approval.
A future orchestrator must construct them from successful 10B/10C evidence. No leverage or
LIVE-short permission is introduced: environment/eligibility gates remain independently mandatory.

### Adjusted capacity and outcomes

`adjusted_new_risk = base_permitted_new_risk * evaluated_state_multiplier`.

This can only equal or reduce upstream capacity. CAUTION/DEFENSIVE may pass positive proposed
risk that fits the reduced allowance, or may be configured to permit none. Zero base capacity,
zero adjusted capacity or a zero-risk proposal does not create implicit permission.
SUSPENDED **always rejects new exposure**, including a zero proposal. It never closes,
resizes or liquidates existing positions.

The immutable result contains input identities, explicit evaluation instant, measurement,
previous/evaluated state, transition, multiplier, base/adjusted/proposed amounts, typed findings,
version and identity. Outcomes are exactly `RISK_STATE_PASS` / `RISK_STATE_REJECTED`, scoped
`RISK_STATE_ONLY`. A pass means only that this proposed new risk fits the evaluated state policy.
It is not `FINAL_RISK_APPROVED`, execution authority, or permission to bypass another gate.

### Reproducibility and exclusions

Canonical SHA-256 identities reuse existing TradeRail normalization; equivalent Decimal
representations and equivalent UTC instants have the same identity. Material input changes
alter identity. Findings are copied into immutable, code-sorted order, rejecting duplicates.
Fresh-process tests cover four Python hash seeds, including multiple invalid inputs and
unordered finding reconstruction. No financial formula or threshold tuning is performed.

10D excludes persistence, state-history retrieval, hard-limit calibration, automatic peak
updates, API/UI, kill-switch operations, final Risk approval, execution-time revalidation,
orders, liquidation, private exchange access and all position-management actions. Trusted
builders, policy governance and durable recovery state must be specified before runtime wiring.


## Milestone 10E — Kill switches and strategy health (domain v1.0.0)

Purpose: a deterministic veto on **new exposure**, independently of trade sizing,
portfolio limits and drawdown permission. This gate does not construct final RiskDecision,
authorize execution, liquidate positions, mutate lifecycle or promote strategies.
10A–10D contracts/calculations are preserved; only typed reasons are extended.

### Exact switch targets and authority boundary

The frozen `KillSwitchScope` vocabulary is reused, without name-based inference:

- USER: exact user UUID.
- PORTFOLIO: exact portfolio UUID.
- STRATEGY: exact family/version, optionally exact configuration identity. An explicitly
  absent configuration selector covers all configurations of that family/version;
  it never matches another version. No family-wide/all-version inference is implemented.
- MARKET: exact canonical instrument UUID **and** canonical symbol; independent of provider.
- DATA: exact provider plus every explicitly supplied instrument, timeframe and dataset
  identity restriction, combined with AND. Omitted restrictions are declared broad scope,
  not missing observations. Provider-wide targeting must be explicit.
- SYSTEM: all proposals in the switch's declared environment.

Targets reject extra fields belonging to another scope. Switches always declare an
exact LAB/PAPER/SHADOW/LIVE environment; no environment wildcard or implicit cross-environment
switch exists. Broader operational control requires explicit records per environment.

`KillSwitchEvidence` carries a stable switch UUID, target, environment, active Boolean or
explicit unknown, typed cause, activation time, optional operational expiry, observation time,
evidence validity and source identity. A source digest is provenance, not authentication.
Only future TradeRail-owned trusted builders may supply these contracts; arbitrary frontend
records cannot be authoritative. There is no API or owner-control surface in 10E.

`KillSwitchInventory` is an immutable, source-identified completeness attestation bound to
one exact intent, portfolio scope and market context. It must explicitly cover **all six
scopes**. An empty switch tuple is allowed only with that complete fresh attestation.
Missing inventory/coverage never means no switches. Duplicate switch UUID observations,
including contradictory active/inactive updates, are rejected rather than selecting a winner.
The inventory and each applicable row are freshness-checked separately.

### Active, inactive, expired and unverifiable observations

Every applicable, observed active switch whose operational expiry has not arrived blocks
new exposure. Multiple active switches remain visible, including multiple switches of the
same scope. Inactive rows do not block. Operational expiry is inclusive: at/after expiry,
the switch no longer blocks **on activation grounds**. This does not waive evidence validity:
a stale/unknown/expired required observation still rejects the gate, even for a switch whose
operational expiry has passed. No last-known state is silently treated as inactive.

Future inventory/observations reject. Evidence expires at equality (`evaluation >= valid_until`);
age equal to a maximum is accepted, and strictly greater age rejects. Activation must not
follow observation, and operational expiry must follow activation. Unknown active state
fails closed. Unrelated validly targeted rows remain in the inventory identity but do not
apply; their row freshness/health cannot block a different user, portfolio, strategy, market,
data context or environment. The required inventory itself must always be fresh and bound.

A relevant active switch yields `KILL_SWITCH_REJECTED` regardless of ordinary upstream
permission. Upstream results and existing positions are never modified.

### Health versus lifecycle

The existing Strategy Lab `StrategyLifecycle` / `StrategyState` vocabulary is reused.
Strategy health is a distinct attested observation with exactly:
`HEALTHY`, `DEGRADED`, `UNHEALTHY`, `SUSPENDED`, `UNKNOWN`.
It answers eligibility for new exposure, not probability of profit. No LLM, scoring,
ranking, optimization or automatic health promotion is involved.

Health and lifecycle evidence must match the intent's exact canonical strategy family,
version and configuration identity, as well as environment. Each has its own observation,
validity interval and source identity. Health also declares its historical observation window.
An otherwise fresh health attestation cannot relabel old metric data as recent: policy
separately bounds age from `data_through`. All times are explicit, aware and normalized to UTC.

`SafetyGatePolicy` must explicitly list allowed lifecycle states for every environment,
including explicit empty deny-all lists. No defaults are supplied. SUSPENDED/RETIRED lifecycle
can never be permitted. The structural envelope restricts SHADOW to SHADOW/LIMITED_LIVE/LIVE,
and LIVE to LIMITED_LIVE/LIVE; within it the policy may only narrow eligibility. LAB/PAPER
require explicit policy permission for any non-suspended/non-retired research or later stage.
Thus HEALTHY plus BACKTESTED can never become LIVE through this gate. Nothing changes lifecycle.

HEALTHY is eligible only when explicitly allowed and every configured evidence requirement
passes. DEGRADED is separately permitted or rejected by policy; it is never silently HEALTHY.
UNHEALTHY, SUSPENDED and UNKNOWN always reject and cannot be added to the permitted policy set.
No automatic position closure occurs.

### Optional metrics and explicit vetoes

Health state is supplied as trusted derived evidence, not inferred by this gate. Optional
metrics retain None: completed trades, expectancy (including negative observations), profit
factor, drawdown, stress degradation, data sufficiency, and existing Strategy Lab validation
verdict/report identity. A verdict and report identity must be supplied together.

Caller-supplied policy may require minimum trade count/expectancy/profit factor, maximum
drawdown/stress degradation, sufficient data and specified research verdicts. Required unknown
values produce `INSUFFICIENT_HEALTH_EVIDENCE`; failed comparisons produce typed metric vetoes.
Minimum/maximum equality is allowed. A metric with no configured requirement remains unknown
if absent, not zero. Explicitly disabling optional metric gates still requires fresh trusted
health state and lifecycle evidence. No threshold is a production recommendation or preset.
A research VALIDATION_CANDIDATE remains evidence only and never promotes lifecycle.

### Combined result, ordering and reproducibility

`evaluate_safety_gate` binds the exact intent, RiskConfiguration, policy, inventory, lifecycle,
health evidence and explicit evaluation instant. The intent/configuration environments and
intent/market validity are checked. It does not rerun sizing, portfolio-risk or state engines.
Those gates remain independently mandatory in future composition.

The immutable result includes every applicable switch, every observed active blocker,
`KILL_SWITCH_PASS`/`KILL_SWITCH_REJECTED`, lifecycle eligibility (None when unverifiable), full
strategy evidence, typed findings, source identities, version and deterministic identity.
The combined outcomes are `SAFETY_GATE_PASS` / `SAFETY_GATE_REJECTED`, scoped
`KILL_SWITCH_AND_STRATEGY_HEALTH_ONLY`. Any rejection finding wins; independent checks continue
so a SYSTEM blocker, STRATEGY blocker, unhealthy state and several failed metrics all survive.
A missing health input does not hide known switch blockers, or vice versa. Missing mandatory
policy/context prevents meaningful checks and fails closed, never producing a partial pass.

A pass means only: **No evaluated kill switch or strategy-health condition blocks new exposure.**
It is not final Risk APPROVED, an order or permission to bypass another gate.

Canonical hashing reuses existing TradeRail conventions. Inventories normalize switch/coverage
order; policies normalize lifecycle/health/verdict order. Findings are grouped by typed code,
with canonical observation ordering. Equivalent Decimal representations and UTC instants preserve
identity. Source evidence, including irrelevant rows, remains bound to identity. Material switch,
target, expiry, lifecycle, metric, policy, strategy or environment changes alter the relevant hash.
Fresh-process tests cover four Python hash seeds, reordered multiple blockers and missing/malformed
inputs. No input object is mutated.

### Explicit exclusions and remaining ownership decisions

No persistence, switch-control API/UI, source authentication service, evidence collection,
health-score generation, final Risk approval, lifecycle promotion, portfolio mutation, execution,
liquidation, private exchange access or credentials exist in 10E. Before runtime/10F integration,
TradeRail must define trusted complete inventory builders, authoritative health/lifecycle sources,
platform policy ownership, observation-window calibration and operational switch/recovery controls.


Monetary expectancy observations and thresholds additionally declare their currency.
A configured minimum expectancy requires a policy currency; an unknown observed currency
fails as insufficient evidence, and a differing currency rejects as mismatched evidence.
No FX conversion or USD/USDT equivalence is inferred. This is the unit of the attested
strategy-health statistic, not a claim about portfolio cash or trade affordability.

Known attribution contradictions also fail closed: a target cannot use the proposal's
instrument UUID with a different canonical symbol (or vice versa), nor its exact strategy
configuration identity with another family/version to appear unrelated. Such evidence is
reported as mismatched, not silently ignored as a different market/strategy. Legitimately
different targets retain isolation. DATA attribution checks apply within its declared provider.


## Milestone 10F: integrated risk evaluation

`app.risk.integrated.evaluate_risk(RiskEvaluationInputs)` composes the existing
10B trade, 10C portfolio, 10D state and 10E safety evaluators and returns the
original immutable 10A `RiskDecision`. It does not repeat their financial
calculations. All four gates are mandatory; there is no bypass, disabling flag,
weighted score or partial approval.

### Evaluation and reconciliation

The fixed call order is trade risk, portfolio risk, risk state, then safety.
Portfolio and state require a passing, reconciled trade result. If that
prerequisite fails they are explicitly BLOCKED, with no fabricated downstream
financial result. Safety remains independently evaluable. A portfolio rejection
does not hide a state or safety rejection. Missing gate inputs, unexpected
results and calculation exceptions fail closed; exception text is not exposed.
Structurally missing/untyped root intent, configuration, snapshot or market
contracts fail input construction rather than inventing an exact-intent record.

Input and result reconciliation binds the intent, ownership, environment,
strategy family/version/configuration, instrument/provider/timeframe, snapshot,
configuration, policies, evidence and evaluation instant. In v1 the intent,
root market context and sizing entry context must have the same market-context
identity. The assumed entry price remains an explicit sizing assumption, not an
execution fill. Portfolio context, drawdown equity/previous state/historical
peak, switch scope and exact lifecycle/health attribution are reconciled.
State capacity is constructed internally from the actual trade projection's
modeled loss and its permitted risk budget; a caller cannot substitute a smaller
proposal to obtain state permission.

APPROVED requires four actual PASS results, complete context reconciliation,
fresh required evidence and a safely derived positive approval lifetime.
Anything else is REJECTED. Rejection findings preserve all safely evaluable
independent blockers, including simultaneous concentration, suspended-state,
kill-switch and health failures. Typed gate findings retain gate, disposition,
blocking status, source result/finding identities and source evidence. Canonical
ordering and grouping preserve multiple observations for the same reason code.
The manifest retains mandatory gate records, policy identities and the complete
immutable input identity.

The only additive change to the original RiskDecision contract allows a REJECTED
record to retain mismatched or future root context for audit, and only with the
explicit typed INTEGRATED_CONTEXT_MISMATCH rejection reason. APPROVED validation
is unchanged: context, causality, affirmative findings and bounded expiry remain
mandatory. A rejected record has no approval expiry and never becomes permission
as time passes; fresh evaluation is required.

### Approval expiry

Expiry is the earliest applicable boundary across configured maximum approval
age (operating and platform), intent, market and entry contexts, portfolio
snapshot/context, correlation validity and freshness, drawdown validity and
freshness, kill-switch inventory validity and freshness, applicable switch
validity and freshness, lifecycle validity and freshness, health validity and
freshness, and health-data freshness. Every boundary retains its source identity.
An applicable inactive switch still supplies required state evidence; an expired
operational switch does not block or create an already-expired operational
approval ceiling. Its evidence freshness/validity still applies. Unrelated
switch rows are not used to justify permission and do not shorten approval.
If the intersection is empty, non-positive or cannot be derived, evaluation
rejects. A gate accepting evidence exactly at its maximum age cannot create a
positive lifetime past that freshness boundary.

### Identity and authority boundary

The final existing RiskDecision identity binds the exact intent, configuration,
snapshot, market context, evaluation time, outcome, expiry and findings. The
integrated manifest binds complete input identity, policies and all source gate
record/result identities; the expiry finding binds all validity boundaries.
Canonical SHA-256 serialization normalizes equivalent Decimal representations,
UTC instants and declared unordered evidence collections. Findings are sorted
independently of incidental processing order or Python hash seed.

Approval concerns one exact TradeIntent and evaluated context, never a market,
strategy or user indefinitely. APPROVED is not an order, execution capability or
permission to bypass future revalidation. These are trusted in-process producer
contracts, not proof that a client-supplied hash or constructed decision is an
authentic authorization. Future execution must verify trusted integrated origin,
exact intent, unexpired approval and unchanged relevant state, perform
execution-time revalidation and eventually enforce single consumption. None of
that order-side machinery or persistence is implemented here.

### Synthetic oracles and exclusions

Tests use explicit research hypotheses: equity/cash 10000 USDT, risk fraction
0.005, entry 100, invalidation 90, zero costs and proposed quantity 1. Existing
trade math yields budget 50, per-unit loss 10, proposed loss 10 and exposure/cash
commitment 100. Portfolio open risk becomes 10, gross exposure 100 and cash
9900. Zero drawdown in NORMAL retains capacity 50. All six switch scopes are
explicitly covered and clear; lifecycle and health are eligible. At T+3 seconds,
a maximum approval age of 30 seconds gives T+33, while required evidence
freshness ends at T+30: final approval expires at T+30.

Rejection oracles cover a 10% drawdown reaching SUSPENDED despite other passes;
1090 instrument exposure exceeding a 1000 limit alongside an active SYSTEM
switch; stale health evidence; and individually passing records spliced from a
different snapshot. These parameters are test hypotheses, not production limits.

10F adds no API, frontend, database reads/writes, persistence, migrations,
providers, credentials, orders, execution adapters, automatic positions,
liquidation, lifecycle changes or strategy promotion. Earlier gate calculations
and thresholds remain unchanged. 10G may add inspection separately; approval
consumption and execution remain future design work.


## Milestone 10G: Risk API and operator inspection boundary

`POST /api/risk/evaluations` is a read-only evaluation operation despite using
POST. It accepts exactly `{"intent_identity":"sha256:<64 lowercase hex digits>"}`.
Additional fields are rejected. Callers cannot supply snapshots, equity, risk
configuration, health, lifecycle, switches, timestamps, quantities, overrides or
an approval outcome. No public approval-consumption or execution route exists.

### Server-side authorization

The existing cookie session and active User are required. Inspection additionally
requires the authenticated user's UUID in the server-owned
`TRADERAIL_RISK_INSPECTOR_USER_IDS` setting (JSON UUID array). The default is an
empty immutable set: nobody is granted inspection implicitly. This is a narrow
capability grant layered on existing authentication, not a second login/role
system. Emails, client headers, query/body role claims and frontend visibility
cannot grant access. Operators can inspect only intents belonging to their own
user identity in v1; there is no cross-owner delegation. Provisioning grants is
an explicit deployment/operator action; this milestone changes no .env file.
POST requires Origin to equal the existing configured frontend_url exactly.
All /api/risk/ responses, including failures, are no-store with no-referrer.

### Authoritative evidence construction and current availability

`TrustedRiskSource.resolve(user_id, intent_identity, evaluated_at)` is an explicit
server-only construction boundary returning existing immutable
RiskEvaluationInputs. Evaluation time is captured once at the HTTP boundary and
passed explicitly into construction and 10F. The service verifies requested
intent, authenticated ownership and evaluation instant before evaluating.
The source must reconcile portfolio ownership/status, canonical intent provenance,
server-owned platform/operating limits, exact valuation, complete portfolio and
market context, drawdown evidence, switch coverage, lifecycle and health. It must
not trust frontend assertions or synthesize clear/healthy/zero-risk state.

There is deliberately no production authoritative implementation yet. The
existing PaperPortfolio cash ledger alone cannot establish complete portfolio
risk, valuation, kill-switch coverage or strategy health; intents are not stored
or authoritatively generated by an application service yet. The default source
therefore raises a controlled 503 trusted_risk_evidence_unavailable. It performs
no database writes or external calls. Merely adding an inspector grant cannot
make approvals available. Tests inject explicit immutable trusted fixtures
through server-side FastAPI dependency overrides; there is no HTTP fixture mode,
client evidence injection or production fallback. Connecting real authoritative
sources is required before operational risk evaluation or Paper Execution.

### Engine reuse and inspection response

The read-only `integrated.inspection.inspect_risk` accessor uses the original
10F collector and finalizer once and retains their exact gate records for
inspection. It adds no calculations, policy decisions or alternative approval
path. Existing 10A-10F files, thresholds and APPROVED invariants are unchanged.
All four mandatory gates remain present; unmet prerequisites are BLOCKED, not
fabricated PASS results. Unknown gate evidence produces a deterministic REJECTED
risk decision where the existing domain can represent it.

Response fields are outcome (APPROVED/REJECTED), exact intent/decision identities,
proposed quantity, evaluated quantity and modeled risk where the original trade
projection exists, valuation currency, four gate outcomes/reasons/blockers and
record/result identities, explicit UTC evaluation/expiry timestamps, and relevant
input/configuration/evidence identities. Financial values are exact Decimal
strings; unavailable projections are null. Risk values are copied from original
results, never recalculated for display. Raw portfolio contents, full strategy
configuration and detailed engineering evidence are not dumped into the response.
There is no ordinary trader-facing risk response in this milestone.

### Failure and reproducibility contract

401 means no valid session; 403 means missing operator grant or untrusted Origin;
404 hides unavailable/wrong-owner intent resolution; 409 means a mismatched
source evaluation instant; 422 rejects malformed bodies. Missing/untyped trusted
sources, construction failures and evaluation failures return sanitized 503
errors without exception details, credentials or stack traces. A representable
unsafe/context-mismatched evaluation returns HTTP 200 with REJECTED and typed
blockers: it never becomes a partial approval.

An identical authoritative input bundle at the same explicit evaluation instant
produces the same response and original decision identity. Separate real-time
requests normally have different evaluation instants and may produce different
identities or freshness results; the caller cannot backdate evaluation time.

Risk API APPROVED still cannot execute a trade. There is no order placement,
private exchange access, approval persistence/consumption, lifecycle promotion,
position creation/liquidation, frontend or Owner Console. Trusted authoritative
adapters, execution-time revalidation and eventual single-use enforcement remain
prerequisites for later Paper Execution design, not capabilities added by 10G.
