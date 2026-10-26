"""Immutable reporting contracts. Amounts are quote-currency Decimal strings."""
from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from app.mandate_eligibility.contracts import Frozen
from app.execution.api.schemas import Financial, UTCDate


class Valuation(Frozen):
    state: Literal['FRESH', 'STALE', 'UNAVAILABLE', 'NOT_REQUIRED']
    price: Financial | None = None
    priced_at: UTCDate | None = None
    valid_until: UTCDate | None = None
    source: str | None = None
    evidence_identity: str | None = None
    reason_codes: tuple[str, ...] = ()


    @model_validator(mode='after')
    def mark_availability(self):
        if self.state=='FRESH':
            if (self.price is None or not self.price.is_finite() or self.price<=0
                or any(x is None for x in (self.priced_at,self.valid_until,self.source,self.evidence_identity))):
                raise ValueError('Fresh mark requires positive price and complete provenance')
        elif self.price is not None:
            raise ValueError('Unavailable/stale marks cannot supply a usable price')
        return self


class Leg(Frozen):
    intent_identity: str
    decision_identity: str
    strategy_decision_identity: str
    strategy_input_identity: str
    strategy_reason_codes: tuple[str, ...] | None
    risk_outcome: Literal['APPROVED'] = 'APPROVED'
    registry_identity: str
    authorization_identity: str
    request_identity: str
    client_order_identity: str
    order_identity: str
    fill_identity: str
    accounting_identity: str
    market_evidence_identity: str
    executed_at: UTCDate
    quantity: Financial
    reference_price: Financial
    execution_price: Financial
    reference_notional: Financial
    gross_notional: Financial
    fee: Financial


class Trade(Frozen):
    position_id: UUID
    position_identity: str
    instrument: str
    instrument_id: UUID
    strategy_family: str
    strategy_version: str
    strategy_configuration_identity: str
    direction: Literal['LONG'] = 'LONG'
    status: Literal['OPEN', 'CLOSED']
    quantity: Financial
    allocation_at_entry: Financial
    eligibility_identity: str
    strategy_observation_identity: str
    entry: Leg
    exit: Leg | None = None
    gross_realized_pnl: Financial | None = None
    net_realized_pnl: Financial | None = None


    @model_validator(mode='after')
    def lifecycle(self):
        if self.status=='OPEN':
            if self.quantity<=0 or any(x is not None for x in (self.exit,self.gross_realized_pnl,self.net_realized_pnl)):
                raise ValueError('Open trade cannot claim a realized close')
        elif self.quantity!=0 or any(x is None for x in (self.exit,self.gross_realized_pnl,self.net_realized_pnl)):
            raise ValueError('Closed trade requires its canonical close')
        return self


class Performance(Frozen):
    scope: Literal['MANDATE_PERFORMANCE_READ_ONLY'] = 'MANDATE_PERFORMANCE_READ_ONLY'
    projection_version: Literal['mandate-performance-v1'] = 'mandate-performance-v1'
    projection_identity: str
    mandate_identity: UUID
    environment: Literal['PAPER'] = 'PAPER'
    currency: Literal['USDT'] = 'USDT'
    mandate_status: str
    status: Literal['DRAFT', 'PAUSED', 'RETIRED', 'NO_TRADES_YET', 'POSITION_OPEN', 'CLOSED', 'UNAVAILABLE']
    financial_state: Literal['AVAILABLE', 'UNAVAILABLE']
    allocation: Financial
    performance_allocation: Financial | None = None
    allocation_changed: bool = False
    capital_semantics: Literal['ALLOCATION_CONTEXT_NOT_WALLET'] = 'ALLOCATION_CONTEXT_NOT_WALLET'
    capital_exposed: Financial | None = None
    capital_committed: Financial | None = None
    available_unallocated: Financial | None = None
    allocation_shortfall: Financial | None = None
    realized_pnl: Financial | None = None
    gross_realized_pnl: Financial | None = None
    open_entry_fees: Financial | None = None
    marked_exposure: Financial | None = None
    price_unrealized_pnl: Financial | None = None
    unrealized_pnl: Financial | None = None
    current_mandate_value: Financial | None = None
    return_percentage: Financial | None = None
    valuation: Valuation
    drawdown_state: Literal['UNAVAILABLE'] = 'UNAVAILABLE'
    drawdown: None = None
    drawdown_reason: Literal['MANDATE_EQUITY_HISTORY_NOT_PERSISTED'] = 'MANDATE_EQUITY_HISTORY_NOT_PERSISTED'
    trade_count: int | None = Field(default=None, ge=0)
    completed_trade_count: int | None = Field(default=None, ge=0)
    trades: tuple[Trade, ...] = ()
    reason_codes: tuple[str, ...] = ()
    evaluated_at: UTCDate


class ReportingUnavailable(ValueError):
    """Unverifiable attribution must never become zero performance."""
