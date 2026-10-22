"""TradeRail-owned book snapshot binding. No frontend balances or hidden pricing."""
from dataclasses import dataclass,field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID
from sqlalchemy import select
from app.portfolio.models import PaperPortfolio
from app.risk.canonical import decimal,require
from ..canonical import seal
from .models import positions,accounting
from .contracts import PaperExecutionError


@dataclass(frozen=True,slots=True)
class PaperBookState:
    portfolio_id: UUID
    user_id: UUID
    environment: str
    base_currency: str
    cash_balance: Decimal
    status: str
    updated_at: datetime
    positions: tuple
    accounting_identities: tuple[str,...]
    state_identity: str=field(init=False)
    def __post_init__(self):
        decimal(self.cash_balance,'trusted cash')
        object.__setattr__(self,'positions',tuple(MappingProxyType(dict(row)) for row in self.positions))
        seal(self,'paper-book-state-1.0.0','state_identity')


def capture_book(connection,portfolio_id,*,lock=False):
    require(portfolio_id,UUID,'portfolio id')
    query=select(PaperPortfolio.__table__).where(PaperPortfolio.id==portfolio_id)
    if lock:query=query.with_for_update()
    row=connection.execute(query).mappings().one_or_none()
    if row is None:raise PaperExecutionError('Authoritative Paper Portfolio unavailable')
    query=select(positions).where(positions.c.portfolio_id==portfolio_id,positions.c.status=='OPEN').order_by(positions.c.position_identity)
    if lock:query=query.with_for_update()
    opened=tuple(dict(r) for r in connection.execute(query).mappings())
    history=tuple(connection.execute(select(accounting.c.result_identity).where(accounting.c.portfolio_id==portfolio_id).order_by(accounting.c.result_identity)).scalars())
    return PaperBookState(row['id'],row['user_id'],row['environment'],row['base_currency'],row['cash_balance'],row['status'],row['updated_at'],opened,history)
