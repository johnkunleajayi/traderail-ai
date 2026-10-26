# Execution Engine — Milestone 11A domain foundation

## Authority boundary

Strategy proposes a TradeIntent. Risk alone decides whether capital exposure is
permitted. Execution may later act only on a valid, exact-intent APPROVED
RiskDecision. Execution does not size a trade, override limits, interpret strategy
quality or rerun financial risk calculations.

11A defines immutable contracts under `app.execution`. It has no venue
implementation, order placement, fill generation or economic simulation. Creating
an Order, Fill or PositionLifecycleReference object records supplied contract
facts only; it does not cause an economic action or change a ledger. These types
are not wired into an API, service loop, database, strategy or portfolio.

## Approved authorization and request

ExecutionAuthorization binds an exact TradeIntent to the existing 10F
RiskEvaluationInspection: the original APPROVED RiskDecision plus all four exact
passing gate records. It checks the decision's integrated coverage/manifest and
record identities, intent/configuration/snapshot/evaluation context and the
approved quantity/risk linkage across the trade, portfolio and state records.
It does not evaluate whether the trade is safe or calculate any risk value.
Missing, rejected, legacy non-integrated, mismatched or spliced evidence fails
closed. Existing RiskDecision validation and 10A–10G files are unchanged.

The request quantity comes directly from the approved trade-risk projection.
There is no request quantity override, alternate sizing policy or actual fill
price. Request side is BUY for approved LONG new exposure and SELL for approved
SHORT new exposure; these are order-direction labels, not recommendations.
Exit/reduce/reversal intent semantics are not introduced by 11A.

ExecutionRequest requires an explicit authorization, canonical opaque venue
identity and timezone-aware creation instant. The creation instant must satisfy
`risk.evaluated_at <= created_at < risk.approval_expires_at`. The same pure
`authorization.validate_at(as_of)` check is available for a future submission
boundary. Construction at an earlier valid instant is not perpetual permission.
A future adapter must supply a trusted current instant, authenticate the source,
reconcile changed state and perform execution-time risk revalidation before any
side effect. Unknown context must never be repaired by Execution.

The existing LAB/PAPER/SHADOW/LIVE environment enum is reused as
ExecutionEnvironment. V1 request construction permits PAPER only. LAB historical
research simulation remains in Strategy Lab; SHADOW and LIVE request capability
are not enabled. PAPER SHORT vocabulary does not imply Quidax spot shorting,
borrowing, leverage or a live venue capability.

These in-process types assume trusted backend construction. Hashes identify
canonical data, not source authenticity or cryptographic authorization. A
client-supplied object, JSON approval or matching digest is not permission.
No current HTTP endpoint accepts these execution contracts.

## Identity chain and idempotency

The existing tagged canonical JSON and SHA-256 helpers are reused, with versioned
`execution.* /v1` namespaces. No alternative hash algorithm, random ID, Python
hash(), implicit timestamp or binary floating-point value is used.

- TradeIntent identity identifies one proposed new-exposure action.
- RiskDecision identity binds the exact evaluated risk context and expiry.
- action_identity derives from the exact intent identity.
- client_order_identity derives from the action identity, independent of retry
  time, approval renewal or venue selection.
- request_identity binds the full authorization, approved quantity through its
  gate evidence, explicit venue identity and original creation instant.
- order_identity derives from client_order_identity; observation identities bind
  evolving order facts without changing the stable economic order identity.
- fill_identity binds stable order identity and the venue/source's fill identity.
  observation_identity also binds exact supplied fill quantity, price and time.
- ApprovalConsumptionBinding has a consumption_key derived from RiskDecision
  identity and a binding identity linking it to the full execution request.

`validate_retry(original, retry)` accepts only the same canonical economic action
AND request. An altered creation time, venue, approval or approved quantity under
the same intent is an idempotency conflict, never an instruction for a second
trade. Renewing approval for the same intent does not produce a new client order
identity. A retry retains its original request fields; a genuinely different
strategy proposal requires a different TradeIntent.

11A models uniqueness, not a persistent guarantee: there is no consumed flag,
mutable in-memory deduplication table or consumed-approval database. Before any
future economic action, an atomic durable boundary must enforce uniqueness of
the economic/client identity and approval-consumption binding, reject conflicting
payloads, return existing results for exact retries and prevent duplicate action
across concurrent requests or process restarts. An expired approval cannot permit
a new submission; retrieving an already-recorded result is a separate future
operation and must not submit again.

## Order and execution lifecycle

OrderState is exactly CREATED, ACCEPTED, PARTIALLY_FILLED, FILLED, CANCELLED,
REJECTED and UNKNOWN. ExecutionState is derived, never independently mutable:
CREATED -> READY; ACCEPTED/PARTIALLY_FILLED -> IN_PROGRESS; FILLED -> COMPLETED;
CANCELLED -> CANCELLED; REJECTED -> FAILED; UNKNOWN -> UNKNOWN.

Allowed state changes:

| From | To |
|---|---|
| CREATED | ACCEPTED, CANCELLED, REJECTED, UNKNOWN |
| ACCEPTED | PARTIALLY_FILLED, FILLED, CANCELLED, UNKNOWN |
| PARTIALLY_FILLED | FILLED, CANCELLED, UNKNOWN |
| UNKNOWN | ACCEPTED, PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED |
| FILLED, CANCELLED, REJECTED | No different state |

