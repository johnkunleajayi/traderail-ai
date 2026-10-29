# Autonomous PAPER runner foundation

The runner provides durable candle work, bounded market maintenance, one-cycle
evaluation and an explicitly launched polling process. Nothing starts inside
FastAPI or depends on browser/request traffic. There is no runner HTTP endpoint
or UI. Strategy, Risk and execution remain the existing separate services.

## One cycle

`app.mandate_runner.cycle.process_one(session_factory, mandate_id)` uses a
fresh-session factory and a real UTC clock by default. It derives the owner
from the saved mandate and checks existing technical eligibility. Market
maintenance completes its transactions and external fetch before trading locks.
The cycle then locks the mandate, rechecks eligibility and terminal barriers,
loads the canonical candle, and locks/creates the unique mandate/candle work.
Runner version never changes the deduplication key.

The existing `mandate_paper.run()` performs the actual strategy, Risk and Paper
work. An optional expected-candle binding checks the entry observation before
execution, and the actual held-position candle before strategy analysis. The
execution clock also rejects expiry, clock reversal and crossing an hourly
boundary. Manual callers omit this optional binding and retain existing behavior.

A savepoint isolates orchestration. Successful work metadata and authorization,
fill, position and accounting facts share the outer commit. A failed evaluation
rolls back its savepoint before a safe failure result is stored. No separate
runner financial ledger exists.

## Outcomes and recovery

- NO_TRADE, Risk rejection, HOLD, entry and close are completed evaluations.
- Transient failures can become RETRY_WAIT, due in 30 seconds and strictly before
  the original freshness deadline. A retry never extends that deadline.
- Stale/mismatched evidence is skipped. History is never traded using a backdated
  timestamp; only the latest completed candle is eligible for new work.
- Incomplete/ambiguous execution, conflicting authorization or an unexpected
  evaluation failure requires reconciliation. A persisted reconciliation result
  blocks subsequent candles for that mandate.
- Completed work is recovered without calling orchestration again. A completed
  close blocks later cycles. The existing first-entry/full-close limitation is
  preserved; this is not a repeated trading-cycle implementation.
- Pausing/retirement racing market maintenance is rechecked under the mandate
  lock before orchestration. Existing kill switches and all Risk gates remain
  authoritative, including on exits.

`CycleResult.persisted` is explicit. Without a canonical candle, or when an
already-expired candle has no work record, the service cannot insert durable
work (its FK and freshness guards prohibit invented/backdated work). It returns
an unpersisted safe result. Existing stale pending work can be durably skipped.
An uncertain database/commit failure returns unpersisted reconciliation-required
status: consult durable work and economic receipts before retrying; this call
never retries execution internally. The process inspects uncertain state under
mandate/work locks, quarantines existing incomplete work, and suppresses the
affected mandate for the remainder of that process. Persisted reconciliation and
closed-lifecycle barriers are respected on every subsequent process/restart.

## Verification boundary

Tests use guarded isolated session/ledger doubles, including the unchanged real
Strategy, Risk and Paper services. They do not establish physical PostgreSQL
locking, trigger enforcement, concurrent-connection or process-crash behavior.
Migrations 0014 and corrective 0015 must be explicitly applied and physically verified in an isolated
environment before running this service against a database. No migration is
performed by the service.


## Separate process (default disabled)

After isolated physical verification and separately authorized schema promotion:

```powershell
Set-Location C:\Users\JOHN-KUNLE\OneDrive\Desktop\traderail\backend
.\.venv\Scripts\python.exe -m app.mandate_runner --env-file .env.runner
```

The operator must create the ignored `.env.runner` file explicitly, or supply
process environment variables. The process does not implicitly load `.env`.
A missing explicitly selected file fails closed. `TRADERAIL_DATABASE_URL` must
be explicitly configured with the `postgresql+psycopg` scheme when enabled.
Use the DIRECT Neon endpoint for isolated verification; never expose its URL.
No credentials belong in source, logs or command-line arguments.

`TRADERAIL_PAPER_RUNNER_ENABLED=false` is the default and opens no database or
provider connection. To stop a running instance use Ctrl+C/SIGINT or SIGTERM;
configuration changes take effect on the next explicit launch. There is no
service installer, automatic restart supervisor, FastAPI startup hook or cron.

Operational defaults (all server-owned and bounded):

