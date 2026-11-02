# M18.3 — reproducible robustness and loss investigation

**HISTORICAL SIMULATION — NOT LIVE OR FORWARD PERFORMANCE**

This milestone analyzes existing verified historical simulation artifacts. It does
not implement a new trading engine, change Adaptive Trend, create authorizations,
access application accounts or influence the independently operating PAPER runner.

## Recovered evidence and immutable baseline

Baseline committed HEAD: `fdc62a5`. The frozen `m18-3-protocol.json` was prepared
before the fresh baseline evaluations. The copy in this directory is byte-for-byte
identical to `work/m18-3-protocol.json`; no research assumptions were replaced.
Protocol identity:
`sha256:7f553dfb354211f3a892f93e0905f9381a2628bdf16697bb18d7cbeb59366a2f`.

The baseline is Adaptive Trend 1.0.0 defaults, BTC/USDT 1h, 100-candle rolling
analysis, LONG/CASH, fixed 0.005 BTC research quantity, 500 USDT starting capital
and fee-inclusive commitment ceiling, canonical Paper modeled costs and explicitly
NON-PRODUCTION repeated-cycle research. Actual production first-close terminal
semantics remain unchanged and are distinct from this research mode.

Every analysis verifies parent report identity, physical event/benchmark file
hashes, complete event hash chains, normalized dataset identity, canonical rolling
window fingerprints, retained feature/measurement values and classification
candidate/reasons. Numerical strategy/intelligence/regime/pricing/accounting/cost
source hashes must match the frozen protocol and current source exactly. Old 2024
reporter/loader metadata-only source drift is reported explicitly; it is not
silently treated as numerical drift. A shared baseline fingerprint must match
across studies. The original source artifacts are never edited or promoted.

Original archive retrieval dates remain intact. Historical candle-close decisions
and next-open research fills are retrospective assumptions, not proof of real-time
market-data availability or production permission. All four Risk gates remain
mandatory in production; they are not claimed to have passed historically.

## Period selection and acquisition

Rule: use January–June in each of the two years immediately preceding the existing
2024 baseline, not a period chosen for Adaptive Trend performance. Starts: Jan 5
03:00 UTC after 99 warmup hours; splits: Apr 1; ends: Jul 1 exclusive. The operator
specifically approved these 12 public monthly Binance archives before acquisition.
No new download or import was repeated during recovery.

- 2022H1: 4,344 complete hourly candles; unchanged existing baseline evaluation
  completed successfully during the interrupted session and was reused.
- 2023H1: retained as REJECTED. The checksummed March archive has a Mar 24 12:00
  candle ending at 12:39:41.646 UTC, followed by 14:00, with no 13:00 candle.
  Accepted rows: 4,342; one invalid partial interval and two unavailable canonical
  intervals. No fill, interpolation, truncated replacement period or performance
  estimate is substituted.
- 2024H1: reuse the existing 4,368-candle M18.2 baseline. April–June 2024 remains
  observed OOS evidence and is never used as an optimization target.

The report accounts for every fresh predeclared period as either analyzed or
explicitly rejected. It cannot silently omit a period. Coverage and rejection
artifact hashes remain visible. The report status is
`COMPLETED_WITH_EXPLICIT_DATA_GAPS`, not proof of a complete three-period campaign.
All these periods are now observed; testing proposed variants requires new,
untouched, separately predeclared holdouts and any necessary acquisition approval.

## Exact local command

From `C:\Users\JOHN-KUNLE\OneDrive\Desktop\traderail\backend`, choose a NEW
output directory. This reads existing inputs only; it does not re-run backtests.

```powershell
.\.venv\Scripts\python.exe -m app.strategy_lab.historical.robustness `
  --protocol ..\docs\m18-3-protocol.json `
  --study 2024H1 ..\work\m18-2-binance-2024h1-normalized ..\work\m18-2-2024h1-final-research\report.json `
  --study 2022H1 ..\work\m18-3-binance-2022h1-normalized ..\work\m18-3-2022h1-baseline\report.json `
  --excluded-dataset ..\work\m18-3-binance-2023h1-normalized\dataset.json `
  --output ..\work\m18-3-review-copy
