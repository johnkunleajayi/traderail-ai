"""Durable initial-state and exact-strategy PAPER authorization facts."""
from sqlalchemy import Table,Column,String,DateTime,Numeric,ForeignKey,CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base

initializations = Table("paper_risk_initializations",Base.metadata,
    Column("portfolio_id",UUID(as_uuid=True),ForeignKey("paper_portfolios.id",ondelete="RESTRICT"),primary_key=True),
    Column("book_identity",String(71),nullable=False),
    Column("peak_equity",Numeric(asdecimal=True),nullable=False),
    Column("previous_state",String(16),nullable=False),
    Column("initialized_at",DateTime(timezone=True),nullable=False),
    CheckConstraint("peak_equity=10000 AND previous_state='NORMAL'",name="ck_paper_initial_risk"))
activations = Table("paper_strategy_activations",Base.metadata,
    Column("configuration_identity",String(71),primary_key=True),
    Column("family",String(64),nullable=False),Column("version",String(64),nullable=False),
    Column("environment",String(5),nullable=False),Column("lifecycle",String(16),nullable=False),
    Column("authority",String(64),nullable=False),Column("activated_at",DateTime(timezone=True),nullable=False),
    CheckConstraint("environment='PAPER' AND lifecycle='PAPER' AND family='ADAPTIVE_TREND' AND version='1.0.0'",name="ck_paper_activation_scope"))