Same-state observations/retries are allowed only with consistent facts. Partial
observations may accumulate fills. Observations cannot go backward in time,
change request/authorization, erase known acceptance/venue attribution, delete
fills, rewrite a fill's quantity/price/time, overfill or reopen terminal orders.
Terminal facts are fixed; later identical observations may advance observation
time. UNKNOWN represents uncertain delivery/status, not a fresh CREATED order.
It requires future authoritative reconciliation under the same client identity,
not blind resubmission. No cancellation or reconciliation service exists yet.

Acceptance is an explicit causally bounded fact: it must follow request creation,
precede the observation and occur while the approval is valid. Future adapters
must establish this authorization/acceptance fact correctly; it is not inferred
from a late response arrival. No approval-expiry cancellation behavior exists.
An already accepted order's supplied fill may occur after approval expiry; this
does not authorize a new order. Fill times must follow acceptance and not exceed
the order observation instant. Cumulative quantity uses existing exact Decimal
sum primitives only to validate consistency with the risk-approved quantity.

CREATED/REJECTED cannot contain acceptance or fills. ACCEPTED has known acceptance
and zero fills. PARTIALLY_FILLED has positive quantity strictly below approved
quantity. FILLED has exactly approved quantity. CANCELLED may retain a partial
fill but cannot disguise a fully filled order. Duplicate/conflicting fill IDs
are rejected. There is no fee, cost, execution-price or P&L calculation.

## Position lifecycle references

PositionLifecycleReference links opening request/order provenance to an OPEN or
CLOSED lifecycle reference, an explicit observation instant and optional declared
closing order identities. Position identity stays anchored to the opening client
order; the reference identity binds the supplied lifecycle facts. Closing IDs
are canonicalized, must be unique and cannot reference the opening order as its
own close. CLOSED requires explicit closing provenance; OPEN does not imply a
fabricated closure.

`validate_position_reference_transition` permits OPEN -> CLOSED and consistent
same-state observations, rejects backward time, changed opening provenance,
CLOSED -> OPEN and rewritten terminal closure references. These are pointers,
not proof of a funded/open position or filled exit. Trusted ledger/fill linkage
and close/reduce authorization must be implemented later. No portfolio position
is opened, closed, resized or persisted here.

## Venue abstraction and Paper versus Live

ExecutionVenue is a Protocol only: venue_identity, environment, future
`submit(request, as_of=...)` and `lookup(client_order_identity, as_of=...)`
contracts. No code invokes these methods and no concrete adapter implements
them. A future PaperExecutionVenue and later QuidaxExecutionVenue must conform
to the same authority/idempotency boundary. There is no Quidax-specific symbol,
fee, order type, precision, credential or private API behavior in 11A.

The protocol alone is not sufficient to enable execution. Durable idempotency,
source authentication, actual revalidation, environment/capability enforcement,
authoritative order observations and explicit ambiguity/recovery handling are
required first. Late cancellation/fill corrections, replacement orders and
venue-specific lifecycle extensions are intentionally deferred.

## Determinism, Decimal and UTC

All contracts are frozen dataclasses. Financial observations require finite
positive Decimal quantities/prices; floats, zero/negative values and non-finite
numbers are rejected. Wire inspection reuses existing exact Decimal-string and
UTC-Z serialization. No financial arithmetic occurs except exact fill-quantity
consistency checks; no financial values are invented, rounded or repriced.

Naive timestamps are rejected and equivalent aware instants normalize to UTC.
Equivalent Decimal representations, dictionary ordering, gate-record order,
fill observation order and closing-reference order retain canonical identities.
Material intent, approval, request, observation or lifecycle changes alter the
appropriate identity. Repeated identical calls have no hidden state. Tests cover
fresh Python processes under four hash seeds as well as deterministic invalid
inputs, exact quantity boundaries, expiry and lifecycle violations.

## Explicit 11A exclusions and 11B+ work

No API/frontend, provider/network/database reads, persistence, migration,
portfolio mutation, account access, credentials, actual order placement,
Paper/Live adapter, simulated fill, economic trade, costs/P&L, automated close,
Risk override, consumption write or strategy promotion is implemented.

11B+ must define trusted authoritative risk inputs and intent generation,
execution-time revalidation and state freshness, durable single-use/idempotent
coordination, Paper fill/cost conventions, trusted order/fill/position recording,
ledger/accounting integration and failure recovery before any economic simulation
or execution. No production/live defaults or execution authority are introduced
by this foundation.


## Milestone 11B — deterministic Paper fill calculations

`app.execution.paper_fill` adds calculation evidence only. It does not implement
PaperExecutionVenue, create an Order/Fill lifecycle event, consume an approval,
write a portfolio/ledger, persist a result or submit anything. All 11A contracts
and tests remain unchanged. No API, provider lookup, network/database access or
hidden wall clock is introduced.

### Authority and the entry/exit boundary

`calculate_paper_fill` takes the exact approved PAPER ExecutionRequest, explicit
PaperMarketEvidence, PaperFillAssumptions, action and explicit execution/evaluation
instants. Quantity is copied from the approved request, never supplied as a new
size, rounded, partially filled or resized to make a cost fit. Approval must be
valid at both execution and calculation time. This calculation does not renew
or consume it, rerun Risk or declare changed market/cost inputs safe.

