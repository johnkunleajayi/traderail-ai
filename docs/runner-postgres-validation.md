# Autonomous PAPER runner: PostgreSQL verification (Step 6A)

Prepared only. No branch creation, Neon connection, migration, fixture worker or
continuous runner was executed. Product behavior is unchanged.

## Prerequisites / target approval

- Backend virtual environment plus requirements-dev. The harness reuses the
  existing pure deterministic Adaptive Trend audit dataset.
- Separate authorization to create a NEW disposable Neon child of development.
  Record child/parent IDs from Console; confirm different IDs and that the DIRECT
  endpoint copied into ignored `backend/.env.integration` belongs to that child.
- SQL cannot prove Neon branch ancestry. Human Console verification is required;
  a hash only binds this approved target to subsequent commands.
- TLS (`sslmode=require`/`verify-full`), psycopg, READ COMMITTED/non-autocommit,
  owner permission to CREATE schemas/functions and inspect its blocking sessions.
- Public child starts at **0013_trading_mandates**, work table absent. This is a
  prerequisite, not a verified fact about today's unconnected database. Stop on
  another revision; never reset shared history to satisfy the procedure.
- Runtime requires **0015_paper_work_code_checks** exactly. Its identifier fits the
  32-character Alembic version column. The harness does not invoke env.py, whose
  normal settings loader could fall back to backend/.env.
- Keep `TRADERAIL_PAPER_RUNNER_ENABLED=false`. Do not launch the continuous runner
  or point the web application at this disposable branch. No other writers.

## Complete dependency inventory

Catalog validation checks model column sets/types/nullability, PKs, CHECK names,
unique column groups, FK targets/delete actions and indexes for users, assets,
instruments, trading_mandates, market_candles, paper_portfolios,
paper_authorizations, paper_authorized_commands, execution_requests,
execution_approval_consumptions, execution_orders, execution_fills,
paper_positions, paper_accounting_entries, paper_kill_switch_states,
paper_risk_initializations, paper_strategy_activations and paper_mandate_work.
Actual CHECK, column, index, trigger and guard-function definitions are saved for review; CHECK names alone do not establish
expression semantics. Compare the catalog expressions to source and negative
case results. Unknown differences block promotion, rather than being repaired.

| PostgreSQL dependency | Required invariant |
| --- | --- |
| Work PK, uq_paper_work_mandate_candle | One mandate/canonical candle even with a different work UUID/runner version |
| Five work FKs | RESTRICT mandate, candle, authorization registry, execution request and position references |
| ck_paper_work_state | Only the five defined states |
| ck_paper_work_attempts | Nonnegative attempts; exact last-attempt nullability |
| ck_paper_work_times | Ordered UTC-capable timestamps; original freshness window |
| ck_paper_work_retry | RETRY_WAIT due time required inside freshness; other states have none |
| ck_paper_work_completed_attempt / outcome | Completion requires an attempt; terminal states require an outcome |
| ck_paper_work_version / outcome_code / reason_code | Safe bounded machine codes |
| ix_paper_work_due | Index on state/retry_at/fresh_until exists; no particular query plan is assumed |
| paper_work_guard | Matching completed BINANCE 1h PAPER candle, max ten-minute validity; PENDING insertion; immutable key/version/deadline; monotonic due attempts; terminal UPDATE/DELETE forbidden |
| 0014 downgrade | ACCESS EXCLUSIVE work lock and refusal on ANY persisted work before DROP; no economic history removal |
| Mandate checks + trading_mandate_guard | Exact PAPER/USDT intent, positive Decimal allocation, ownership/lifecycle immutability |
| Candle unique/checks | Instrument/provider/timeframe/open identity; aligned completed intervals; valid finite OHLCV |
| Registry PK/UQs/composite FKs + immutable_authorization | APPROVED/PAPER/exact quantity/expiry; portfolio-owner and command binding; no mutable approval |
| Execution PK/UQs/composite FKs + immutable_audit/consumption_guard/order_guard | Unique request/action/client-order/order/fill, single-use decision/action, expiry and immutable canonical artifacts |
| Position/accounting constraints + guards | Unique provenance/receipts, one OPEN position per portfolio/instrument, full-close-only lifecycle including closed_at, exact NUMERIC and immutable accounting |
| Initial Risk/strategy activation/switch protections | Ownership/subject/FK/CHECK/immutability; missing or ACTIVE switch remains fail-closed |

