"""Derived mandate capital/performance, never a ledger or trading control."""
from decimal import Decimal
from pydantic import BaseModel
from app.mandates.service import owned
from app.mandates.domain import MandateError
from app.mandates.schemas import MandateResponse
from app.mandate_eligibility.contracts import canonical_identity
from app.risk.canonical import timestamp
from app.strategy_lab.numeric import exact_sum, exact_product, difference, ratio
from app.execution.registry.repository import RegistryUnavailable, AuthorizationUnavailable
from .contracts import Performance, Valuation, ReportingUnavailable
from .evidence import load_trades
from .valuation import current_mark

ZERO=Decimal('0')


def project(c,mandate,user_id,at):
    at=timestamp(at)
    # This validates stored intent, including a finite positive Decimal allocation.
    intent=MandateResponse.model_validate(mandate)
    if intent.user_id!=user_id:
        raise MandateError('mandate_not_found','Mandate not found',404)
    base=dict(mandate_identity=intent.id,mandate_status=intent.status.value,allocation=intent.capital_allocation,
        evaluated_at=at)
    try:
        trades=load_trades(c,mandate,user_id)
        if any(t.entry.executed_at>at or (t.exit is not None and t.exit.executed_at>at) for t in trades):
            raise ReportingUnavailable('Future economic facts cannot form an earlier valuation')
    except (ReportingUnavailable,RegistryUnavailable,AuthorizationUnavailable,ValueError,KeyError,TypeError,ArithmeticError):
        return finish(base,status='UNAVAILABLE',financial_state='UNAVAILABLE',
            valuation=Valuation(state='UNAVAILABLE',reason_codes=('ATTRIBUTION_RECONCILIATION_REQUIRED',)),
            reason_codes=('ATTRIBUTION_RECONCILIATION_REQUIRED',))
    opened=tuple(t for t in trades if t.status=='OPEN')
    closed=tuple(t for t in trades if t.status=='CLOSED')
    exposed=exact_sum(t.entry.gross_notional for t in opened)
    open_fees=exact_sum(t.entry.fee for t in opened)
    commitment=exact_sum((exposed,open_fees))
    unused=difference(intent.capital_allocation,commitment)
    realized=exact_sum(t.net_realized_pnl for t in closed)
    gross=exact_sum(t.gross_realized_pnl for t in closed)
    bases={t.allocation_at_entry for t in trades}
    baseline=next(iter(bases)) if len(bases)==1 else intent.capital_allocation if not bases else None
    valuation=current_mark(c,opened,at) if opened else Valuation(state='NOT_REQUIRED')
    mark=price_pnl=unrealized=None
    if not opened:
        mark=price_pnl=unrealized=ZERO
    elif valuation.state=='FRESH':
        mark=exact_sum(exact_product(t.quantity,valuation.price) for t in opened)
        # Matches existing LONG marked-equity semantics: entry fee is incurred,
        # no hypothetical future exit fee/spread/slippage is charged at a mark.
        price_pnl=difference(mark,exposed)
        unrealized=difference(price_pnl,open_fees)
    value=exact_sum((baseline,realized,unrealized)) if baseline is not None and unrealized is not None else None
    percentage=exact_product(ratio(difference(value,baseline),baseline),Decimal('100')) if value is not None else None
    status=intent.status.value if intent.status.value in ('DRAFT','PAUSED','RETIRED') else (
        'POSITION_OPEN' if opened else 'CLOSED' if closed else 'NO_TRADES_YET')
    return finish(base,status=status,financial_state='AVAILABLE',performance_allocation=baseline,
        allocation_changed=any(t.allocation_at_entry!=intent.capital_allocation for t in trades),
        capital_exposed=exposed,capital_committed=commitment,open_entry_fees=open_fees,
        available_unallocated=max(ZERO,unused),allocation_shortfall=max(ZERO,unused.copy_negate()),
        realized_pnl=realized,gross_realized_pnl=gross,marked_exposure=mark,price_unrealized_pnl=price_pnl,
        unrealized_pnl=unrealized,current_mandate_value=value,return_percentage=percentage,valuation=valuation,
        trade_count=len(trades),completed_trade_count=len(closed),trades=trades,
        reason_codes=('ALLOCATION_BASELINE_HISTORY_AMBIGUOUS',) if baseline is None else ())


def finish(base,**fields):
    payload={**base,**fields}
    return Performance(projection_identity=canonical_identity('performance',raw_values(payload)),**payload)


def inspect(db,user_id,mandate_id,*,evaluated_at):
    # Acquire before the first ORM query. A database-enforced READ ONLY repeatable
    # snapshot prevents mixed entry/close facts across multiple SELECT statements.
    if db.in_transaction():
        raise ReportingUnavailable('An unused reporting session is required')
    connection=db.connection(execution_options={'isolation_level':'REPEATABLE READ','postgresql_readonly':True})
    mandate=owned(db,user_id,mandate_id)
    return project(connection,mandate,user_id,evaluated_at)


def raw_values(value):
    # Pydantic Financial serializers produce strings even in model_dump(python).
    # Hash original typed values so equivalent Decimal representations stay equal.
    if isinstance(value,BaseModel):
        return {name:raw_values(getattr(value,name)) for name in type(value).model_fields}
    if isinstance(value,dict):
        return {key:raw_values(item) for key,item in value.items()}
    if isinstance(value,(tuple,list)):
        return tuple(raw_values(item) for item in value)
    return value
