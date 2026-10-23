from sqlalchemy import Table,Column,String,Text,DateTime,Numeric,CheckConstraint,ForeignKeyConstraint,UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID,JSONB
from app.db.base import Base

authorizations=Table('paper_authorizations',Base.metadata,
    Column('registry_identity',String(71),primary_key=True),
    Column('authorization_identity',String(71),nullable=False,unique=True),
    Column('intent_identity',String(71),nullable=False),
    Column('decision_identity',String(71),nullable=False,unique=True),
    Column('user_id',UUID(as_uuid=True),nullable=False),
    Column('portfolio_id',UUID(as_uuid=True),nullable=False),
    Column('instrument_id',UUID(as_uuid=True),nullable=False),
    Column('environment',String(5),nullable=False),Column('outcome',String(8),nullable=False),
    Column('quantity',Numeric(asdecimal=True),nullable=False),
    Column('created_at',DateTime(timezone=True),nullable=False),
    Column('evaluated_at',DateTime(timezone=True),nullable=False),
    Column('approval_expires_at',DateTime(timezone=True),nullable=False),
    Column('payload',JSONB,nullable=False),Column('canonical_payload',Text,nullable=False),
    CheckConstraint("environment='PAPER' AND outcome='APPROVED'",name='ck_paper_auth_scope'),
    CheckConstraint("quantity NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND quantity>0",name='ck_paper_auth_quantity'),
    CheckConstraint('created_at=evaluated_at AND evaluated_at<approval_expires_at',name='ck_paper_auth_times'),
    ForeignKeyConstraint(['user_id'],['users.id'],ondelete='RESTRICT'),
    ForeignKeyConstraint(['portfolio_id','user_id'],['paper_portfolios.id','paper_portfolios.user_id'],ondelete='RESTRICT',name='fk_paper_auth_owner'),
    ForeignKeyConstraint(['instrument_id'],['instruments.id'],ondelete='RESTRICT'),
    UniqueConstraint('registry_identity','user_id','portfolio_id',name='uq_paper_auth_owner'))

commands=Table('paper_authorized_commands',Base.metadata,
    Column('request_identity',String(71),primary_key=True),
    Column('command_identity',String(71),nullable=False,unique=True),
    Column('action_identity',String(71),nullable=False,unique=True),
    Column('registry_identity',String(71),nullable=False),
    Column('user_id',UUID(as_uuid=True),nullable=False),Column('portfolio_id',UUID(as_uuid=True),nullable=False),
    Column('executed_at',DateTime(timezone=True),nullable=False),
    Column('payload',JSONB,nullable=False),Column('canonical_payload',Text,nullable=False),
    ForeignKeyConstraint(['registry_identity','user_id','portfolio_id'],
        ['paper_authorizations.registry_identity','paper_authorizations.user_id','paper_authorizations.portfolio_id'],
        ondelete='RESTRICT',name='fk_paper_command_auth'))