The partial unique OPEN-position index is separately captured/reviewed. Related
portfolio-time indexes are checked for existence, not claimed as safety locks.

### Locks actually used

- Mandate `owned(lock=True)`: FOR UPDATE, shared by manual and autonomous paths;
  pause/retire races serialize before the locked recheck.
- Work ensure/load/attempt/result/barrier/uncertain recovery: FOR UPDATE.
- Expected candle: FOR SHARE. Canonical instrument/assets/prefix, active user,
  lifecycle and exact six-scope switch reads also use FOR SHARE where implemented.
- Paper book: portfolio and OPEN positions FOR UPDATE. Position management also
  locks the acquisition row; execution persistence locks an existing order.
- Registry owner check: FOR SHARE plus composite ownership FKs and uniqueness.
- READ COMMITTED, non-autocommit, savepoints and one outer economic/work commit
  are mandatory. External market fetching precedes trading locks.
- **Production uses NO SKIP LOCKED, advisory locks or leases.** SKIP LOCKED is therefore not an integration claim test; it is recorded as N/A.

## Commands safe now: offline only

```powershell
Set-Location C:\Users\JOHN-KUNLE\OneDrive\Desktop\traderail
$Py = '.\backend\.venv\Scripts\python.exe'
& $Py backend/integration/runner_postgres.py --plan
& $Py -m pytest backend/tests/test_runner_pg_harness.py backend/tests/test_mandate_runner.py backend/tests/test_mandate_migration.py --basetemp=backend/.pytest_cache/runner_pgtests -q
```

Default/no-argument mode is also offline. Expected plan: OFFLINE_PLAN_ONLY,
connection=false, production_skip_locked=false. No target files are read.
Existing pytest connection guards remain enabled; do not disable them to run
physical tests. Physical phases run as a separate finite CLI, not under pytest.

## Future authorized disposable-branch procedure

1. Create the disposable child in Neon Console; record/verify parent, child and
   DIRECT endpoint. Do not change main/development branch endpoints.
2. Operator edits only ignored `.env.integration` for this child, leaving runner
   disabled. The harness rejects pooled/non-Neon/TLS-disabled targets, ambiguous
   URL aliases and a target not matching the approval hash. It rejects symlinked integration files, ignores inherited
   database variables and disables dotenv interpolation; never reads normal .env.
3. Create the ignored approval file below. Enter actual branch IDs and confirm
   ownership only AFTER comparing the target with Neon Console.
4. Run preflight then migration. The ONLY public schema migration is empty
   **0013 -> 0014 -> 0013 -> 0014 -> 0015** in a guarded transaction. No older revision
   is downgraded; unrelated data/fingerprints must remain unchanged.
5. Run catalog and private-schema cases. Each case bootstraps frozen migrations
   into `tr_runner_<random>_<case>`, search_path fixture,pg_catalog ONLY. Public
   candles, portfolios, history and public work are not fixture storage. Public
   work must stay empty at 0015; any unexpected row stops the campaign.
6. The eight safety cases use `runner-pg-scenario-v1`, an explicit historical
   scenario clock, and can start at any minute. The complete modeled timeline
   must precede PostgreSQL server time. Existing target, revision and baseline
   checks still apply. Client/server clock alignment is measured separately;
   deterministic safety phases never depend on that measurement or hour position.
   Never extend freshness or approval policies.

Approval preparation AFTER branch creation/configuration is authorized:

