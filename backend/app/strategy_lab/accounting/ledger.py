"""Private run-local ledger; output observations are immutable."""
from app.strategies.decisions import DecisionType
from app.strategies.inputs import PositionState
from app.strategy_lab.costs import CostBreakdown
from app.strategy_lab.numeric import ZERO, difference, exact_product, exact_sum
from app.strategy_lab.replay.transitions import transition_state
from app.strategy_lab import metrics
from .performance import trade_observations
from .trades import complete_trade


class Ledger:
    def __init__(self, initial_capital):
        self.initial = self.cash = initial_capital
        self.reserved = self.proceeds = ZERO
        self.entry = None
        self.state = PositionState.CASH
        self.trades = []
        self.fees = self.spread = self.slippage = ZERO

    def apply(self, fill):
        if fill.position_before is not self.state:
            raise ValueError("Filled position state mismatch")
        following = transition_state(self.state, fill.action)
        if fill.position_after is not following:
            raise ValueError("Invalid filled transition")
        p = fill.pricing
        if fill.action in (DecisionType.ENTER_LONG, DecisionType.ENTER_SHORT):
            required = exact_sum((p.fill_notional if fill.action is DecisionType.ENTER_LONG else p.reference_notional, p.fee_amount))
            if required > self.cash:
                raise ValueError("Insufficient unleveraged capital; quantity will not be resized")
            self.cash = difference(self.cash, required)
            self.entry = fill
            if fill.action is DecisionType.ENTER_SHORT:
                self.reserved, self.proceeds = p.reference_notional, p.fill_notional
        else:
            if self.entry is None or self.entry.quantity != fill.quantity:
                raise ValueError("Exit lacks matching entry quantity and provenance")
            trade = complete_trade(self.entry, fill)
            if fill.action is DecisionType.EXIT_LONG:
                self.cash = exact_sum((self.cash, p.fill_notional, p.fee_amount.copy_negate()))
            else:
                self.cash = exact_sum((self.cash, self.reserved, self.proceeds,
                    p.fill_notional.copy_negate(), p.fee_amount.copy_negate()))
            if self.cash < ZERO:
                raise ValueError("Insufficient capital to settle short cover without borrowing")
            self.trades.append(trade)
            self.entry = None
            self.reserved = self.proceeds = ZERO
        self.fees = exact_sum((self.fees, p.fee_amount))
        self.spread = exact_sum((self.spread, p.spread_cost))
        self.slippage = exact_sum((self.slippage, p.slippage_cost))
        self.state = following
        # Check entry collateral immediately at its causal economic reference, not a future close.
        if fill.action is DecisionType.ENTER_SHORT:
            self.mark(p.reference_open)

    def mark(self, price):
        totals = metrics.realized_pnl_aggregation(trade_observations(self.trades))
        # Fees are realized when incurred; open entry spread/slippage remain in mark-to-market.
        open_fee = self.entry.pricing.fee_amount if self.entry else ZERO
        if self.fees != exact_sum((totals.fees, open_fee)):
            raise ValueError("Fee ledger does not reconcile with completed trades and open entry")
        realized = difference(totals.net_pnl, open_fee)
        capital = exact_sum((self.initial, realized))
        quantity = self.entry.quantity if self.entry else ZERO
        market_value = exact_product(quantity, price)
        if self.state is PositionState.LONG:
            unrealized = exact_product(quantity, difference(price, self.entry.pricing.fill_price))
            equity = exact_sum((self.cash, market_value))
        elif self.state is PositionState.SHORT:
            unrealized = exact_product(quantity, difference(self.entry.pricing.fill_price, price))
            equity = exact_sum((self.cash, self.reserved, self.proceeds, market_value.copy_negate()))
            if market_value > equity:
                raise ValueError("Short collateral exceeds unleveraged one-times-equity exposure limit")
        else:
            unrealized, equity = ZERO, self.cash
        if self.cash < ZERO or equity < ZERO:
            raise ValueError("Negative cash/equity would require unsupported borrowing")
        if equity != exact_sum((capital, unrealized)):
            raise ValueError("Cash, realized capital and mark-to-market fail exact reconciliation")
        return totals, realized, capital, quantity, unrealized, equity

    @property
    def costs(self):
        return CostBreakdown(self.fees, self.spread, self.slippage)