11A TradeIntent approvals describe new exposure; there is no close/reduce request
contract. The request-bound calculation therefore supports ENTER_LONG/BUY and
ENTER_SHORT/SELL only. EXIT_LONG/SELL and EXIT_SHORT/BUY are tested and available
as PaperLegCalculation arithmetic with scope PRICING_ONLY_NO_AUTHORITY. That
arithmetic is not authorization, a request-bound fill, or a workaround for an
entry approval. A request-bound exit or opposite-direction action fails closed.
Explicit close-intent authority must be designed later rather than fabricated.

### Existing formula reuse

PaperLegCalculation delegates to the already-tested Strategy Lab
`execution.pricing.price_fill` primitive. It does not call the historical replay
or execution simulator. ResearchCostModel supplies explicit fee/full-spread/
slippage rates and delay; Risk's TradeRiskCosts validation additionally enforces
the same positive-adverse-price convention for both sides. No venue fee claim,
production rate preset or new execution-price formula is introduced.

For reference price P, approved base quantity Q, full spread fraction s, slippage
fraction l and fee fraction f:

- Per-unit spread displacement S = P * s * 0.5.
- Per-unit slippage displacement L = P * l.
- BUY (LONG entry or SHORT exit): modeled execution price X = P + S + L.
- SELL (LONG exit or SHORT entry): X = P - S - L.
- Reference notional = Q * P.
- Gross modeled notional = Q * X.
- Spread cost = Q * S; slippage cost = Q * L (unsigned cost magnitudes).
- Fee = gross modeled notional * f, charged separately from X.
- BUY per-leg net cash effect = -(gross modeled notional + fee).
- SELL per-leg net cash effect = gross modeled notional - fee.

Signed per-unit impacts, positive cost totals, reference/modeled prices,
reference/gross notionals, fee and net cash effect remain distinct in immutable
evidence. Spread/slippage are additive against the original reference, not
compounded; they are not charged again on top of an already adjusted price.
The shared primitive's legacy `reference_open` field carries the explicitly
supplied reference here; it does not claim a fetched candle open or imply an
intrabar price-selection rule.

All values use the existing exact Decimal finite sum/product helpers. There is
no binary float, display rounding, exchange tick-size assumption or currency
rounding. Costs require finite rates in [0, 1], and s * 0.5 + l < 1. Financial
prices/quantities must be finite and strictly positive. Arithmetic/temporal
failures raise controlled ValueError; no NaN, Infinity, fallback or fake fill is
returned. Undefined/unavailable evidence cannot become a zero observation.

Net cash effect is an analytic per-leg quote-currency flow, not a portfolio cash
balance, realized P&L, collateral release or permission to reuse short proceeds.
No borrowing, leverage or live short capability is inferred. Capital/position
accounting and any short reserve model remain separate later responsibilities.

### Trusted market evidence and timing

PaperMarketEvidence binds exact request identity, canonical instrument UUID/
symbol/provider/timeframe, PAPER environment, reference price, source identity,
price timestamp, observation timestamp, explicit validity boundary and a trusted
validity Boolean (unknown is None). Required health must be True. Digests record
provenance, not source authentication: a later TradeRail-owned evidence builder
must supply it; there is no caller-facing evidence injection endpoint.

Required chronology is priced_at <= observed_at <= executed_at <= evaluated_at,
with request creation <= executed_at. Naive timestamps fail; aware instants
normalize to UTC. Expiry equality rejects. Price age is measured from priced_at,
not recent retrieval/observation, and may not exceed the explicit max_market_age
at evaluation (maximum-age equality is accepted). Thus a newly observed stale
quote cannot be refreshed by changing its retrieval timestamp. Instrument,
environment or request mismatch, missing validity, future observations, stale or
expired market evidence all fail closed.

Delay is the existing explicit nonnegative duration in ResearchCostModel.
Eligibility is request.created_at + execution_delay; executed_at must not
precede it. No reference price is fetched or selected to satisfy delay. Zero
delay adds no snapshot wait. This is not a change to Strategy Lab's separate
NEXT_COMPLETED_CANDLE_OPEN historical replay convention: no backtest is run here,
and the caller must supply the trusted reference for the declared instant.
A delay extending beyond approval validity cannot create a modeled fill under
that approval. No interpolation or future-data selection is performed.

### Results, identity and independent examples

PaperFillCalculation is immutable and binds the complete request/intent/risk
provenance, instrument/environment, action/direction, approved quantity, market
evidence and its identity, all cost/freshness/delay assumptions, eligibility,
explicit execution/evaluation instants and calculated pricing/cashflow result.
PaperLegCalculation has a separate pricing-only identity. Existing execution
canonical SHA-256 conventions normalize equivalent Decimals and UTC instants.
Meaningful input/result changes alter identity. Four fresh-process/hash-seed
tests also reconstruct reordered upstream risk gate evidence deterministically.
No request-time randomness or mutable process state is used.

Literal synthetic oracles use P=100, Q=2, full spread=0.02, slippage=0.01 and
fee=0.01 (research assumptions only): spread and slippage are each 1 per unit.
BUY gives X=102, gross=204, fee=2.04 and net cash=-206.04. SELL gives X=98,
gross=196, fee=1.96 and net cash=194.04. Total spread and slippage are each 2,
already reflected in gross notional. Zero-cost and tiny/high-precision examples
are tested separately; no thresholds are tuned against market observations.

### Explicit limitations and 11C+ prerequisites

