# Trading Mandate foundation

A mandate is structured user intent, not a TradeIntent, strategy signal, Risk approval, order or trading permission. Saving, activating, pausing or retiring it has no portfolio, Risk, execution, provider or strategy side effects. ACTIVE means structurally ready only. No existing trading path reads mandates.

## Contract

PAPER, USDT, BTC/USDT spot and LONG_ONLY are the only supported environment/currency/market/direction. Required intent inputs are `capital_allocation` (positive finite exact decimal string), `risk_level` (CONSERVATIVE/BALANCED/GROWTH) and `trading_style` (SHORT_TERM/MEDIUM_TERM/LONGER_TERM). The fixed fields may be omitted or provided with their fixed values. Extra fields, including owner, status, quantity, market price, strategy parameters and trusted balances, are rejected. Allocation is an intention, not a cash reservation or proof of available capital. Multiple mandates may exist; no aggregate allocation or funding promise is made.

Risk level is a user preference, not a RiskConfiguration or authority to change platform limits. Trading style is not a raw timeframe. Future server-owned mapping to technical eligibility is deferred; CONSERVATIVE/GROWTH intent does not configure either profile for execution.

## API

All routes use existing cookie authentication and return no-store responses. Every write also requires the exact configured frontend Origin. Ownership always comes from the authenticated user; foreign and nonexistent IDs both return 404.

- `POST /api/mandates`: create a complete DRAFT (201).
- `GET /api/mandates`: list the user's mandates, newest first; empty list when none.
- `GET /api/mandates/{id}`: retrieve one owned mandate.
- `POST /api/mandates/{id}/intent`: replace all editable intent, supplying the same required fields as creation.
- `POST /api/mandates/{id}/activate`, `/pause`, `/retire`: lifecycle actions.

Example creation body: `{"capital_allocation":"2000.00","risk_level":"BALANCED","trading_style":"MEDIUM_TERM"}`.

Responses include mandate UUID, authenticated owner UUID, intent, status and explicit UTC created/updated timestamps. Financial values are decimal strings without binary float conversion or scale coercion. Validation errors return 422; invalid lifecycle or incomplete persisted intent returns 409. Database errors use the existing sanitized 503 handler.

## Lifecycle and persistence

DRAFT -> ACTIVE or RETIRED; ACTIVE -> PAUSED or RETIRED; PAUSED -> ACTIVE or RETIRED. RETIRED is terminal. Repeating the current lifecycle action is harmless. Intent edits are allowed only while DRAFT or PAUSED. Activation revalidates all persisted intent and the canonical active spot instrument. Drafts require complete intent in V1; partial drafting is deferred.

Row locks serialize edits and lifecycle actions. Ownership/identity/creation time are immutable. PostgreSQL enforces allowed values, positive finite allocation, mandatory fields, canonical market/owner foreign keys, lifecycle/edit rules and server timestamps. Hard deletion is refused; retire instead. This is a current-state intent record, not a complete edit-event audit log.

`0013_trading_mandates` adds one table, one owner/listing index and one lifecycle guard function/trigger. It changes no existing economic tables. Downgrade refuses while any mandate exists; it never deletes intent automatically. Migration deployment is explicit, never application startup. Source/offline DDL checks do not constitute real PostgreSQL verification; physical migration/trigger verification remains a separate isolated-database step before promotion. No migration is applied as part of implementation testing.

## Exclusions

No frontend, LLM input, LIVE mandates, SHORT, strategy selection, technical parameters, funding mutations, automatic observation, Risk evaluation, authorization, order creation, execution, billing or performance fees. Activation never starts trading.


## Technical eligibility (derived, resolver 1.0.0)

`GET /api/mandates/{id}/eligibility` uses existing authenticated ownership checks and no-store response headers. Missing and foreign IDs both return 404. No request body, client policy overrides or write endpoint exists. The only database reads are the owned mandate and its exact canonical instrument registry record; there are no market observations or financial writes.

The immutable result separates `user_intent` from `technical_eligibility`, exposes ELIGIBLE/INELIGIBLE, typed reasons, mandate/context/result identities, resolver version, policy version/identity and an explicit UTC `resolved_at`. Allocation stays exact Decimal context, serialized as a string. It never becomes a quantity, funding reservation or Risk-approved amount.

