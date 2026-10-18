"""Causal quote-currency accounting over frozen replay and research fills."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from app.market_intelligence.types import TIMEFRAME_SECONDS
from app.strategies.inputs import PositionState
from app.strategy_lab.dataset import ResearchDataset, create_dataset
from app.strategy_lab.numeric import require_decimal
from app.strategy_lab.replay.types import ReplayResult
from app.strategy_lab.execution.types import ExecutionSimulation
from app.strategy_lab.execution.engine import simulate_execution
from app.strategy_lab.execution.config import HistoricalExecutionConfiguration, identity
from .config import ACCOUNTING_CONFIG
from .ledger import Ledger
from .performance import incurred_costs, summarize
from .types import EquityObservation, OpenPosition, ResearchResult


def account_simulation(dataset: ResearchDataset, replay: ReplayResult, execution: ExecutionSimulation,
                       *, initial_capital: Decimal, as_of: datetime) -> ResearchResult:
    """Start in CASH, apply fills at their historical opens, mark each evaluation close.

    Inputs and all prior modules remain untouched. Invalid funding is rejected, not resized.
    """
    require_decimal(initial_capital, "initial_capital", nonnegative=True)
    if not isinstance(execution, ExecutionSimulation) or not isinstance(execution.configuration, HistoricalExecutionConfiguration):
        raise ValueError("An immutable historical execution simulation is required")
    # Reuse Part 2's validation/pricing exactly; never recompute strategies or analytics.
    expected = simulate_execution(dataset, replay, quantity=execution.quantity,
        cost_model=execution.configuration.cost_model, as_of=as_of)
    if expected != execution:
        raise ValueError("Execution artifacts do not match the frozen replay and cost configuration")
    if execution.initial_state is not PositionState.CASH:
        raise ValueError("Accounting requires an explicit CASH start; no historical entry basis is invented")
    duration = timedelta(seconds=TIMEFRAME_SECONDS[dataset.context.timeframe])
    ledger, curve, exposed, cursor = Ledger(initial_capital), [], timedelta(0), 0
    for index in range(replay.warmup_count, dataset.candle_count):
        candle, applied = dataset.candles[index], []
        while cursor < len(execution.fills) and execution.fills[cursor].eligible_fill_timestamp == candle.open_time:
            fill = execution.fills[cursor]
            ledger.apply(fill)
            applied.append(fill.fill_identity)
            cursor += 1
        totals, realized, capital, quantity, unrealized, equity = ledger.mark(candle.close)
        if ledger.state is not PositionState.CASH:
            exposed += duration
        prefix = create_dataset(dataset.context, dataset.candles[:index+1],
            warmup_start=dataset.warmup_start, evaluation_start=dataset.evaluation_start,
            evaluation_end=candle.open_time+duration, as_of=as_of)
        payload = {"accounting": ACCOUNTING_CONFIG.configuration_identity,
            "dataset_prefix": prefix.dataset_fingerprint, "initial_capital": initial_capital,
            "strategy_configuration": replay.strategy_definition.configuration_identity,
            "execution_configuration": execution.configuration.configuration_identity,
            "previous_observation": curve[-1].observation_identity if curve else None,
            "timestamp": candle.close_time, "cash": ledger.cash, "reserved_capital": ledger.reserved,
            "restricted_short_proceeds": ledger.proceeds, "realized_capital": capital,
            "gross_realized_pnl": totals.gross_pnl, "realized_pnl": realized,
            "completed_trade_net_pnl": totals.net_pnl, "unrealized_pnl": unrealized,
            "total_equity": equity, "incurred_costs": asdict(ledger.costs),
            "position_state": ledger.state, "quantity": quantity, "mark_price": candle.close,
            "applied_fills": tuple(applied)}
        observation_id = identity("strategy-lab.equity-observation/v1", payload)
        curve.append(EquityObservation(candle.close_time.astimezone(timezone.utc), dataset.context,
            ledger.cash, ledger.reserved, ledger.proceeds, capital, totals.gross_pnl, realized, totals.net_pnl,
            unrealized, equity, ledger.costs, ledger.state, quantity, candle.close,
            candle.open_time.astimezone(timezone.utc), ACCOUNTING_CONFIG.marking,
            prefix.dataset_fingerprint, tuple(applied), observation_id))
    if cursor != len(execution.fills) or ledger.state is not execution.final_filled_state:
        raise ValueError("Fill chronology or final filled-state mismatch")
    trades = tuple(ledger.trades)
    costs = incurred_costs(execution.fills)
    if costs != ledger.costs:
        raise ValueError("Incurred ledger costs do not reconcile with shared cost metrics")
    performance = summarize(initial_capital, trades, execution.fills, tuple(curve), exposed,
        dataset.evaluation_end-dataset.evaluation_start)
    open_position = None
    if ledger.entry:
        entry, last = ledger.entry, curve[-1]
        open_position = OpenPosition(ledger.state, entry.quantity, entry.pricing.fill_price,
            entry.pricing.reference_open, entry, entry.pricing.fee_amount, last.mark_price, last.timestamp,
            last.unrealized_pnl, ledger.reserved, ledger.proceeds)
    result_id = identity("strategy-lab.research-result/v1", {
        "dataset": dataset.dataset_fingerprint, "replay": replay.replay_identity,
        "execution": execution.simulation_identity, "accounting": ACCOUNTING_CONFIG.configuration_identity,
        "initial_capital": initial_capital, "trades": [trade.trade_identity for trade in trades],
        "equity": [row.observation_identity for row in curve], "performance": asdict(performance),
        "open_entry_fill": ledger.entry.fill_identity if ledger.entry else None})
    return ResearchResult(dataset.dataset_fingerprint, dataset.context, ACCOUNTING_CONFIG, initial_capital,
        replay, execution, trades, open_position, tuple(curve), totals, costs, performance, result_id)