```powershell
New-Item -ItemType Directory -Force work/runner-pg-results | Out-Null
Copy-Item backend/integration/runner-target.example.json work/runner-pg-approval.json
@'
import json, sys
from pathlib import Path
from dotenv import dotenv_values
from sqlalchemy.engine import make_url
sys.path.insert(0, str(Path('backend').resolve()))
from integration.runner_postgres import fingerprint
try:
    values = dotenv_values('backend/.env.integration', interpolate=False)
    value = values.get('TRADERAIL_DATABASE_URL') or values.get('DATABASE_URL')
    path = Path('work/runner-pg-approval.json')
    approval = json.loads(path.read_text())
    approval['target_sha256'] = fingerprint(make_url(value))
    path.write_text(json.dumps(approval, indent=2))
    print('Offline binding saved; operator confirmation still required.')
except Exception:
    print('STOP: target binding failed; no connection attempted.')
    raise SystemExit(1)
'@ | & $Py -
```

Manually edit that ignored JSON with actual parent/child IDs and
operator_confirmed_child=true only after Console verification. Do not store a
URL/password in the approval. Do not approve the versionable example file.

Physical commands below are NOT authorized/executed in Step 6A:

```powershell
$Approval = 'work/runner-pg-approval.json'
$Child = (Get-Content $Approval -Raw | ConvertFrom-Json).child_branch_id
$Common = @('--env-file','backend/.env.integration','--approval',$Approval,'--confirm-child',$Child,'--artifacts','work/runner-pg-results')
& $Py backend/integration/runner_postgres.py @Common --phase preflight
if ($LASTEXITCODE -ne 0) { throw 'STOP: preflight failed' }
& $Py backend/integration/runner_postgres.py @Common --phase migration
if ($LASTEXITCODE -ne 0) { throw 'STOP: migration failed' }
& $Py backend/integration/runner_postgres.py @Common --phase catalog
if ($LASTEXITCODE -ne 0) { throw 'STOP: catalog failed' }
```

Run ONE case at a time, inspect its exit code/output, and stop immediately on
failure. Do not paste an unchecked loop or continue down this list after STOP.

```powershell
& $Py backend/integration/runner_postgres.py @Common --phase contracts
if ($LASTEXITCODE -ne 0) { throw 'STOP: contracts failed' }
& $Py backend/integration/runner_postgres.py @Common --phase locks
if ($LASTEXITCODE -ne 0) { throw 'STOP: locks failed' }
& $Py backend/integration/runner_postgres.py @Common --phase race
if ($LASTEXITCODE -ne 0) { throw 'STOP: race failed' }
& $Py backend/integration/runner_postgres.py @Common --phase rollback
if ($LASTEXITCODE -ne 0) { throw 'STOP: rollback failed' }
& $Py backend/integration/runner_postgres.py @Common --phase kill
if ($LASTEXITCODE -ne 0) { throw 'STOP: kill failed' }
& $Py backend/integration/runner_postgres.py @Common --phase recovery
if ($LASTEXITCODE -ne 0) { throw 'STOP: recovery failed' }
& $Py backend/integration/runner_postgres.py @Common --phase barriers
if ($LASTEXITCODE -ne 0) { throw 'STOP: barriers failed' }
& $Py backend/integration/runner_postgres.py @Common --phase close
if ($LASTEXITCODE -ne 0) { throw 'STOP: close failed' }
```