Historical policy `mandate-eligibility-v1` is preserved unchanged. Its technical mapping set is deliberately empty: v1 declares no SHORT_TERM/MEDIUM_TERM/LONGER_TERM -> strategy/configuration/timeframe mapping. The controlled Paper source's existing 1h setup is not such a mapping. Under v1, all otherwise valid ACTIVE mandates return INELIGIBLE with STYLE_MAPPING_UNCONFIGURED and STRATEGY_MAPPING_UNCONFIGURED, with no eligible configuration. No existing Adaptive Trend configuration is automatically declared compatible and no lifecycle/performance evidence is manufactured.

The exact existing BALANCED `paper-risk-v1` configuration is referenced by version/identity only, without copying limits or invoking Risk. CONSERVATIVE/GROWTH have no configured reference and return RISK_PREFERENCE_UNSUPPORTED; neither falls back to BALANCED. This reference is not authorization. DRAFT, PAUSED and RETIRED have separate blocking reasons. Unsupported environment/currency/market/direction/style, unavailable/disabled/non-spot registry records and missing policy fail closed. PAPER/USDT/BTC/USDT/LONG_ONLY remain fixed V1 boundaries.

Explicit server-owned technical mappings populate the typed policy contract. It must declare the exact preference combination and strategy family/version/configuration identity/timeframe; there is no approximate match or runtime strategy selection. Synthetic mapping-contract tests use a named TEST_ONLY hypothesis, never installed in production; v2 tests separately assert the actual approved configuration. Product policy must deliberately approve each mapping before it can make a production mandate ELIGIBLE.

Identities reuse existing canonical JSON/SHA-256 helpers. Decimal scale and equivalent UTC instants normalize; material mandate, registry and policy changes alter identity. Reasons/configurations have stable ordering. `resolved_at` is response metadata, deliberately excluded from semantic identity: repeated inspection of unchanged context does not create a different eligibility identity. Eligibility is a snapshot, not durable authorization or evidence of future freshness.

No persistence/migration or frontend was added. Eligibility never evaluates a strategy, creates a TradeIntent, calls Risk, authorizes execution, creates an order, changes positions or mutates Paper cash. An ELIGIBLE result means technical compatibility only; all downstream authorization remains mandatory.


### Approved policy v2: first controlled technical configuration

The current API uses `mandate-eligibility-v2`; v1 remains immutable and resolvable with its original empty mapping. The only v2 mapping is ACTIVE + PAPER + USDT + BTC/USDT + BALANCED + MEDIUM_TERM + LONG_ONLY. With an active canonical spot registry record, it returns ELIGIBLE.

Its server-owned technical reference identifies ADAPTIVE_TREND / 1.0.0, the unchanged canonical default configuration identity, timeframe `1h`, timeframe role SETUP_EVALUATION and position capabilities LONG/CASH. The canonical strategy identity is constructed with the existing StrategyDefinition and DEFAULT_CONFIGURATION, exactly as AdaptiveTrendStrategy does; no parameter values are duplicated or exposed. No strategy decision is calculated. The Risk reference is the unchanged `paper-risk-v1` identity/version. Resolver version remains 1.0.0 because the algorithm has not changed; the product mapping is versioned in policy v2.

MEDIUM_TERM -> 1h is an explicit product-policy selection of the setup/evaluation cadence, not a one-hour holding period or a one-candle exit. Positions may later remain open across multiple candles until legitimate strategy exit conditions occur. No 4h confirmation, 15m entry or 1d context is introduced.

SHORT_TERM, LONGER_TERM, CONSERVATIVE, GROWTH, unsupported environments/instruments/currencies/directions and all non-ACTIVE states remain INELIGIBLE. There are no inferred additional mappings or fallback selections. Capital allocation remains unchanged exact user-intent context: 500 USDT is never an order, quantity, reservation, target exposure or Risk-approved amount.

ELIGIBLE means only that this mandate may use this technical configuration. Inspection still performs no market-data fetch, strategy evaluation, TradeIntent creation, Risk evaluation/authorization, execution, order/position creation or balance mutation. No migration or frontend change is needed.


## Mandate strategy observation (mandate-strategy-v1)

`POST /api/mandates/{id}/evaluate` accepts an empty command (`{}` or no body), uses authenticated ownership and the existing exact trusted-Origin check, and returns no-store responses. Missing/foreign mandate IDs return 404; ineligible mandates or unavailable analytical context return controlled 409 errors; extra client fields return 422. The caller cannot choose a price, quantity, observation instant, position state or technical configuration.

