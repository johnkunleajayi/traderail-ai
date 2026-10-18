"""Matched-fill economic P&L with exact cost reconciliation."""
from dataclasses import asdict
from app.strategies.inputs import PositionState
from app.strategies.decisions import DecisionType
from app.strategy_lab.costs import CostBreakdown
from app.strategy_lab.metrics import RealizedTrade
from app.strategy_lab.numeric import difference, exact_product, exact_sum
from app.strategy_lab.execution.config import identity
from app.strategy_lab.execution.types import SimulatedFill
from .config import ACCOUNTING_CONFIG
from .types import CompletedTrade


def complete_trade(entry: SimulatedFill, exit: SimulatedFill) -> CompletedTrade:
    if not isinstance(entry, SimulatedFill) or not isinstance(exit, SimulatedFill):
        raise ValueError("A matched pair of simulated fills is required")
    if (entry.action, exit.action) == (DecisionType.ENTER_LONG, DecisionType.EXIT_LONG):
        direction = PositionState.LONG
        reference_move = difference(exit.pricing.reference_open, entry.pricing.reference_open)
        fill_move = difference(exit.pricing.fill_price, entry.pricing.fill_price)
    elif (entry.action, exit.action) == (DecisionType.ENTER_SHORT, DecisionType.EXIT_SHORT):
        direction = PositionState.SHORT
        reference_move = difference(entry.pricing.reference_open, exit.pricing.reference_open)
        fill_move = difference(entry.pricing.fill_price, exit.pricing.fill_price)
    else:
        raise ValueError("Entry and exit do not describe a matching direction")
    if (entry.quantity != exit.quantity or entry.context != exit.context
        or entry.strategy_configuration_identity != exit.strategy_configuration_identity
        or (entry.strategy_family,entry.strategy_version) != (exit.strategy_family,exit.strategy_version)
        or entry.configuration != exit.configuration or entry.eligible_fill_timestamp >= exit.eligible_fill_timestamp):
        raise ValueError("Trade fill context, configuration, quantity or chronology mismatch")
    gross = exact_product(reference_move, entry.quantity)
    costs = CostBreakdown(exact_sum((entry.pricing.fee_amount, exit.pricing.fee_amount)),
        exact_sum((entry.pricing.spread_cost, exit.pricing.spread_cost)),
        exact_sum((entry.pricing.slippage_cost, exit.pricing.slippage_cost)))
    outcome = RealizedTrade(gross, costs)
    if outcome.net_pnl != difference(exact_product(fill_move, entry.quantity), costs.fees):
        raise ValueError("Reference P&L, adverse fills and costs fail exact reconciliation")
    trade_id = identity("strategy-lab.completed-trade/v1", {
        "accounting": ACCOUNTING_CONFIG.configuration_identity,
        "entry_fill": entry.fill_identity, "exit_fill": exit.fill_identity,
        "gross_pnl": gross, "costs": asdict(costs), "net_pnl": outcome.net_pnl})
    return CompletedTrade(entry, exit, direction, entry.quantity,
        entry.eligible_fill_timestamp, exit.eligible_fill_timestamp,
        entry.pricing.reference_open, exit.pricing.reference_open,
        entry.pricing.fill_price, exit.pricing.fill_price, gross, costs, outcome.net_pnl,
        exit.dataset_prefix_fingerprint, entry.strategy_family, entry.strategy_version,
        entry.strategy_configuration_identity, entry.configuration.configuration_identity, trade_id)