| Phase | Expected physical result |
| --- | --- |
| preflight | Public 0013, approved target, PG READ COMMITTED, clocks aligned; baseline.json saved |
| migration | Empty upgrade/downgrade/upgrade passes; final public 0015; baseline unchanged |
| catalog | Column/PK/CHECK/UQ/FK/index/trigger inventory recorded; corrected CLOSED and partial OPEN semantics reviewed |
| real-clock | At 0015: bounded real UTC/PG clock measurement accepted; canonical hour/freshness boundary probes pass with explicitly synthetic in-memory candles; no fixtures or financial writes |
| contracts | Version/UUID cannot bypass dedup; invalid writes fail; UTC preserved; terminal update/delete and populated downgrade refuse without mutation |
| locks | Independent connections: FOR UPDATE blocks, lock_timeout gives 55P03, FOR SHARE excludes writer locks, release permits progress; SKIP LOCKED is N/A |
| race | Two spawned workers execute real process_one; pg_blocking_pids confirms B waits; one canonical economic effect, B recovers stored work |
| rollback | Test-only failure after real persistence rolls back authority/economics/cash; durable RECONCILIATION_REQUIRED remains and due() refuses retry |
| kill | Terminate ONLY the owned local fixture worker before commit; server releases locks, no partial facts, fresh client completes once |
| recovery | Terminate after commit before normal worker completion; fresh client recovers exact work without another economic effect |
| barriers | Uncertain pending work is durably quarantined under mandate/work locks; fresh clients refuse automatic processing |
| close | Genuine fixture HOLD then EXIT_LONG + NEW Risk authorization; two legs, one CLOSED position, canonical P&L/cash reconciliation, terminal retry barrier |

A successful physical phase exits 0 and prints:
`PASS: RUNNER POSTGRES <PHASE>; public baseline unchanged; fixtures retained on disposable child`.
Any STOP/nonzero exit means retain the branch and inspect; never treat a partial
PASS followed by a cleanup STOP as campaign success.

Every economic case checks exact cash against canonical receipt cash_after,
receipt cash chaining, position quantity and counts for requests, consumptions,
orders, fills, positions, receipts, authorizations and commands. It does not
implement a second accounting engine. No providers/network are allowed in
fixtures; required completed history is seeded only in the private schema.

Every safety phase uses the same serializable historical scenario, including
entry race/kill/recovery. Close advances explicitly through entry, genuine HOLD,
genuine exit and a retry two days after exit. These are simulated event times
with real PostgreSQL constraints and transactions, not production historical
catch-up or real-time market validation. All clocks passed to work creation,
attempts, due/recovery, Risk and execution originate in that timeline.
No strategy/Risk threshold or accounting behavior is patched. Fault hooks only
pause or raise after persistence. A legitimate unexpected strategy/Risk outcome
fails the fixture assertion; it must not be forced into approval.

Recovery models a lost application result after commit, NOT a TCP proxy dropping
PostgreSQL's COMMIT reply. Do not claim wire-level fault verification or a
mathematical proof covering every possible duplicate-action failure mode.

## Cleanup / blockers before real authorization

All fixture schemas/artifacts are retained, including on failure. Public data and unrelated-schema
fingerprints are compared to the original baseline across every phase. Do not
broadly truncate, disable triggers or delete economic rows. On STOP, retain the
child/fixture manifests and investigate; do not blindly rerun over retained state.
After review, confirm the recorded child ID/parent again and DELETE ONLY THAT
DISPOSABLE CHILD in Neon Console. Branch disposal is the cleanup mechanism.
Do not erase history via application downgrade or promote 0015 to development
without separate authorization and a passing physical campaign.

Pending external prerequisites: child creation/approval, DIRECT integration
configuration and revision/privilege/clock confirmation.
The harness is source/offline tested only. No physical PostgreSQL result is
claimed. Any discrepancy discovered during real validation must stop the run
before product changes are considered.


### Blocking FOR UPDATE: safety and accidental concurrent processes

Blocking FOR UPDATE is appropriate for the current controlled single-runner
workload. A nonexistent work row cannot itself be locked, so the existing mandate
row is locked first, and the unique mandate/candle constraint is the final guard.
With two accidental instances, discovery may select the same candidate twice.
The second execution transaction waits for the mandate lock; once the first
commits, it reads/reuses the completed work and does not repeat the economic
operation. If the first rolls back/dies, the waiter may proceed only after lock
release and fresh eligibility/evidence checks. If the configured lock timeout
expires first, it fails closed rather than skipping authorization or freshness.
Different mandates may still serialize on a shared portfolio or initial safety
provisioning; this is bounded contention, not a claim of unlimited parallelism.