This is a deterministic full-quantity model, not a liquidity promise. No depth,
order book, available volume, partial fills, market impact, gaps or guaranteed
stop price is invented. The result is calculation evidence, not a simulated
ledger transaction or a committed economic action.

There is no persistence, approval consumption, order submission, actual Paper
adapter, portfolio mutation, API/frontend, Quidax behavior, credentials or Live
behavior. All four leg-price formulas exist; authorized exit-request construction
remains intentionally unavailable until close/reduce authority is modeled.

Before any 11C+ economic application, authoritative market/risk evidence and
source authentication, execution-time risk revalidation for changed prices/costs/
portfolio state, durable atomic idempotency/consumption, explicit close authority,
ledger/accounting integration and recovery must be implemented. A calculated
price or net cash effect is never a replacement for those controls.


## Milestone 11C — persistence and atomic approval consumption

This is a storage boundary, not Paper Execution. It accepts an existing validated
PAPER `ExecutionRequest`, explicit consumption time, a validated chronological
11A order-observation path, and already-calculated 11B fill audits. It never calls
a pricing engine, venue, market provider, portfolio service or execution API.
Synthetic test fixtures supply observed artifacts; there is no application path
that generates an economic action through this repository.

### Tables and identities

- `execution_requests`: immutable canonical request and full exact-string audit
  evidence; intent, approved RiskDecision, economic action, client-order and
  order identities; instrument/provider/timeframe, approved quantity, PAPER
  environment, and the user/portfolio UUIDs already present in the approved intent.
- `execution_approval_consumptions`: one durable binding per RiskDecision, request
  and economic action, with explicit consumed-at and approval expiry. Composite
  foreign keys reconcile request/decision/intent/action/environment/ownership.
- `execution_orders`: request-bound order identity and client identity, current
  lifecycle state and observation, acceptance/venue attribution and audit payload.
  Instrument, side/action and quantity are preserved through the exact request
  foreign-key relationship and the full order evidence, not independently resized.
- `execution_fills`: append-only 11A fill identity plus its request-bound 11B
  calculation and market-evidence identities; reference/modeled prices, quantity,
  signed per-unit spread/slippage impacts, positive cost totals, reference/gross
  notionals, separate fee, net cash effect, and explicit fill time. Complete 11B
  assumptions, fee rate, delay and audit context remain in exact-string JSONB.

The repository does not invent or verify ownership from HTTP claims. It carries
only the exact scope of the upstream trusted Risk authorization. These UUIDs are
provenance bindings, not newly minted account/portfolio ownership or authorization
rules. No foreign key to a newly invented RiskDecision/TradeIntent table exists.
Those domain contracts still have no persistence of their own.

### Atomicity, idempotency and concurrency

`persist_artifacts(connection, artifacts)` requires a caller-owned PostgreSQL
READ COMMITTED transaction with verified driver autocommit disabled. Unknown or
enabled autocommit rejects. It opens a savepoint, writes request, approval binding,
order and supplied fill facts, and releases the savepoint only if all reconcile.
It never commits the outer transaction. The returned receipt is provisional until
the caller commits; it is not proof of durable commit or venue execution. A later
caller rollback removes everything;
a caught conflict rolls back the whole savepoint without leaving partial artifacts.
Future 11D can place this participant inside its broader transaction boundary.

Unique indexes/constraints protect request/action/client/order identities, the
single-use decision binding, and fill/venue-fill/calculation identities. Inserts
use PostgreSQL `ON CONFLICT DO NOTHING`, then compare the winner's canonical
payload. An exact retry resolves to the same immutable receipt and cannot create a
second economic action. A changed payload, renewal timestamp or another action
under a consumed approval conflicts. It is not converted to a new request.

PostgreSQL's unique-index conflict arbitration supplies the durable concurrency
boundary. A following READ COMMITTED statement sees the committed winner. Order
rows use `SELECT ... FOR UPDATE` to serialize lifecycle changes. No process-local
mutex substitutes for database uniqueness. Stronger isolation is explicitly
unsupported by this participant and must not be silently retried inside it.
Connection/storage exceptions are sanitized; constraint errors never grant an
approval. No transient retry service or background worker is introduced.

### Immutable versus mutable facts

Request, consumption and fill records reject UPDATE and DELETE through migration
triggers. All PostgreSQL financial columns use unconstrained NUMERIC(asdecimal=True)
without precision/scale coercion; timestamps are timezone-aware UTC in the domain
and TIMESTAMPTZ in PostgreSQL. JSON financial values remain exact strings.

Only the current order's lifecycle observation can advance. The caller supplies a
complete valid path starting at CREATED that contains the exact persisted
observation. Existing 11A transition validation prevents rewinds, disappearing or
rewritten fills, changed acceptance and attribution, and terminal reopening.
SQL guards additionally reject deleted orders, rewritten identity/attribution,
nonchronological observations and forbidden state transitions. A later timestamp
may re-observe unchanged terminal facts. Lifecycle updates do not rewrite immutable
fill facts. This is a current-state row, not a separate historical event journal.

11C stores only full request-bound 11B fill calculations. It adds no partial-fill
simulation or exit authorization. SELL cashflow is audit evidence only, never
short collateral, capital availability, borrowing or spending permission.

### Verification and operational boundary

