"""Read-only persisted runner work and portfolio context; never runner liveness."""
from typing import Literal
from uuid import UUID
from pydantic import Field
from sqlalchemy import select
from app.mandate_eligibility.contracts import Frozen
from app.execution.api.schemas import Financial, UTCDate
from app.portfolio.models import PaperPortfolio
from app.mandate_runner.models import work
from app.mandates.service import owned
from app.mandates.domain import MandateError


class Evaluation(Frozen):
    work_id: UUID
    state: Literal['PENDING','RETRY_WAIT','COMPLETED','SKIPPED','RECONCILIATION_REQUIRED']
    category: Literal['PENDING','NO_TRADE','ENTRY_EXECUTED','POSITION_HELD','POSITION_CLOSED',
        'RISK_REJECTED','SKIPPED','FAILURE','COMPLETED']
    outcome_code: str | None = Field(default=None,pattern=r'^[A-Z][A-Z0-9_]{0,63}$')
    reason_code: str | None = Field(default=None,pattern=r'^[A-Z][A-Z0-9_]{0,63}$')
    attempts: int = Field(ge=0)
    recorded_at: UTCDate
    last_attempt_at: UTCDate | None
    retry_at: UTCDate | None
    fresh_until: UTCDate


class Activity(Frozen):
    scope: Literal['PAPER_ACTIVITY_READ_ONLY'] = 'PAPER_ACTIVITY_READ_ONLY'
    mandate_identity: UUID
    environment: Literal['PAPER'] = 'PAPER'
    currency: Literal['USDT'] = 'USDT'
    portfolio_cash: Financial | None
    portfolio_state: Literal['AVAILABLE','UNAVAILABLE']
    latest_evaluation: Evaluation | None
    runner_health: Literal['UNVERIFIED'] = 'UNVERIFIED'
    runner_health_reason: Literal['HEARTBEAT_NOT_PERSISTED'] = 'HEARTBEAT_NOT_PERSISTED'
    observed_at: UTCDate


def evaluation(row):
    state=row['state'];outcome=row['outcome_code']
    category=('PENDING' if state in ('PENDING','RETRY_WAIT') else 'FAILURE' if state=='RECONCILIATION_REQUIRED'
        else 'SKIPPED' if state=='SKIPPED' else {
            'NO_TRADE':'NO_TRADE','PAPER_EXECUTED':'ENTRY_EXECUTED','POSITION_HELD':'POSITION_HELD',
            'POSITION_CLOSED':'POSITION_CLOSED','RISK_REJECTED':'RISK_REJECTED',
            'EXIT_RISK_REJECTED':'RISK_REJECTED'}.get(outcome,'COMPLETED'))
    return Evaluation(work_id=row['work_id'],state=state,category=category,
        outcome_code=outcome,reason_code=row['reason_code'],attempts=row['attempts'],
        recorded_at=row['updated_at'],last_attempt_at=row['last_attempt_at'],
        retry_at=row['retry_at'],fresh_until=row['fresh_until'])


def inspect_activity(db,user_id,mandate_id,*,at):
    if db.in_transaction():raise ValueError('Unused reporting session required')
    c=db.connection(execution_options={'isolation_level':'REPEATABLE READ','postgresql_readonly':True})
    mandate=owned(db,user_id,mandate_id)
    if mandate.environment!='PAPER' or mandate.allocation_currency!='USDT':
        raise MandateError('paper_activity_unavailable','PAPER USDT reporting only',409)
    portfolio=c.execute(select(PaperPortfolio.__table__).where(PaperPortfolio.user_id==user_id,
        PaperPortfolio.environment=='PAPER')).mappings().one_or_none()
    usable=portfolio is not None and portfolio['base_currency']=='USDT' and portfolio['status']=='active'
    row=c.execute(select(work).where(work.c.mandate_id==mandate_id)
        .order_by(work.c.created_at.desc(),work.c.work_id.desc()).limit(1)).mappings().one_or_none()
    return Activity(mandate_identity=mandate_id,portfolio_cash=portfolio['cash_balance'] if usable else None,
        portfolio_state='AVAILABLE' if usable else 'UNAVAILABLE',
        latest_evaluation=evaluation(row) if row is not None else None,observed_at=at)