```

Completed recovery artifacts are in `work/m18-3-research-reviewed/research.json`
and `research.md`. Earlier development output remains preserved separately.
Parent reports, raw archives, normalization metadata and protocol must be retained
for artifact-level reproduction; numerical code and original reporting code are
identified separately, so metadata-version changes need not imply strategy changes.

## Attribution and measurement definitions

Trade net P&L is reconciled from original reference prices, actual modeled fill
prices, exact quantity, entry/exit fees, spread/slippage and canonical cash changes.
Completed trade legs must match exact recorded fill objects; each fill is used
once, and any open entry must explain the remaining position. Ending equity is
initial capital + completed net P&L + open unrealized P&L - recognized open entry
fee. Costs are not charged twice. Curves and drawdown must agree with baseline.

MFE/MAE use held-bar highs/lows versus the adverse entry fill, scaled by quantity.
Entry bar is included; the exit bar is excluded because the exit fills at its
open. Excursions are OHLC bounds, not attainable sale prices, and exclude
hypothetical exit fees. A losing trade with positive MFE is not proof of late exits.

Regimes are those recorded at entry/exit DECISION closes, not invented at fill
instants. Entry evidence and exit reason codes remain explicit. Group totals by
entry/exit regime, holding duration (<=24h, 24–72h, 72–168h, >168h), normalized ATR
bands anchored to canonical configuration floors, outcome and UTC entry/exit
month reconcile exactly to the completed-trade total. Holding duration and exit
regime are outcomes; post-hoc grouping does not provide an entry-time filter.

## UNKNOWN diagnostics

No alternate classifier is implemented. Cached canonical calculate_features and
measure_evidence reconstruct each rolling 100-candle latest observation. Canonical
classify_evidence verifies its measured candidate and base reasons against the
retained event. Original confirmation/retention and final regime labels remain
source evidence. Previous-regime details not retained in events are not invented.

Offline instrumentation extracts boolean expressions from the trusted canonical
classifier source for independent support counts. It never chooses a regime or
changes branch precedence. Failed independent predicates are not automatically
causal reasons for an UNKNOWN label. Missing comparisons remain unavailable.
Primary UNKNOWN reasons are disjoint confirmation-pending, conflicting-evidence,
insufficient-evidence or another recorded reason; supporting reason counts are
multi-label. A full 100-bar observation can be ambiguous without missing data.

Forward 24h/72h price moves are hindsight diagnostics bounded by each period end.
The protocol's 2%/5% absolute-move thresholds are not statistical significance
thresholds or profitable opportunities. Overlapping anchors are dependent. Greedy
non-overlapping anchors remove within-group overlap, not temporal or cross-group
dependence. No forward movement is fed into strategy decisions or optimization.

## Benchmark and statistical interpretation

Cash is constant quote capital. Full buy-and-hold uses canonical fee-inclusive
allocation and adverse entry pricing. Fixed-unit buy-and-hold uses the same
predeclared BTC quantity, only if its modeled commitment fits capital/ceiling.
Idle cash is included; returns use entire starting capital. All passive positions
are marked at final close without an invented exit fee.

The exposure-matched reference calibrates a passive budget to the strategy's
observed mean marked exposure, capped by capital/ceiling. This is EX POST and
cannot be deployed as if that mean were known in advance. It matches an average
amount, not time exposure, volatility, tails or trading skill. Realized matching
error and daily return dispersion are reported. A zero budget calls the existing
CASH benchmark: zero BTC, no fees, unchanged cash/equity and zero exposure.

Trade returns are net completed P&L / entry commitment including entry fee.
Mean/median/population dispersion are descriptive. Daily portfolio returns use
adjacent UTC closing days, excluding the first partial-day return. Trade counts
are observed, not an asserted independent sample size. No IID win-rate interval,
bootstrap, p-value or statistical significance is fabricated. Best/worst 1/3/5
concentration uses absolute P&L contributions, with deletion arithmetic labelled
as sensitivity rather than an executable trade-selection policy.

Cost multipliers 0, 0.5, 1 and 2 reprice the same entry/exit references and quantities
using shared pricing and cost metrics, including fee changes caused by adjusted
fill prices. They cover completed trades only. Funding, Risk, sizing and decision
admissibility are NOT re-evaluated; these are fixed-path sensitivity observations,
not counterfactual portfolio backtests or executable approvals.

## Historical Risk-replay feasibility contract

| Control | Evidence class | Requirement |
|---|---|---|
| Allocation ceiling | Modeled | Historical allocation/ownership not inferred; explicit denomination and fee-inclusive commitment |
| Position sizing | Modeled | Existing Risk sizing with explicit account/equity/exposure/invalidation inputs; no substitute sizing engine |
| Completed candles | Faithful from archive | Exact context, aligned completed intervals, continuity, OHLCV, provenance and 100-bar prefix |
| Operational freshness | Unavailable | Actual contemporaneous retrieval, provider quality and health evidence |
| Authorization age | Modeled only | Simulated timestamps can test canonical expiry rules, not prove an original approval |
| Six kill switches | Unavailable | Original USER/PORTFOLIO/STRATEGY/MARKET/DATA/SYSTEM attestations; missing is never inactive |
| Strategy lifecycle/health | Unavailable | Original activation/lifecycle/health evidence |
| Portfolio/drawdown | Modeled | Explicit genesis, cash, costs, positions and complete entry-to-current marks |
| Position lifecycle | Modeled | Exact simulated fills; repeated research is distinct from production first-close terminal behavior |
| Fixed-fill reconciliation | Faithful arithmetic | Existing Decimal accounting verifies simulated economics, not actual execution or authorization |

A future replay must supply every canonical input, distinguish modeled and observed
provenance, use the existing four-gate Risk engine, and return unavailable/blocked
when operational evidence is absent. It must never create production registry
records or silently assume healthy/inactive controls to obtain an approval.

## Completion and next milestone

Available-data research and implementation can complete with the explicit 2023
data gap. Robust profitable advantage and live-money readiness are NOT established.
M18.4 should choose one isolated hypothesis, freeze its variant and acceptance
criteria, predeclare new untouched holdouts, and separately resolve the source
completeness issue. Preserve the unchanged baseline and never tune on the prior
April–June 2024 OOS period. More trades or fewer UNKNOWN labels alone is not success.