These are source-level expectations, not a physical concurrency PASS. The harness
uses a ten-second bounded lock timeout for its synchronized two-process race;
the product default is three seconds. The locks case explicitly forces 250ms to
verify 55P03. None of these fixture settings change the product configuration.
Unpersisted reconciliation suppression remains process-local if storage cannot
be inspected; operator review before restart is still required in that case.

Cleanup failures are reported separately and return a failing exit code without
printing raw driver errors. The harness never silently deletes retained state.


## Preflight diagnostics and deadline

Preflight emits flushed, sanitized progress stages for connection establishment,
revision/work-table and clock checks, every table fingerprint, catalog queries,
baseline artifact handling, the second full fingerprint pass and final checks.
No connection metadata is logged. On each preflight connection it verifies the
psycopg connection timeout (10 seconds) and active PostgreSQL statement (30 seconds),
lock (10 seconds), and idle-transaction (60 seconds) settings. Missing/different
settings refuse verification.

An independent 180-second watchdog bounds the entire preflight, including blocked
network calls and resource release. Expiry prints a sanitized STOP with the last
stage and exits only this finite harness process with status 1. It never terminates
other processes or PostgreSQL sessions. The operating system closes this process's
sockets; server-side disconnect detection is not guaranteed to be instantaneous.
Preflight performs no migrations or fixture writes. A partial baseline artifact
must not be treated as PASS; retain evidence and investigate any STOP before
continuing. Both complete baseline passes and all existing safety checks remain.
The watchdog is not enabled for migration or fixture phases.


Preflight connection diagnostics distinguish `DBAPI_CONNECTION_ESTABLISHING`
(transport, TLS and PostgreSQL startup negotiation) from
`SQLALCHEMY_CONNECTION_INITIALIZING` (dialect/session setup after a DBAPI
connection exists), then `SQLALCHEMY_CONNECTION_READY`. Startup negotiation is
part of the DBAPI call; these markers cannot separate its individual network
steps. Failures emit only fixed categories: CONNECTION_TIMEOUT, TLS_FAILURE,
SOCKET_FAILURE, STARTUP_OPTIONS_FAILURE, INITIALIZATION_FAILURE or
UNKNOWN_CONNECTION_FAILURE. Driver-message classification is best-effort and
never prints the message or connection arguments. Unknown failures remain
unknown; no automatic retry or configuration fallback is introduced. All
original connection arguments and startup options remain unchanged.


The finite verification harness now uses a 10-second connection timeout for the
controlled comparison with the successful standalone connection. All startup
options and other deadlines remain unchanged; this does not change production
runner database configuration. No retries or fallback connections are added.
Typed psycopg/OS timeout evidence takes precedence over generic socket wording.
A SOCKET_FAILURE is not proof that startup options were accepted: PostgreSQL
startup negotiation still occurs within the DBAPI connection stage.


Catalog also emits sanitized connection-stage diagnostics and the existing fixed
connection-failure categories. Reflection operations identify the table and
constraint/column/index operation; numbered CATALOG_SQL markers identify each
SQLAlchemy cursor execution without printing SQL, bind values or connection
metadata. Runtime schema readiness, trigger/function definitions and partial
OPEN index checks have explicit stages. Catalog retains the same 10-second
connection and 30/10/60-second session timeouts, both public baseline passes and
all catalog assertions. The preflight-only watchdog remains preflight-only.


## Implementation 1: deterministic scenario time

`integration/runner_scenario.py` owns the immutable, serializable historical
`ScenarioTimeline` and callable `ScenarioClock`. The timeline begins at
2026-01-15 12:01 UTC, holds at the next candle, exits at candle 218 of the existing
219-candle dataset and recovers the terminal result two days later. A dataset
length/timestamp mismatch refuses fixture setup rather than adapting strategy.
The manifest and successful phase artifact record `SIMULATED_HISTORICAL` and all
scenario event times. Every new invocation still uses its own private schema.

