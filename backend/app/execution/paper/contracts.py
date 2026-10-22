"""Explicit immutable PAPER commands and persisted economic results."""
from dataclasses import dataclass,field
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from app.risk.canonical import require,timestamp,decimal,identity
from ..canonical import seal
from ..request import ExecutionRequest
from ..paper_fill import PaperMarketEvidence,PaperFillAssumptions


class PaperExecutionError(ValueError):
    """Controlled fail-closed orchestration failure; never carries credentials."""


@dataclass(frozen=True,slots=True)
class PaperCommand:
    request: ExecutionRequest
    market: PaperMarketEvidence
    assumptions: PaperFillAssumptions
    executed_at: datetime
    command_identity: str=field(init=False)
    def __post_init__(self):
        require(self.request,ExecutionRequest,'request');require(self.market,PaperMarketEvidence,'market')
        require(self.assumptions,PaperFillAssumptions,'assumptions')
        object.__setattr__(self,'executed_at',timestamp(self.executed_at))
        seal(self,'paper-command-1.0.0','command_identity')


@dataclass(frozen=True,slots=True)
class PaperEconomicResult:
    request_identity: str
    command_identity: str
    intent_identity: str
    decision_identity: str
    order_identity: str
    fill_identity: str
    before_state_identity: str
    position_id: UUID
    action: str
    quantity: Decimal
    execution_price: Decimal
    gross_notional: Decimal
    fee: Decimal
    cash_before: Decimal
    cash_after: Decimal
    entry_cost_basis: Decimal | None
    entry_fee_attributable: Decimal | None
    gross_realized_pnl: Decimal | None
    net_realized_pnl: Decimal | None
    executed_at: datetime
    result_identity: str=field(init=False)
    def __post_init__(self):
        for name in ('request_identity','command_identity','intent_identity','decision_identity','order_identity','fill_identity','before_state_identity'):
            identity(getattr(self,name),name)
        require(self.position_id,UUID,'position id')
        if self.action not in ('ENTER_LONG','EXIT_LONG'):raise PaperExecutionError('Unsupported PAPER action')
        for name in ('quantity','execution_price','gross_notional','fee','cash_before','cash_after'):
            decimal(getattr(self,name),name,positive=name in ('quantity','execution_price','gross_notional'))
        realized=(self.entry_cost_basis,self.entry_fee_attributable,self.gross_realized_pnl,self.net_realized_pnl)
        if self.action=='ENTER_LONG' and any(x is not None for x in realized):raise PaperExecutionError('Entry has no realized outcome')
        if self.action=='EXIT_LONG':
            if any(type(x) is not Decimal or not x.is_finite() for x in realized):raise PaperExecutionError('Close requires finite exact realized evidence')
            decimal(self.entry_cost_basis,'entry cost basis',positive=True);decimal(self.entry_fee_attributable,'entry fee')
        object.__setattr__(self,'executed_at',timestamp(self.executed_at))
        seal(self,'paper-economic-result-1.0.0','result_identity')


def restore_result(wire):
    # Restores the immutable committed economic fact, never re-prices a retry.
    args=dict(wire);expected=args.pop('result_identity')
    for name in ('quantity','execution_price','gross_notional','fee','cash_before','cash_after','entry_cost_basis','entry_fee_attributable','gross_realized_pnl','net_realized_pnl'):
        args[name]=None if args[name] is None else Decimal(args[name])
    args['position_id']=UUID(args['position_id']);args['executed_at']=datetime.fromisoformat(args['executed_at'].replace('Z','+00:00'))
    result=PaperEconomicResult(**args)
    if result.result_identity!=expected:raise PaperExecutionError('Persisted economic receipt identity mismatch')
    return result
