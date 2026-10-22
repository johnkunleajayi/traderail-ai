"""Full LONG close accounting; no quantity sizing or performance statistics."""
from decimal import Decimal
from app.strategy_lab.numeric import exact_sum,difference
from .contracts import PaperExecutionError


def cash_after(cash,leg):
    value=exact_sum((cash,leg.net_cash_effect))
    if value<0:raise PaperExecutionError('Insufficient PAPER cash; borrowing/resizing is forbidden')
    return value


def full_close(position,leg):
    if position['quantity']!=leg.quantity:raise PaperExecutionError('Full close only; oversell and partial reduction unsupported')
    gross=difference(leg.reference_notional,position['entry_reference_notional'])
    net=exact_sum((leg.gross_notional,position['entry_cost_basis'].copy_negate(),position['entry_fee'].copy_negate(),leg.fee.copy_negate()))
    costs=exact_sum((position['entry_spread_cost'],position['entry_slippage_cost'],leg.spread_cost,leg.slippage_cost,position['entry_fee'],leg.fee))
    if difference(gross,costs)!=net:raise PaperExecutionError('LONG close cost reconciliation failed')
    return gross,net
