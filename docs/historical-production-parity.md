# M18.2 — rolling strategy parity and opportunity analysis

**HISTORICAL SIMULATION — NOT LIVE OR FORWARD PERFORMANCE**

## What is and is not aligned

The local evaluator reuses canonical intelligence, regime classification and
Adaptive Trend 1.0.0 defaults, recomputing exactly 100 hourly candles at each close.
Original archival retrieval timestamps are preserved; observation time is the
maximum of completion and retrieval within the window. Decision timestamps remain
historical candle closes. This is retrospective analysis, not proof of historical
real-time data availability, operational freshness, or permission to execute.

Only LONG/CASH research fills exist. CASH UNKNOWN + NO_ACTION is a valid no-trade;
UNSAFE and actionable UNKNOWN entry decisions are rejected. Held-position genuine
EXIT_LONG uses the distinct production close-proposal semantics (including an
UNKNOWN regime if the genuine position-aware strategy exits), without fabricating
Risk permission. HOLD remains non-actionable. Unsupported SHORT proposals never
open positions or reverse LONG positions. Strategy thresholds are unchanged.

The default retains production v1's first-close terminal barrier. Use
`--repeated-research-cycles` only for explicitly NON-PRODUCTION opportunity studies.
Do not interpret a one-trade first-cycle report as strategy-wide trading frequency.
Full, IS and OOS periods start independent ledgers; no holdings, fills, cash/P&L
or pending decisions cross the split. OOS uses prior candles as rolling analytics
warmup only. No optimization or threshold search exists. Multiple non-overlapping
periods can be evaluated with separately predeclared --start/--split/--end windows.

### Risk limitations

No historical authoritative portfolio snapshots, drawdown genesis/transition
provenance, strategy lifecycle/health attestations, kill-switch states or expiring
authorizations are manufactured. The four production Risk gates are NOT claimed
to have passed. Risk rejection counts are unavailable, not inferred from no-trade.

Research uses a predeclared fixed BTC quantity. Canonical research adverse pricing
and fee-inclusive cash commitment are compared with actual simulated cash and an
explicit research ceiling; rejection does not resize quantities or fake cash.
These are `research_funding_rejected`, NOT Risk decisions. All simulated fills are
labelled `RESEARCH_FILL_NOT_AUTHORIZED`. Actual production authorizations, requests,
orders, positions, accounts and runner work are never created.

Historical fills occur at NEXT candle open with existing `paper-costs-v1` fee,
full-spread/2 and slippage rates. Production PAPER execution uses its authorized
close-price observation; the report explicitly does not claim execution-price
parity. No order-book liquidity, latency, exchange precision or market impact is
modelled. Open positions are marked at final close, never forcibly closed.

## Local data workflow

The operator approved unauthenticated Binance Public Data spot archive downloads.
No API keys, new dependencies, database connection or normal dotenv are used.
Original ZIPs, official SHA256 CHECKSUM files and retrieval URL/date manifests
remain separate from normalized JSONL. Downloads are explicit, bounded, no-retry,
TLS-verified and do not follow redirects. Only fixed public archive URLs are used.

Official documentation: https://github.com/binance/binance-public-data
Archive path: https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/
The symbol convention is Binance SPOT BTCUSDT = BTC/USDT, UTC 1h opens. Spot
archives before 2025 use milliseconds; 2025 onward uses microseconds. Source end
timestamps are checked in their original unit, then normalized to canonical
inclusive final milliseconds. Prices and volumes are never interpolated.
The source repository documentation does not establish a market-data redistribution
license; reports mark data licensing undetermined. Verify rights before redistribution.
Checksums establish transfer integrity against the downloaded manifest, not a
cryptographic proof of market correctness or tradable historical availability.

Import validates archive hashes, bounded single CSV content, month/unit, completed
intervals, finite Decimal OHLCV, chronological order, duplicates and continuity.
Incomplete rows are explicitly excluded. Other invalid/missing/duplicate rows make
the dataset unusable; no silent sorting, interpolation or economic replay follows.
Counts and up to 100 safe rejection examples remain in dataset.json. Failed output
directories remain for inspection; choose a new output path rather than overwrite.

Default bound is 20,000 candles; explicit --max-candles supports 100–100,000.
Each archive/normalized file has a 64 MiB bound, manifests/lines are separately
bounded, and acquisition is capped at 24 months per manifest. Replay calculations
hold only a 100-candle analytical deque, bounded snapshots and numeric metric
series/trades. Detailed decision/equity events stream to JSONL; no expanding
analytical prefixes are recalculated. Runtime is proportional to observations
with fixed-size analysis. No unbounded dataset or network fetch is accepted.
Position basis and equity peak persist independently of the 100-candle window.

