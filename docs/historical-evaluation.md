# M18.1 local historical evaluation

**HISTORICAL SIMULATION — NOT LIVE OR FORWARD PERFORMANCE**

This is an offline composition of the existing Strategy Lab dataset, canonical
intelligence/regime, Adaptive Trend 1.0.0, causal replay, next-open execution,
Decimal accounting, metrics and chronological IS/OOS engines. There is no HTTP
endpoint, database, dotenv loader, provider fetch, Risk authorization or production
execution. It does not control or observe the separate autonomous runner.

## Run

From `backend`, using an explicitly approved LOCAL input file:

```powershell
.\.venv\Scripts\python.exe -m app.strategy_lab.historical `
  --input ..\work\m18-1-long-profit-input.json `
  --output ..\work\m18-1-long-profit-report.json `
  --initial-capital 10000 --quantity 1
```

Output must not already exist. A failed run returns nonzero with a sanitized
category. The CLI never overwrites an existing report. Quantity is explicit BTC
research units, not a mandate allocation or Risk-sized exposure. Unfunded runs
fail instead of borrowing or resizing. Both capital and quantity are positive
Decimal strings. The local input is limited to 2 MB and 300 hourly candles, matching
the current bounded research interface; no hidden history is loaded.

## Input contract

`traderail-historical-input-v1` requires provenance, source_kind
(`SYNTHETIC_FIXTURE` or `LOCAL_ARCHIVE_UNVERIFIED`), instrument_id, instrument
`BTC/USDT`, provider `BINANCE`, timeframe `1h`, warmup_start, evaluation_start,
evaluation_end (exclusive), as_of (explicit archival cutoff), split_at, and candles.
Candle fields: open_time, close_time, retrieved_at, open/high/low/close/volume.
All timestamps must have timezones; financial values must be finite Decimal
strings. No unknown fields, floating-point prices, incomplete bars, gaps,
duplicates, invalid OHLCV or future-to-cutoff retrievals are accepted.
The provenance is a caller declaration, not verification of market authenticity.

## Timing and split semantics

Warm-up precedes evaluation and must satisfy existing computed analytical history.
Each decision uses only its chronological prefix, the canonical strategy and
position state. Decision timestamp is the candle close; observation cutoff is the
maximum of prefix completion and original retrieval timestamps. Retrieval dates
are never rewritten to suggest earlier availability. The existing research engine
fills at the following candle open with adverse spread/slippage, and records
end-of-data unfilled actions. Open positions are marked, never forcibly closed.

The split is fixed before evaluation. In-sample truncates at split_at; OOS uses
prior candles for analytical warm-up only and starts independently in CASH with
its own initial capital. No trade/pending order/P&L is carried across the split.
The full-period result is separate from paired results; do not add paired cash
or returns together. No optimization or configuration search occurs.

## Economics and metrics

The existing `paper-costs-v1` fee/spread/slippage rates are referenced, not copied.
This research adapter currently requires symmetric entry/exit rates and refuses
an unsupported future asymmetric policy. It reuses next-open research execution,
not production close-price execution. Legacy research can simulate LONG and SHORT;
this grants no production SHORT or LIVE capability and is not LONG_ONLY mandate parity.

Cash, fills, completed trades, fees, spread, slippage, equity and P&L all come from
existing research accounting. Fees/spread/slippage are not charged a second time.
`metrics.net_realized_pnl` describes completed trades; equity rows separately
preserve the ledger's incurred-entry-fee recognition for open positions.
Unrealized P&L excludes hypothetical future exit fees under the existing convention.
Gross profit/loss aggregate positive/absolute-negative reference-price trade P&L;
profit factor, win rate and expectancy use completed NET trade outcomes.

Returns, win rate, drawdown and utilization are fractions; exposure_percentage is
percent. Capital utilization is gross marked exposure / initial capital, averaged
over uniform hourly marks, not wallet cash or a Risk budget. Drawdown includes
starting capital before the first mark. All amounts serialize as Decimal strings.
Durations serialize as integer microseconds (averages as Decimal strings).
Annualized return is explicitly unavailable: at most 300 hours does not establish
a defensible annual result. Undefined metrics remain null with reason codes.
Even defined small-sample metrics are descriptive, not statistical validation.

Every report includes dataset/configuration/result fingerprints, strategy identity,
source declaration, coverage, costs, trades/fills, decisions, equity/drawdown,
open position, unfilled actions, full/IS/OOS metrics and explicit warnings.
Reports are deterministic under equivalent input/configuration and contain no
wall-clock creation time. Synthetic inputs remain marked synthetic everywhere
through the root label/source_kind/warnings; they are correctness examples only.

## Existing example

`work/m18-1-long-profit-input.json` and `work/m18-1-long-profit-report.json` are
local ignored artifacts generated exclusively from the existing
`backend/tests/test_integrated_9c_audit.py::frozen_dataset('long_profit')` fixture.
They are NOT evidence of real market profitability. No new market data was fetched.

## Limitations / M18.2 candidates

Explicitly approved real archival imports with verifiable provenance, longer-horizon
scalability, deliberate LONG/CASH and rolling-window parity studies, and an agreed
annualization/statistical sufficiency policy. These are not implemented here.
Risk-gated sizing and live/forward validation remain separate responsibilities.