Migration `0007_execution_artifacts` follows `0006_market_candles`. Do not apply it
to Neon as part of this milestone. The accepted live baseline remains 526 candles.
The existing pytest guard still forbids every physical database connection; it
has not been weakened or given a URL-based opt-in. The repository currently has
no supported isolated PostgreSQL test infrastructure. Tests therefore compile
PostgreSQL upgrade/downgrade SQL offline, verify schema/constraint parity, and
exercise rollback, idempotency and controlled conflict responses using a recording
transaction double. They do **not** establish a physical PostgreSQL round-trip,
real concurrent unique-index wait, trigger execution, or migration round-trip.
Those require an explicitly approved, verified isolated PostgreSQL environment
before 11D operational use. No SQLite substitute or new infrastructure is added.

### Explicit exclusions

No PaperExecutionVenue, real or simulated economic orchestration, provider calls,
API, frontend, portfolio/position/P&L mutation, live request workflow, approval
execution-time revalidation service, background worker, or approval-to-order
submission exists. Durable consumption here is a stored reservation for one exact
action; it is not proof that an order was sent or a trade occurred. Atomic 11D
orchestration, authoritative runtime evidence, recovery/reconciliation workflows
and operational rollout remain separate work.


## Milestone 11D — transactional PAPER LONG/CASH accounting

11D is the first internal Paper economic simulation. It does not submit exchange
orders. `execute_paper(connection, command, as_of=...)` is a server-side transaction
participant, not an HTTP endpoint, venue adapter, scheduler or browser authority.
11C's isolated physical PostgreSQL upgrade/downgrade, concurrency and round-trip
checks were completed before this milestone; its earlier verification caveat is
historical, not a description of the current physical verification status.

### Authority and minimal contract extensions

Existing entry intents and their canonical identities retain their original
semantics. A full LONG close must be declared **before Risk evaluation**, inside
the exact immutable TradeIntent entry context as `paper_action: EXIT_LONG` plus
`paper_position_identity`. It therefore changes the intent and approval identity.
An entry approval cannot be relabelled as an exit at the execution boundary.
Unknown/malformed markers reject. The request exposes the bound action and side;
11B still owns all pricing formulas, and 11C persists the same audit chain.

Closes still require a valid four-gate APPROVED RiskDecision and the exact approved
quantity. Existing Risk gates are deliberately unchanged: they conservatively
apply their current new-exposure eligibility/sizing rules even to this explicitly
attributed close. There is no reduction-only, suspended-state or emergency-exit
bypass. A dedicated risk-reducing approval policy remains future work.

### Authoritative state and revalidation

`capture_book` derives a deterministic book identity from the existing database
portfolio, exact cash, open position rows and immutable accounting receipt IDs.
A trusted server Risk snapshot must use that identity as its `valuation_identity`.
The service recomputes it under the portfolio lock and reconciles scope, cash,
position IDs/instruments/quantities/strategy attribution, timestamps, marked equity
and required known position evidence. Callers cannot supply replacement cash or
quantity to the service. Market evidence must bind the exact request and approved
data source; its price must equal the Risk sizing reference. 11B checks freshness,
causality, validity, delay and expiry at the explicit execution instant. Assumed
fees/spread/slippage must still match the Risk-approved cost convention.

Only an active authoritative user, active PAPER portfolio, active canonical SPOT
instrument and active assets are eligible. Quote currency must equal the actual
portfolio currency: currently USD. There is no USD/USDT parity or implicit FX.
Consequently current USDT/NGN stored markets cannot economically execute against
a USD portfolio. Isolated tests may seed a disposable USD reference instrument;
no new canonical market or historical data is added to the normal database.

### Atomicity, locking and retries

The caller must own a non-autocommit PostgreSQL READ COMMITTED transaction.
The service uses a savepoint covering revalidation, 11C consumption/request/order/
fill writes, cash mutation, position mutation and the immutable economic receipt.
It does not commit. Returned results remain provisional until the caller commits.

A shared user lock then an exclusive portfolio row lock stabilize ownership and
cash; open position rows and used instrument/asset references are also locked.
The portfolio lock serializes competing proposals from the same prior book. A
second proposal sees the committed book change and must obtain fresh Risk evidence;
it cannot spend against the earlier snapshot. Existing database uniqueness and
11C row locks remain authoritative, with no in-process mutex as a substitute.

Exact retries reconstruct the stored immutable result before re-pricing or making
any economic mutation, even if the old approval/evidence has since expired. This
is a read of an already-authorized economic fact, not renewed permission. Changed
command, costs, market evidence or execution time under one action conflicts.
A consumed 11C reservation without a corresponding economic receipt fails closed;
it is not silently adopted. An ambiguous commit is resolved by exact retry. Errors
roll back the complete savepoint; no automatic resizing or portfolio reset occurs.

### LONG accounting and positions

For approved quantity Q, 11B modeled price X, and separate fee F:

- BUY cash change = `-(Q * X + F)`.
- SELL cash change = `Q * X - F`.
- New cash = exact prior cash + that 11B cash effect; negative cash rejects.

A BUY opens one LONG position with its exact original quantity, modeled average
entry price, reference notional, modeled cost basis, entry fee/spread/slippage,
strategy attribution, intent/approval/request/order/fill references and UTC time.
There is at most one OPEN position per portfolio/instrument. Scale-in and partial
closes reject. A SELL requires the exact targeted OPEN position, same strategy and
full held quantity; overselling never converts into SHORT. Full close sets quantity
to zero and status CLOSED, preserving all original acquisition facts and recording
the closing request/time. CLOSED positions cannot reopen or have origin rewritten.