## Commands (PowerShell, from backend)

Use NEW output directories; no-overwrite is intentional. The following exact
archive and split choice was declared before inspecting results, not optimized.

```powershell
.\.venv\Scripts\python.exe -m app.strategy_lab.historical.parity download `
  --month 2024-01 --month 2024-02 --month 2024-03 `
  --month 2024-04 --month 2024-05 --month 2024-06 `
  --output ..\work\m18-2-review-raw

.\.venv\Scripts\python.exe -m app.strategy_lab.historical.parity import `
  --manifest ..\work\m18-2-review-raw\archives.json `
  --output ..\work\m18-2-review-normalized

.\.venv\Scripts\python.exe -m app.strategy_lab.historical.parity evaluate `
  --dataset ..\work\m18-2-review-normalized `
  --output ..\work\m18-2-review-first-cycle `
  --start 2024-01-05T03:00:00Z --split 2024-04-01T00:00:00Z `
  --end 2024-07-01T00:00:00Z `
  --capital 500 --quantity 0.005 --commitment-ceiling 500
```

For NON-PRODUCTION multiple-cycle diagnostics use the same evaluation arguments,
a new --output directory, and `--repeated-research-cycles`. To reuse the already
downloaded data, substitute `..\work\m18-2-binance-2024h1-normalized` for --dataset;
do not redownload merely to rerun analysis. Source originals and generated reports
remain ignored work artifacts. Neither import nor evaluate accesses a provider.

## Reading reports correctly

report.json binds source HEAD, actual local source hashes, dataset integrity,
configuration/split and event/benchmark file hashes. Uncommitted source is explicitly
identified. JSONL event chains retain window boundaries, decisions/reasons, regimes,
CASH counterfactuals, fills, cash, equity and drawdown. No report is PASS until the
complete evaluation and artifact writes finish. On failure, FAILED metadata and
partial evidence remain; do not treat them as completed performance.

Counts distinguish eligible evaluations, UNKNOWN/UNSAFE, NO_ACTION, genuine LONG
entry proposals, held-position CASH probes, HOLD, EXIT_LONG, research funding
rejection and completed fills. SHORT proposals are capability exclusions, not
Risk rejection. Counts overlap where explicitly named (UNKNOWN can also NO_ACTION).

Signal frequency = actual LONG entry proposals / eligible managed evaluations.
Entry frequency = simulated entry fills / eligible evaluations. Completed-trade
frequency = completed trades * 720 / period hours. CASH probes while holding are
counterfactual diagnostics only; they do not imply permitted duplicate exposure.
Entry-condition pass/fail rates are measured ONLY where canonical LONG
`entry_confirmations` explicitly exist. In non-trending/UNKNOWN contexts the checks
are unobserved, not assumed false. Do not infer missing condition failures.

All financial values are Decimal strings. Return, drawdown, win rate, frequency
and utilization are fractions; exposure_percentage is percent. Utilization is
gross marked exposure / initial capital; it is not a wallet balance. Net realized
P&L is completed-trade net; curve realized P&L separately includes incurred open
entry fees. Ending equity includes open mark-to-market; future exit fees are not
charged. Undefined win rate/PF/expectancy/holding duration remain null.

Buy-and-hold and cash share initial capital and exact period dates. Buy-and-hold
uses the existing fee-inclusive ROUND_DOWN allocation convention and first-open
entry with the same adverse costs; its final close is marked without a fictional
exit fee. Cash has zero return/exposure. Strategy fixed quantity often has much
less exposure, so benchmark differences are not risk-adjusted alpha claims.

## Genuine initial study and next decisions

Approved archives contained 4,368 consecutive candles, Jan 1–Jun 30 2024. Evaluation
began Jan 5 03:00 after 99 warmup bars; April 1 is the fixed IS/OOS boundary.
First-cycle mode and separately labelled repeated research were both exercised.
Generated work reports contain the exact figures; synthetic unit fixtures are
never used as real market-performance evidence.

A negative OOS result must remain negative. Frequent UNKNOWN or a high conditional
momentum-failure rate does not itself prove excessive conservatism. Separate
regime coverage, condition coverage, existing-position exclusions, economic costs
and unavailable Risk evidence before proposing any change.

M18.3 candidates: additional predeclared non-overlapping real periods, benchmark
exposure-matched comparisons, independently reviewed statistical sufficiency,
and a deliberate historically evidenced Risk-sizing/operational replay contract.
Do not loosen production thresholds or infer live-money readiness from this study.