| Setting after TRADERAIL_ prefix | Default | Allowed |
| --- | --- | --- |
| PAPER_RUNNER_POLL_SECONDS | 15 | 5-300 seconds |
| PAPER_RUNNER_PAGE_SIZE | 100 | 1-1000 mandates |
| PAPER_RUNNER_CONNECT_TIMEOUT_SECONDS | 5 | 2-30 seconds |
| PAPER_RUNNER_STATEMENT_TIMEOUT_MS | 15000 | 1000-60000 ms |
| PAPER_RUNNER_LOCK_TIMEOUT_MS | 3000 | 100-10000 ms, below statement timeout |
| PAPER_RUNNER_IDLE_TRANSACTION_TIMEOUT_MS | 30000 | 5000-120000 ms |
| PAPER_RUNNER_NETWORK_TIMEOUT_SECONDS | 8 | 1-30 seconds; connect capped at 3 |

The process uses a separate NullPool SQLAlchemy engine with READ COMMITTED,
non-autocommit transactions and PostgreSQL connection/statement/lock/idle
transaction timeouts. HTTPX applies bounded connect/read/write/pool timeouts;
the existing Binance adapter also limits response size. These are I/O and SQL
operation limits, not a hard deadline for an entire multi-statement cycle.

Startup performs SELECT-only revision/object checks and refuses a missing or
inconsistent 0015 work foundation. It never applies migrations. Authentication
settings are not required to run this server-owned process.

Each poll keyset-pages through ACTIVE PAPER mandates and uses the existing
eligibility resolver. It ignores barriers, terminal current-candle work and
RETRY_WAIT items not yet due. Outside the existing ten-minute 1H freshness
window, it waits without historical replay. Discovery is advisory: competing
processes may discover the same candidate, but `process_one()` serializes on the
mandate/work/portfolio locks and the unique mandate/candle constraint. No lease,
in-process lock or cache replaces PostgreSQL authority. No database lock is
held while fetching external market data.

Shutdown is cooperative. An interruptible Event wait wakes immediately, no new
mandate starts, and checked clocks unwind in-flight cycle transactions before
new economic work can commit. A commit already completed remains recoverable.
In-flight I/O drains according to its configured timeouts. Normal restart reads
durable state rather than replaying a saved queue of old hourly candles.

Every mandate failure is isolated. Read-only admission failures may be checked
again next poll. Unexpected processing failures and unpersisted reconciliation
results suppress that mandate in the current process and emit an operator
alert. Unknown commit inspection acquires the mandate/work locks before reading:
completed work is recovered, incomplete existing work is durably quarantined,
and absent work proves no atomic cycle commit occurred. No recovery path calls
execution. If the database cannot be inspected, or no work/candle exists to
anchor a durable quarantine, suppression is process-local: investigate before
restarting, and do not install an unattended crash-restart supervisor yet.
A restart still re-reads authoritative barriers/work and the original canonical
execution guards; it never blindly resubmits an UNKNOWN order.

Logs are JSON events containing UTC time, safe machine codes and UUID references.
They exclude connection details, raw exceptions, user emails and financial
payloads. The process never logs SQL parameters or settings/credentials.

## Safest physical verification plan (NOT performed)

Do not test destructive migration/kill scenarios on the shared development
branch. Create a disposable Neon child branch/snapshot of the existing dev
state and use its DIRECT endpoint in a dedicated ignored integration config.
Confirm branch ownership out-of-band and keep the runner disabled throughout
preflight. Use an explicitly configured integration connection for Alembic;
never rely on the normal `.env` fallback in `alembic/env.py`.

1. Capture the current revision, accepted application counts/fingerprints and
   portfolio/economic state on the disposable copy. Check the expected 0013
   starting revision rather than assuming it.
2. Apply 0014 then 0015 only to that copy after explicit authorization. Verify all columns,
   FKs, uniqueness, CHECKs, indexes and trigger bodies against source. Perform
   empty 0014 -> 0013 -> 0014, then 0015 before inserting any work fixtures; a populated
   downgrade must refuse without changing history.
3. Use clearly tagged deterministic fixtures on the disposable branch and two
   independent PostgreSQL connections/processes. Synchronize competing same-key
   attempts: verify exactly one work row, approval consumption, order, fill,
   position mutation and accounting receipt. Verify separate mandates progress
   independently and lock timeout does not leave partial effects.
4. Inject rollback, terminate a worker before commit, and simulate a lost commit
   acknowledgement. Restart and prove durable work/receipts recover without
   re-execution. Test shutdown, stale-hour skipping, reconciliation barriers and
   terminal-close barriers. A new runner version must not change deduplication.
5. Recheck unrelated baseline fingerprints. Dispose the entire test branch after
   review rather than disabling immutability triggers, deleting economic facts
   or broadly truncating tables. Promote 0015 to development only after these
   checks pass and promotion is separately authorized.

Current offline tests do not prove actual PostgreSQL locking, concurrent-client
behavior, trigger execution, Neon migration success or process-kill recovery.
No Neon connection or migration is part of the source milestone.