The service first resolves mandate-eligibility-v2. Only its exact approved ACTIVE/PAPER/USDT/BTC/USDT/BALANCED/MEDIUM_TERM/LONG_ONLY configuration can proceed. Ineligible requests stop before book/market/strategy work. Capital remains exact Decimal mandate context; allocation does not feed the strategy or become quantity, exposure or reserved funds.

Adaptive Trend requires an explicit CASH/LONG thesis context. This first bridge reuses read-only PaperBook capture and proves CASH from a complete empty open-position inventory in the owner's active USDT Paper portfolio. Missing, mismatched or nonempty books fail closed: mandate-to-held-position attribution and exit orchestration are not introduced here. No balance or position is changed.

The bridge reuses the existing stored-only `risk.paper_source.market.read_observation` analytical adapter, without invoking any Risk evaluator. It selects the canonical active BTC/USDT SPOT instrument and exact BTC/USDT assets, BINANCE provider, and latest 100 completed 1h candles. Existing data validation, feature calculation, regime classification and default Adaptive Trend 1.0.0 evaluation run unchanged on one causal observation. There is no new indicator calculation, regime threshold, strategy parameter, multi-timeframe logic or automatic refresh. Completion is the next expected hourly boundary; the existing paper-market-validity-v1 permits evaluation strictly before ten minutes after that boundary. Missing, insufficient, stale, future-retrieved, malformed or inconsistent data and unsafe/unknown regimes fail closed. Refresh stored history through the existing explicit market-data operation before retrying.

The immutable STRATEGY_DECISION_ONLY result preserves the genuine canonical decision and reasons separately from the mandate outcome. ENTER_LONG remains a proposal only. Canonical NO_ACTION is exposed as NO_TRADE. ENTER_SHORT/EXIT_SHORT remain visible as underlying proposals but yield NO_TRADE with UNSUPPORTED_DIRECTION; they are never converted to long. A held-long exit cannot be authorized from this CASH context.

Compact provenance includes mandate/eligibility policy and identities, server-owned technical reference, authoritative book identity, market/feature/regime identities, latest candle and validity timestamps, strategy family/version/configuration/input/decision identities, regime evidence and capability reasons. Raw candle histories are not returned. Existing canonical hashing binds these semantic inputs; equivalent repeated observations preserve evaluation identity. The explicit UTC evaluated_at is response metadata rather than a new strategy observation identity. The result is an observation snapshot, never durable trading permission.

This endpoint does not create a TradeIntent, call Risk, create RiskDecision/authorization, call execution, create orders/positions, reserve allocation or mutate portfolio/accounting state. All downstream gates remain mandatory in future milestones. No new persistence, migration or frontend is required.


## Mandate entry Risk authorization (mandate-risk-v1)

`POST /api/mandates/{id}/risk-evaluate` is separate from strategy-only `/evaluate`. It accepts only an empty command, requires the authenticated owner and the existing exact trusted Origin, and returns no-store responses. No client quantity, price, cost, balance, indicator, strategy decision, Risk limit or approval is accepted. It returns a frozen owner-level summary; internal gate findings, raw snapshots and engineering evidence remain outside this response.

The transaction locks the owned mandate, resolves unchanged eligibility-v2 and runs the existing stored-evidence strategy bridge. NO_TRADE (including an unsupported SHORT proposal) succeeds without creating a TradeIntent, assembling Risk inputs or registering authorization. Only genuine capability-filtered ENTER_LONG proceeds. The existing first-trade PAPER source supplies the canonical ATR14 invalidation TradeIntent, actual locked untouched USDT Paper book/snapshot, paper-risk-v1 and every portfolio, drawdown, lifecycle, health and six-scope kill-switch prerequisite. The source re-reads its canonical observation; all market, intelligence, regime, configuration, decision, owner and book identities must reconcile with the mandate observation. There is no hidden refresh or fallback. This retains the source's bounded untouched-first-trade limitation.

The canonical TradeIntent entry context additionally binds mandate UUID, exact Decimal allocation/currency, eligibility policy/result, technical configuration and strategy observation identities. Reserved analytical and Paper policy fields cannot be overridden by supplemental provenance. Mandate allocation is a maximum modeled entry capital commitment, including entry fees, not an order amount or reservation. The new optional server-owned `TradeSizeConstraints.max_entry_cash_commitment` is passed to existing Risk sizing. Risk alone calculates quantity using the most restrictive existing loss, exposure, cash and quantity bounds plus this ceiling. For LONG, existing `SizeProjection.required_cash` equals modeled adverse entry notional plus modeled entry fee; spread/slippage are already in that price and are not counted twice. Future exit costs remain in existing potential-loss sizing but are not added to this entry budget. paper-costs-v1 and paper-risk-v1 values are unchanged.