For a full close (all entry costs attributable to the closed quantity):

- Entry cost basis = modeled BUY notional, excluding its separately recorded fee.
- Gross realized P&L = exit reference notional - entry reference notional.
- Net realized P&L = exit modeled notional - entry cost basis - entry fee - exit fee.
- Equivalently net = gross - both legs' spread/slippage costs - both fees.

Entry costs already embedded in modeled prices are not subtracted twice. The close
cash movement subtracts only its own fee; entry cash was debited once on opening.
Example hypothesis, Q=1, reference 100 then 120, full spread .02, slippage .01 and
fee .01: BUY price 102, fee 1.02, cash 9896.98 from 10000; SELL price 117.6, fee
1.176, final cash 10013.404, gross realized 20 and net realized 13.404.
No continuously changing unrealized P&L or performance aggregates are persisted.

### Persistence and precision

Migration `0008_paper_accounting` adds `paper_positions` and immutable
`paper_accounting_entries`. Cash becomes unconstrained NUMERIC to avoid losing
sub-cent 11B costs; starting capital remains exactly 10000.00 USD with its existing
creation/default semantics. No balance is reset or credited by the migration.
Existing human-facing portfolio serialization still formats two decimals; it is
not used for economic calculations. Financial domain and stored receipt values
remain exact Decimal strings/NUMERIC, with explicit aware UTC times.

Only current position closure fields are mutable; SQL guards preserve its origin
and disallow scale-in/reopening/deletion. Accounting receipts reject UPDATE/DELETE.
0007's existing facts remain immutable. Its new-request action constraint gains
only the explicit LONG SELL/EXIT_LONG combination. The downgrade refuses values
that would require cash rounding or removal of persisted EXIT_LONG authorization
facts, rather than silently coercing them into the older schema.

### Explicit limitations

PAPER only, internal venue attribution only, LONG/CASH only, full closes only, one
open instrument position, exact quote-currency accounting only. SHORT domain and
four-leg arithmetic remain available, but SHORT economic orchestration rejects
until collateral/margin accounting is explicitly designed. No live venue/order/
provider calls, API, frontend, background worker, leverage, performance engine,
automatic funding, emergency liquidation, or autonomous execution is introduced.
Fresh authoritative analytical/risk inputs must come from trusted server builders;
no public runtime builder or HTTP financial-state acceptance is created here.


### 11D final-review safeguards

0008 downgrade refuses any persisted Paper position or accounting receipt, whether
OPEN, CLOSED, or entry-only, before dropping tables. Existing fractional-cent cash
and EXIT_LONG guards remain; economic history is never deleted or transformed to
permit downgrade. An empty compatible schema may still downgrade.

OPEN requires positive quantity and no closure fields. CLOSED requires zero
quantity, a non-null closing timestamp not before opening, and a non-null closing
request. The database CHECK rejects missing timestamps explicitly rather than
allowing SQL UNKNOWN. The existing transition trigger and immutable-origin guard
remain in force.

Editing 0008 does not update databases where it is already applied. Such databases
require a separately authorized forward corrective migration for the lifecycle
CHECK; no forward migration is introduced by this correction.

## Milestone 11E — authenticated Paper execution API

`POST /api/paper/executions` accepts exactly `intent_identity`,
`decision_identity`, and `request_identity` (canonical SHA-256 identities).
Unknown fields are rejected. Clients cannot submit ownership, portfolio cash,
approved quantity, positions, prices, costs, approvals, or execution timestamps.
Existing cookie authentication is required (401 anonymously); the exact configured
frontend Origin is required (403 otherwise). Responses, including errors, use
`no-store` and `no-referrer`.

The server-only `TrustedPaperCommandSource` must resolve the exact immutable
approved command for the authenticated user. All three identities and ownership
are checked before opening an economic transaction; a mismatch is 404. There is
no HTTP registration or injection route for trusted evidence. No Risk decision
is constructed or approved by the API. New execution timestamps come from the
explicit boundary clock; retries must resolve the original command and original
timestamp so 11D can reconstruct the committed fact even after approval expiry.

**Current authoritative-source limitation:** like the existing Risk inspection
boundary, the default resolver is deliberately unavailable. There is no durable
intent/approval registry or production evidence builder yet. Therefore normal
requests return 503 `trusted_paper_evidence_unavailable` without opening an
economic transaction. This is not a fallback success path. A trusted server
resolver must be explicitly implemented/wired before end-to-end operational API
verification; HTTP clients cannot enable it. Tests inject trusted server fixtures.

When a trusted source is supplied, the adapter invokes only existing
`execute_paper` inside one caller-owned PostgreSQL transaction. The transaction
commits before a success response is returned; any domain, presentation, or
commit failure prevents success. Existing 11D locks, revalidation, risk-approved
quantity, approval consumption, immutable receipts, currency rules, full-close
restriction and exact-retry behavior remain unchanged. SHORT is rejected before
economic orchestration; the API enables no LIVE or venue behavior.

The concise response includes COMPLETED/PAPER, result/intent/decision/request/
order/fill identities, position UUID and historical OPENED/CLOSED effect, action,
quantity, modeled fill price, gross notional, fee, currency, resulting cash,
realized gross/net P&L where present, and original execution UTC time. Financial
values are exact strings, absent realized outcomes are null. Position effect and
cash describe that committed operation, not a live current-position/balance query;
an exact retry returns the same original result.