Both the coordinator and spawned workers receive that exact timeline. No safety
phase calls the default production clock; `cycle_once` requires an explicit
`at`. The original freshness, five-minute approval age, work deadlines and
reconciliation/terminal barriers are evaluated at those explicit times. No
PostgreSQL clock is replaced: the existing future-creation guard and
`statement_timestamp()` stay authoritative. Mandate recording timestamps remain
real database timestamps, distinct from modeled event times.

Wall/monotonic process waits, connection/query timeouts, worker supervision and
cleanup are unchanged. Implementation 2 below replaces the clock comparison and
adds the separate real-clock lane. Worker-supervision corrections are implemented in Step 3 below.
No physical PostgreSQL PASS is established by the offline scenario tests.


## Implementation 2: bounded clock observations and separate real-clock lane

The former server-time versus client-time-after-response comparison is removed.
Non-economic admission phases (preflight, migration, catalog) use the bounded
measurement below. The eight deterministic safety phases do not measure client
skew; they only verify their complete historical timeline precedes server time.
An accepted separate real-clock lane is still required for campaign acceptance.

Each measurement captures client-before UTC, PostgreSQL `clock_timestamp()`,
client-after UTC and monotonic round-trip elapsed time. The possible server-minus-
client offset interval is [server - client-after, server - client-before], expanded
conservatively by 50ms of measurement slack. The midpoint is an estimate only.
The original strict two-second tolerance is unchanged: the entire interval must
fit inside (-2s,+2s) to be WITHIN_TOLERANCE. An interval entirely outside is
OUTSIDE_TOLERANCE; overlapping/ambiguous intervals are INCONCLUSIVE. Wall versus
monotonic discontinuities within or between samples also mean INCONCLUSIVE,
not proof of a bad Windows clock. The 50ms slack does not enlarge allowed skew.

At most THREE read-only samples are taken. Only latency/tolerance uncertainty
permits another sample; a step/discontinuity or proven out-of-tolerance interval
stops immediately. Driver failures propagate without reconnection or retry.
INCONCLUSIVE and OUTSIDE_TOLERANCE both return STOP/nonzero before baseline work
or any migration. Measurement artifacts are retained for either result. No
measurement retries an economic operation.

After the child is already at 0015 and the accepted baseline exists, the future
separate command is:

```powershell
& $Py backend/integration/runner_postgres.py @Common --phase real-clock
if ($LASTEXITCODE -ne 0) { throw 'STOP: real-clock validation failed' }
```

The phase records actual client/server UTC observations and expected completed
1h candle identities. It can run at any minute: a time outside freshness should
correctly reject stale evidence. In-memory SYNTHETIC probes exercise immediately
before/at the UTC-hour rollover, immediately before/at the freshness deadline,
and a prior stale candle prefix using the unchanged canonical analysis code.
These are not retrieved stored candles, real provider prices, Risk approvals or
trades. Artifacts explicitly separate REAL_CLOCK_OBSERVATION,
REAL_CLOCK_WITH_SYNTHETIC_BOUNDARY_PROBES, and SIMULATED_HISTORICAL results.
Do not claim live-market readiness from a boundary-probe PASS.

Step 2 did not change worker coordination or cleanup. Step 3 below adds finite
worker supervision; transport timeouts and the preflight-only 180s watchdog
remain unchanged. Wire-level COMMIT acknowledgement faults remain unverified. No real PostgreSQL results are implied
by offline tests of injected clock samples.


## Implementation 3: finite worker supervision and evidence

The race/kill/recovery cases now separate bootstrap from trading execution.
Every trading worker and read-only probe is a separately spawned, owned process
that constructs its own engine/connections. No SQLAlchemy engine/session or live
connection is passed between processes. Bootstrap validates schema/readability,
reports READY and waits on a start gate. For race, BOTH trading workers and an
independent PostgreSQL observer are READY before A is authorized to execute.
Readiness does not guarantee later network availability: a subsequent connection
can still fail, and failure stops the test rather than retrying trading.

Fixed integration-only monotonic limits (not production runner settings):

