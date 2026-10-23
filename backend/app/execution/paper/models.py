"""PAPER LONG-only positions and append-only economic receipts."""
from sqlalchemy import Table,Column,String,DateTime,Numeric,Text,CheckConstraint,ForeignKey,Index
from sqlalchemy.dialects.postgresql import UUID,JSONB
from app.db.base import Base
from app.portfolio.models import PaperPortfolio
from app.users.models import User
from app.instruments.models import Instrument
from ..persistence import models as execution_models


def key(name,**kwargs):return Column(name,String(71),nullable=False,**kwargs)
def money(name,nullable=False):return Column(name,Numeric(asdecimal=True),nullable=nullable)
def at(name,nullable=False):return Column(name,DateTime(timezone=True),nullable=nullable)

positions=Table('paper_positions',Base.metadata,
    Column('position_id',UUID(as_uuid=True),primary_key=True),key('position_identity',unique=True),
    Column('user_id',UUID(as_uuid=True),ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
    Column('portfolio_id',UUID(as_uuid=True),ForeignKey('paper_portfolios.id',ondelete='RESTRICT'),nullable=False),
    Column('instrument_id',UUID(as_uuid=True),ForeignKey('instruments.id',ondelete='RESTRICT'),nullable=False),
    Column('canonical_symbol',String(100),nullable=False),Column('environment',String(5),nullable=False),
    Column('direction',String(5),nullable=False),Column('currency',String(16),nullable=False),
    Column('strategy_family',String(64),nullable=False),Column('strategy_version',String(64),nullable=False),key('strategy_configuration_identity'),
    key('origin_intent_identity'),key('origin_decision_identity'),
    key('opening_request_identity',unique=True),key('opening_order_identity'),key('opening_fill_identity'),
    money('opening_quantity'),money('quantity'),money('average_entry_price'),money('entry_reference_notional'),
    money('entry_cost_basis'),money('entry_fee'),money('entry_spread_cost'),money('entry_slippage_cost'),
    Column('status',String(6),nullable=False),at('opened_at'),at('closed_at',True),
    Column('closing_request_identity',String(71),unique=True),
    CheckConstraint("environment='PAPER' AND direction='LONG' AND currency IN ('USD','USDT')",name='ck_paper_position_scope'),
    CheckConstraint("(status='OPEN' AND quantity>0 AND quantity=opening_quantity AND closed_at IS NULL AND closing_request_identity IS NULL) OR (status='CLOSED' AND quantity=0 AND closed_at IS NOT NULL AND closed_at>=opened_at AND closing_request_identity IS NOT NULL)",name='ck_paper_position_lifecycle'),
    *(CheckConstraint(f"{n} NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND {n} {'>' if n in ('opening_quantity','average_entry_price','entry_cost_basis','entry_reference_notional') else '>='} 0",name='ck_paper_pos_'+n)
      for n in ('opening_quantity','quantity','average_entry_price','entry_reference_notional','entry_cost_basis','entry_fee','entry_spread_cost','entry_slippage_cost')))
from sqlalchemy import ForeignKeyConstraint
for column,target in (('opening_request_identity','execution_requests.request_identity'),('opening_order_identity','execution_orders.order_identity'),('opening_fill_identity','execution_fills.fill_identity'),('closing_request_identity','execution_requests.request_identity')):
    positions.append_constraint(ForeignKeyConstraint([column],[target],ondelete='RESTRICT',name='fk_paper_pos_'+column))
Index('uq_paper_position_open_instrument',positions.c.portfolio_id,positions.c.instrument_id,unique=True,postgresql_where=positions.c.status=='OPEN')

accounting=Table('paper_accounting_entries',Base.metadata,
    key('request_identity',primary_key=True),key('result_identity',unique=True),key('command_identity'),key('before_state_identity'),
    Column('position_id',UUID(as_uuid=True),ForeignKey('paper_positions.position_id',ondelete='RESTRICT'),nullable=False),
    Column('portfolio_id',UUID(as_uuid=True),ForeignKey('paper_portfolios.id',ondelete='RESTRICT'),nullable=False),
    Column('user_id',UUID(as_uuid=True),ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
    key('order_identity'),key('fill_identity'),key('decision_identity'),key('intent_identity'),
    Column('action',String(11),nullable=False),Column('environment',String(5),nullable=False),Column('currency',String(16),nullable=False),
    money('quantity'),money('cash_before'),money('cash_after'),money('gross_notional'),money('fee'),
    money('entry_cost_basis',True),money('entry_fee_attributable',True),money('gross_realized_pnl',True),money('net_realized_pnl',True),
    at('executed_at'),Column('evidence',JSONB,nullable=False),Column('canonical_payload',Text,nullable=False),
    CheckConstraint("environment='PAPER' AND currency IN ('USD','USDT') AND action IN ('ENTER_LONG','EXIT_LONG')",name='ck_paper_accounting_scope'),
    CheckConstraint("(action='ENTER_LONG' AND entry_cost_basis IS NULL AND entry_fee_attributable IS NULL AND gross_realized_pnl IS NULL AND net_realized_pnl IS NULL) OR (action='EXIT_LONG' AND entry_cost_basis IS NOT NULL AND entry_fee_attributable IS NOT NULL AND gross_realized_pnl IS NOT NULL AND net_realized_pnl IS NOT NULL)",name='ck_paper_accounting_realized'),
    *(CheckConstraint(f"{n} NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)"+("" if n in ('gross_realized_pnl','net_realized_pnl') else f" AND {n} {'>' if n in ('quantity','gross_notional') else '>='} 0"),name='ck_paper_acct_'+n)
      for n in ('quantity','cash_before','cash_after','gross_notional','fee','entry_cost_basis','entry_fee_attributable','gross_realized_pnl','net_realized_pnl')))
for column,target in (('request_identity','execution_requests.request_identity'),('order_identity','execution_orders.order_identity'),('fill_identity','execution_fills.fill_identity')):
    accounting.append_constraint(ForeignKeyConstraint([column],[target],ondelete='RESTRICT',name='fk_paper_accounting_'+column))