Malformed bodies are 422. Unsupported actions, expired authorization, invalid
evidence, insufficient cash, idempotency conflicts and other domain rejections
are controlled 409 responses with stable codes. Storage/commit failures and
unavailable authoritative evidence are sanitized 503 responses; ambiguous storage
outcomes require reconciliation with the same request, never a new economic ID.
No SQL, internal error details or credentials are reflected to clients.

API tests use isolated server fixtures and a transaction/economic-service double
to verify transport, delegated command identity, error/rollback propagation,
entry/full-close response oracles and retry forwarding. They do not replace the
separately verified real PostgreSQL 11D concurrency/atomicity checks. The ordinary
test database guard remains unchanged. No migration, persistence redesign,
provider call, frontend, scheduler, or real venue execution is added in 11E.

## Milestone 11E.2 — durable pre-execution authorization registry

Migration `0010_paper_authorizations` adds two server-only immutable tables. It
must be verified on the isolated integration database before any separately
authorized normal-database migration. No route accepts registry payloads.

`paper_authorizations` stores the complete `AuthorizationPackage`: the original
immutable TradeIntent, full APPROVED RiskDecision, all four passing GateRecords,
original RiskEvaluationInputs (including policies, snapshots and safety evidence),
and explicit execution-evidence references. Indexed/constraint-bound metadata
contains ownership, instrument, quantity, identities and approval times. Created
time is the original evaluation instant; insertion/retrieval never reissues an
approval or introduces a wall-clock identity. Only PAPER/APPROVED records are
accepted. A composite FK binds portfolio and user to the actual Paper portfolio;
the additive `(id,user_id)` unique key changes no portfolio economics.

`ExecutionEvidenceReferences` binds the approved data source, market context,
cost configuration and portfolio valuation identities. It does not invent a fresh
price, fees or validity. A future server resolver must retrieve those authoritative
sources and revalidate freshness/current book; changed incompatible state requires
fresh Risk evaluation. Historical references alone are not permission to execute.

`paper_authorized_commands` optionally binds the exact original PaperCommand to
an existing registered authorization, request/action and owner. First registration
requires unexpired approval and explicit current command time, matching approved
price/cost/source references, and existing 11B market/time validation. It performs
no economic action and consumes no approval. Changed payload under the same
request/action conflicts. Recovery returns the original validated command after
expiry for 11D's exact-retry reconciliation; that does not permit a first execution
after expiry or extend any authorization. Existing 11C consumption remains the
single-use authority. There is no registry-side automatic retry/execution.

The versioned tagged JSON codec preserves nested Decimal types and representations,
UTC instants, UUIDs, enum keys, mappings and immutable domain records. Recovery
uses a fixed allowlist (no pickle, eval or payload-controlled imports), runs original
constructors, verifies every derived field/identity and matches canonical payload
and metadata. The package also verifies the integrated report's original inputs
identity. Equivalent canonical inputs preserve identity; the first stored exact
representation is retained on an equivalent retry. Digest integrity is not source
authentication: only trusted server code may write this repository.

`risk.authorization_service.evaluate_and_register` is the server integration point:
it evaluates unchanged 10F once, persists only an APPROVED full package, and returns
REJECTED without creating a record. The caller owns the PostgreSQL READ COMMITTED,
non-autocommit transaction; repository savepoints prevent partial records on
failure. Neither repository commits. Exact persistence retries are idempotent;
unique constraints arbitrate concurrent conflicts. UPDATE/DELETE triggers reject
mutation of both tables. Downgrade refuses any persisted registry history.

The existing Risk inspection HTTP route remains inspection-only. Its production
trusted Risk source is still unavailable. This milestone supplies the durable
boundary, not production policy/health/kill-switch or market evidence builders,
an intent-generation workflow, or an operational Paper resolver. Consequently
11E's default HTTP resolver continues to return controlled 503. No approval is
manufactured from request identities, balances, candles or client financial data.


## Bounded production PAPER authorization, execution and full close

The earlier 11E/11E.2 unavailable-resolver descriptions above describe those
milestone boundaries. ProductionPaperCommandSource now resolves the existing
POST /api/paper/executions identity-only command inside the same transaction as
11D's economic service. No additional migration is required. Only owned USDT
PAPER portfolios, BTC/USDT canonical Binance 1H evidence and LONG/CASH are supported.
Execution does not evaluate strategy or calculate a new permitted quantity.

POST /api/risk/paper-evaluations accepts {} under existing owner/operator and
trusted-Origin protection. A genuine Adaptive Trend ENTER_LONG and all four Risk
gates are required for APPROVED. NO_TRADE and REJECTED remain valid outcomes.
Only APPROVED registers an authorization; the response includes deterministic
request_identity as well as intent_identity and decision_identity. Submit those
three IDs to POST /api/paper/executions. Financial HTTP fields remain forbidden.

The server reconstructs the immutable authorization, locks/captures the portfolio,
checks its exact approved PaperBook state identity, and re-reads the exact approved
canonical candle prefix. A newer observation cannot silently replace that prefix.
Approved quantity, price, source and server-owned paper-costs-v1 must reconcile;
approval and market evidence must remain valid. The existing adverse fill model
and atomic accounting service perform all economics. Command registration,
approval consumption, order/fill, cash, position and receipt commit together.