| Operation | Deadline |
| --- | --- |
| Joint trading/observer startup | 120 seconds |
| Trading execution/results/recovery inspection | 180 seconds |
| Coordination / pre-commit pause / after-result pause | 30 seconds |
| PostgreSQL blocking observation | 8 seconds, within the unchanged 10s lock timeout |
| Owned-worker cleanup | 20 seconds |

Startup does not consume the deliberate transaction pause. PostgreSQL observation
and durable inspection run in owned read-only probe processes, so stalled probe
connections/queries can be terminated without preventing trading-worker cleanup.
A probe acquires the existing mandate FOR UPDATE lock before inspecting durable
work, canonical references, authority/economic counts, cash and receipts. It does
not execute trading or modify rows. Pending, partial or conflicting facts fail
closed. Inspection errors/timeouts permit NO recovery cycle.

Worker READY/RUNNING/PRE_COMMIT/COMMITTED/FAILED events are application reports.
COMMITTED explicitly carries APPLICATION_REPORT_ONLY and an unconfirmed database
outcome until a successful probe reads the canonical work/receipts. Terminating a
process is not proof of PostgreSQL rollback or immediate server lock release.
The kill case re-executes its deliberately interrupted fixture only AFTER fresh
locked inspection confirms no work/authority/economic effect and unchanged cash.
Recovery of an already committed fixture requires positive durable confirmation
first. No uncertain economic operation is automatically retried.

The ONLY commit-release gate opening is the normal race success path AFTER
PostgreSQL confirms actual contention. No release/start gate opens in cleanup.
On any STOP, all owned trading/probe processes are terminated if alive, joined,
and checked. Unconfirmed death or cleanup failure returns STOP; primary assertion
and cleanup failures remain separately reported. Previously authorized work might
already have committed when another failure is detected: STOP never asserts
rollback and retained state must be inspected before a manual rerun.

Before schema/fixture work, the manifest records the private schema, scenario and
expected worker sidecars. Phase events and per-worker JSONL files are fresh,
append-only, flushed/fsynced, and contain only sanitized operational codes and
identifiers. Sidecars remain if a worker is terminated before it can report a
result. Phase PASS is published only after assertions, both baseline checks,
owned-worker cleanup and database-resource cleanup succeed. A .pending file is
not PASS; require exit 0 plus the final PASS artifact/output. All private schemas
and failure evidence remain retained. No trigger disabling or destructive cleanup.

Host/OS failure, externally killing the coordinator, or storage failures can
interrupt normal cleanup. Never assume surviving processes are absent in those
cases; inspect before rerun. Offline tests establish supervision/control flow,
not physical PostgreSQL transaction outcome, lock release or COMMIT-wire behavior.
The real race/rollback/kill/recovery/accounting phases remain mandatory physical
evidence before activation. No physical PASS is claimed by this implementation.


## Corrective revision 0015 and retained 0014 campaigns

0014 is immutable. 0015 replaces only the two machine-code CHECKs and refuses
downgrade. Fresh-child `migration` starts at public 0013, exercises the empty
0014 round trip, then installs 0015 in the same transaction. PASS requires 0015;
no 0015 downgrade or successful final 0014 state is permitted. Failure rolls
back the migration transaction and remains STOP, not promotion evidence.

For an existing approved, unexpired disposable child at 0014: retain the original
approval, baseline and every failed schema/artifact. Do not rerun preflight or
fresh-child migration. With separate operator authorization run `--phase correction`
using the SAME approved target and artifact directory. It requires revision 0014,
empty public work, and equality with the accepted original public baseline; only
public 0015 is applied. Private failed schemas remain unchanged. A STOP requires
inspection, never an automatic retry. After PASS run catalog, real-clock and all
eight safety phases (new isolated schemas); catalog requires the corrected code
patterns. Old results do not substitute for these corrected-schema results.

Use the existing Common CLI arguments with `--phase correction`; no command in
this section authorizes execution. Verify child lifetime/identity before approval.
If expired, prepare a new approved child with a genuine 0013 starting state; do
not downgrade a populated or 0015 database to manufacture that prerequisite.
Runner activation remains separately prohibited and disabled by default.