The snapshot keeps the real portfolio cash/equity and exact book valuation identity. Only the computed sizing cash limit is bounded by allocation. A 500 USDT mandate therefore cannot authorize 500 USDT of modeled entry notional plus an additional fee. No quantity calculation is performed in mandate orchestration. The response's quantity and economics come from the evaluated trade-risk projection, with entry fee read from its existing unit-loss model.

All four existing Risk gates remain mandatory. NO_TRADE has no intent/Risk/authorization fields. REJECTED has the exact canonical intent and Risk decision summaries, four gate outcomes and typed blocking reasons, with no new authorization. APPROVED uses existing `evaluate_and_register` and the immutable Paper authorization registry. It includes intent/decision/configuration/registry/authorization identities, Risk-evaluated quantity, modeled entry economics and original approval expiry. No request, order, fill or consumption is created.

Same-observation retries use the existing registry and a deterministic proposal key binding mandate/eligibility/book/market/strategy context. Mandate and portfolio row locks serialize competing endpoint calls under the existing PostgreSQL transaction rules. A retry still assembles authoritative current evidence and checks all four gates: new blockers reject and missing evidence fails closed. If current gates pass, it returns the original stored approval instead of persisting another executable approval. It never renews expiry; `reused` and `expired` are explicit. An expired original fact remains expired and does not become execution permission through retrieval. Changed material mandate/evidence inputs describe a different proposal. Conflicting or unverifiable stored packages fail closed rather than being overwritten.

Authority initialization/attestation and approval persistence share the caller's transaction; failure rolls them back. There is no cash mutation, capital reservation, position/accounting write, execution call, approval consumption or LIVE path. No migration or frontend changes are required. The optional cap is omitted from canonical serialization when absent, preserving pre-existing non-mandate hashes and registry payloads; when present it is identity-bound and cannot be stripped without reconstruction failure. Source tests use isolated SQL doubles and real strategy/Risk/registry code; they do not establish physical PostgreSQL concurrency or durability proof.


## Explicit mandate PAPER entry (paper-run)

`POST /api/mandates/{id}/paper-run` accepts an empty command, requires the authenticated owner and existing exact trusted Origin, and returns no-store responses. It is an explicit entry action; saving or activating a mandate still never starts trading. No scheduler, polling loop or frontend control is introduced.

For a new entry it calls the existing mandate Risk boundary unchanged: ACTIVE/eligible mandate, stored canonical 1h evidence, genuine Adaptive Trend decision, canonical TradeIntent, four Risk gates, and durable authorization. NO_TRADE and unsupported SHORT stop successfully without execution. Risk rejection returns RISK_REJECTED without an order. Only APPROVED proceeds. The server retrieves and verifies the exact immutable authorization, including owner, PAPER/USDT/BTC context, technical configuration, mandate/eligibility/observation provenance, allocation constraint, quantity and paper-costs-v1. The client supplies none of those facts.

The wrapper derives the canonical ExecutionRequest identities from the registry package, then calls the existing production command source and Paper execution API/service adapter. An explicit injected UTC clock is sampled after Risk for execution-time expiry checking. Execution does not re-evaluate strategy or calculate a new permitted quantity. Its existing canonical candle-prefix validation, freshness, cost matching, book locking, consumption, order/fill persistence and LONG accounting remain authoritative. No execution engine or financial formula is duplicated.

Risk registration and Paper execution share one SQLAlchemy Session connection/outer transaction. Existing repository/service savepoints participate in that transaction; they do not independently commit. The outer handler commits only after a valid typed result exists. Any failure rolls back newly created authority/authorization/command facts and all economic changes together. Successful BUY accounting debits actual portfolio cash by modeled notional plus fee and opens the existing canonical LONG position. Allocation remains a fee-inclusive ceiling, never a separate wallet, cash reservation or instruction to use the whole allocation. Existing Risk may permit substantially less.

This is a bounded first-entry endpoint, not re-entry or position management. It checks immutable completed entry receipts for this owned mandate before requiring CASH or observing the market again. A complete prior entry is reconstructed through the existing production command/receipt recovery path, even after expiry, changed market conditions or a later pause/retirement. This returns the original historical fact (`reused=true`), not new permission or a current-position claim. Its capital context and executed_at are the originals, and its position may since have been closed. It never opens a second position or re-authorizes that entry. Multiple completed entries or incomplete/unknown command/consumption/order/fill/receipt state fail closed with paper_run_reconciliation_required; no blind resubmission or automatic repair occurs.