Exact successful retries recover the original command/receipt, even after expiry;
they never authorize another action. A duplicate waiting for the portfolio lock
re-reads the winning command after that lock is acquired. Conflicting identities,
UNKNOWN state or consumption without a reconstructable receipt fail closed.
PostgreSQL constraints/locks, not process memory, are the concurrency authority.

### Fresh exit Risk authorization

POST /api/risk/paper-close-evaluations accepts only position_id and uses the same
server-enforced operator permission, exact Origin and no-store policy. This
Risk-side adapter evaluates Adaptive Trend in LONG state. Only a genuine EXIT_LONG
creates a fresh exit TradeIntent, four-gate Risk report and authorization. The
consumed entry approval is acquisition provenance only, never permission to exit.
The full held quantity is proposed explicitly and cannot silently be resized.
Submit the new approved exit's three identities to the same execution endpoint.

Only the first single position is supported. Immutable initialization/acquisition
facts must reconcile with the current book and complete completed-candle history
since entry within the existing 100-candle prefix. The adapter reconstructs observed
equity peaks and prior risk state with the existing hysteresis policy; it invents
no intrabar peak. Missing history, additional economic activity, an incompatible
original invalidation boundary or missing evidence fails closed. All four unchanged
Risk gates still apply, including conservative exposure checks; a close may be
rejected. There is no emergency-liquidation bypass or strategy threshold change.

BUY cash change = -(modeled notional + fee). SELL cash change = modeled notional
- fee. Gross realized P&L = reference exit notional - acquisition reference notional.
Net realized P&L = modeled exit notional - acquisition modeled cost basis - entry
fee - exit fee. Spread/slippage are already in prices and are not charged twice.
The original position closes with acquisition provenance; retries cannot duplicate
cash, fills, position changes or realized P&L.

Synthetic oracle (quantity 1): reference prices 178 and 193; modeled prices 178.178
and 192.807; fees 0.178178 and 0.192807. Cash is
10000 -> 9821.643822 -> 10014.258015. Gross realized P&L = 15 and net = 14.258015.
This fixture does not change production evidence, policy or strategy thresholds.
Tests run real services against an isolated rollback-capable SQL ledger double;
that is not physical PostgreSQL concurrency/constraint/crash-recovery proof.

Physical verification of migrations 0011/0012 and this wiring remains DEFERRED due
to the runtime PostgreSQL connectivity limitation. No normal DB is connected to
or migrated here. Verify on an isolated branch before separately authorized normal
deployment and manual API testing. No new migration, provider call, LIVE, SHORT
accounting, partial exit, scale-in, scheduler, frontend or performance engine.


## Mandate-driven first PAPER entry

The explicit authenticated `/api/mandates/{id}/paper-run` boundary composes the unchanged mandate Risk service with `ProductionPaperCommandSource` and `run_paper_execution` on the caller's single transaction connection. Only a genuine approved LONG entry with an exact durable registry package can proceed. The canonical request uses the approval's original timestamp/identities; execution-time expiry is checked with a fresh explicit UTC instant. The existing service consumes the approval once, fills exactly its approved quantity using paper-costs-v1, and persists the normal order/fill/position/accounting records. No sizing, price invention, cash reservation or second wallet exists in this layer.

Mandate provenance remains in the immutable TradeIntent registry payload and is reachable through existing position/order/fill origin references. Receipt recovery precedes any fresh strategy/Risk evaluation: successful retries reconstruct the original event even when approval/market evidence has since expired or the mandate is paused. The recovery path requires complete command, request, consumption, FILLED order, one fill and accounting evidence; incomplete/unknown or ambiguous facts require reconciliation and never trigger blind resubmission. A recovered OPENED result describes the historical opening event, not a claim that the position is currently open. No automatic re-entry or exit is added.

The API transaction includes any newly registered Risk approval plus command and economic persistence, and commits only after result construction. Failure rolls back the whole operation. Source tests use the existing SQL ledger double and genuine deterministic analytical fixtures, not a physical PostgreSQL connection. The original non-mandate execution path and all financial formulas/limits are unchanged.


## Mandate position management and exit

The explicit mandate paper-run adapter now manages its acquired canonical LONG
position. It reuses the existing position-aware `assemble_close` analysis and
four-gate registered Risk response, then the unchanged production command source
and Paper close service. HOLD has no economic effects; rejected exit Risk cannot
execute. A genuine approved EXIT_LONG uses a fresh authorization for the exact
held quantity, never the consumed entry approval. Acquisition provenance is
preserved through the opening registry, intent, decision, request/order/fill and
canonical position; exit provenance links that same position and opening registry.

Accounting is unchanged: BUY debits modeled entry notional plus fee; SELL credits
modeled exit notional less fee. Gross realized P&L uses reference notional change;
net uses modeled exit notional less entry modeled cost basis and both fees.
Spread/slippage remain in modeled prices and are not charged again. Mandate
allocation remains context; cash belongs to the authoritative USDT portfolio.

One caller-owned transaction and existing database row locks cover close Risk
registration and all execution/accounting effects. Closed-receipt recovery uses
original command identities/instant and never evaluates a new strategy or approval.
Incomplete/UNKNOWN artifacts require reconciliation; a completed close never
causes automatic re-entry. See trading-mandates.md for position result states,
paused/retired historical recovery and explicit freshness behavior. No financial
formula, Risk threshold, cost policy, position schema or non-mandate close behavior
is changed. SQL ledger tests are source/service verification, not physical
PostgreSQL concurrency proof; no database connection is made for this milestone.