The frozen response distinguishes NO_TRADE, RISK_REJECTED and PAPER_EXECUTED. It exposes mandate, eligibility and strategy-observation/decision references, original capital context, Risk/authorization identities and approved quantity where applicable. PAPER_EXECUTED reuses the existing safe execution response for request/order/fill/position identities, exact executed quantity, price/notional/fee, cash_after and execution timestamp. Internal credentials and raw Risk engineering evidence are not returned. Position origin intent/decision and opening request/order/fill references link back to the immutable registry TradeIntent's mandate provenance; no mandate-specific position or accounting table exists.

No migration is needed. Eligibility, strategy algorithms, Risk gates/thresholds, cost assumptions, Paper accounting and the non-mandate execution API remain unchanged. There is no LIVE/Quidax venue path, SHORT, leverage, sub-wallet, withdrawal, automatic exit or mandate-aware position management. Existing genuine EXIT_LONG plus fresh Risk authorization remains the separate exit foundation. Tests exercise real engines/services with the established rollback-capable SQL ledger double; physical PostgreSQL locking, constraints and concurrent connections are not proven by that double.


## Explicit mandate PAPER position management

`POST /api/mandates/{id}/paper-run` is now position-aware. It remains an empty,
owner-authenticated, exact-Origin command with no-store responses. No scheduler,
provider refresh or client financial inputs are added. Without acquisition facts
it uses the unchanged entry pipeline. With complete acquisition facts it follows
the immutable registry mandate provenance through the opening request/order/fill
to the canonical Paper position; it cannot pyramid or create another entry.

For an ACTIVE eligible mandate with an OPEN LONG, the adapter reuses
`risk.paper_source.close.assemble_close`: fresh canonical completed BTC/USDT 1h
history, unchanged intelligence/regime/Adaptive Trend, explicit LONG thesis context.
`NO_ACTION` returns POSITION_HELD, without creating a close intent, authorization,
order, fill or accounting entry. Stale, missing or inconsistent evidence returns
a controlled 409. No hidden refresh or threshold adjustment is performed.

A genuine EXIT_LONG reuses the same four-gate close Risk evaluation and durable
registry as `/api/risk/paper-close-evaluations`, on the caller's transaction
connection. EXIT_RISK_REJECTED creates no close authorization or economic effect.
APPROVED supplies a new intent, decision and authorization bound to the canonical
position's full opening quantity. The consumed entry approval never permits exit.
Execution uses the existing ProductionPaperCommandSource and Paper service at a
fresh explicit execution instant. Expiry, book/evidence/cost/quantity checks,
single-use consumption, SELL fill and full-close accounting remain unchanged.

POSITION_CLOSED exposes the existing execution result: order/fill/position IDs,
quantity, fee, gross/net realized P&L and resulting global portfolio cash. The
position result additionally links the opening registry/intent/decision/eligibility
identities, exit strategy input/decision and UTC timestamps. Allocation is original
mandate context, not a separate wallet; it is not recalculated as close quantity.

The mandate row, portfolio and position are locked in the one outer transaction.
New close authorization, command, consumption, order/fill, cash/position updates
and receipt either commit together or roll back. Completed close recovery requires
complete canonical command/request/consumption/FILLED order/single fill/receipt and
closing-request linkage. It returns the stored close after expiry without another
strategy evaluation, authorization, cash credit or P&L realization. Ambiguous or
UNKNOWN facts return reconciliation-required; they never resubmit. Closed positions do not automatically re-enter. For a PAUSED/RETIRED mandate with an open
position, only the historical entry receipt is recovered; no new evaluation or
close is initiated. A completed close remains recoverable regardless of mandate
status. This preserves historical receipt recovery while making ACTIVE calls
manage fresh position evidence rather than replaying the entry event.

This extends the earlier first-entry-only paper-run scope. Strategy-only evaluate
and entry-only risk-evaluate retain their existing boundaries. No migration,
position table, new Risk/SELL/P&L engine, partial close, scale-in, SHORT, leverage,
LIVE, frontend or background automation is introduced. Source tests use real
services over a guarded SQL ledger double; physical PostgreSQL concurrency and
trigger verification are not claimed by those tests.
