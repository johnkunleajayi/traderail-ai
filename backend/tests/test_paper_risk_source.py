"""Bounded PAPER evidence composition. No physical DB/provider in pytest."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID
import pytest
from fastapi.testclient import TestClient
from app.risk.paper_source import service as s
from app.risk.paper_source.market import analyze,PaperEvidenceUnavailable
from app.risk.paper_source.policies import *
from app.risk.paper_source.models import initializations,activations
from app.risk.integrated.inspection import inspect_risk
from app.risk.api.dependencies import evaluation_time
from app.risk.api.trusted import get_trusted_risk_source
from app.auth.dependencies import get_current_user
from app.main import create_app
from app.core.config import Settings
from test_adaptive_trend_intent import build
from test_adaptive_trend import observation
from app.strategies.contract import evaluate_strategy
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
from app.execution.paper.book import PaperBookState
from test_integrated_9c_audit import frozen_dataset


def sample(monkeypatch,*,atr='1',active=False):
    intent=build(atr=atr)
    obs=observation(feature_changes={'atr_14':Decimal(atr)})
    at=obs.candle.close_time+timedelta(minutes=1,milliseconds=1)
    obs=replace(obs,as_of=at)
    decision=evaluate_strategy(AdaptiveTrendStrategy(),obs)
    market=replace(intent.market_context,observed_at=at,expires_at=at+timedelta(minutes=4))
    book=PaperBookState(intent.scope.portfolio_id,intent.scope.user_id,'PAPER','USDT',Decimal('10000'),
        'active',at,(),())
    monkeypatch.setattr(s,'owned_untouched_book',lambda c,u:book)
    monkeypatch.setattr(s,'read_observation',lambda c,at:(obs,decision,market))
    monkeypatch.setattr(s,'provision_inactive',lambda *a,**k:None)
    from test_paper_switches import connection,load
    def inventory(c,i,**kwargs):
        from app.risk.vocabulary import KillSwitchScope
        conn,_=connection(i,active=KillSwitchScope.SYSTEM if active else None,changed_at=at)
        from app.risk.paper_switches import load_inventory
        return load_inventory(conn,i,**kwargs)
    monkeypatch.setattr(s,'load_inventory',inventory)
    activation=dict(configuration_identity=obs.definition.configuration_identity,family='ADAPTIVE_TREND',version='1.0.0',
        environment='PAPER',lifecycle='PAPER',authority=ACTIVATION_VERSION,activated_at=at)
    c=Mock();c.dialect.name='postgresql';c.in_transaction.return_value=True
    c.connection.dbapi_connection.autocommit=False;c.get_isolation_level.return_value='READ COMMITTED'
    absent=Mock();absent.mappings.return_value.one_or_none.return_value=None
    saved=Mock();saved.mappings.return_value.one.return_value=activation
    c.execute.side_effect=[absent,Mock(),Mock(),saved]
    return c,intent.scope.user_id,at


def test_complete_evidence_uses_existing_gates(monkeypatch):
    c,user,at=sample(monkeypatch)
    i,decision=s.assemble(c,user,at)
    assert i.snapshot.equity==i.snapshot.available_cash==Decimal('10000')
    assert i.snapshot.open_positions==() and i.snapshot.open_risk==0
    assert i.snapshot.scope.base_currency=='USDT'
    assert i.sizing_assumptions.proposed_quantity is None
    assert i.sizing_assumptions.costs is COSTS
    report=inspect_risk(i)
    assert len(report.gates)==4
    assert report.decision.outcome.value=='REJECTED'  # nominal maximum can exceed concentration; never resize outside Risk


def test_operational_health_requires_clear_switches(monkeypatch):
    c,user,at=sample(monkeypatch,active=True)
    i,_=s.assemble(c,user,at)
    assert i.health_evidence.state.value=='UNKNOWN'
    report=inspect_risk(i)
    assert 'SYSTEM_KILL_SWITCH_ACTIVE' in [r.value for r in report.decision.reason_codes]


def test_no_trade_never_initializes_or_registers(monkeypatch):
    c,user,at=sample(monkeypatch)
    obs=observation('range');d=evaluate_strategy(AdaptiveTrendStrategy(),obs)
    monkeypatch.setattr(s,'read_observation',lambda c,at:(obs,d,None))
    result,decision=s.assemble(c,user,at)
    assert result is None and decision.decision.value=='NO_ACTION'
    c.execute.assert_not_called()


def analytical_rows():
    ds=frozen_dataset('no_trades')
    rows=ds.candles[-HISTORY:]
    # Preserve OHLC; set causal retrieval to completion for this synthetic history.
    rows=tuple(replace(r,retrieved_at=r.close_time+timedelta(milliseconds=1)) for r in rows)
    at=rows[-1].close_time+timedelta(milliseconds=1,minutes=1)
    return ds.context,rows,at


def test_real_engines_accept_flat_history_as_no_trade():
    context,rows,at=analytical_rows()
    obs,d,market=analyze(context,rows,at=at)
    assert d.decision.value=='NO_ACTION'
    assert market.data_through==rows[-1].close_time
    assert market.expires_at==rows[-1].open_time+timedelta(hours=1,minutes=10)
    assert analyze(context,rows,at=at)==(obs,d,market)


@pytest.mark.parametrize('change',['stale','future_retrieval','missing','wrong_currency'])
def test_market_fail_closed(change):
    context,rows,at=analytical_rows()
    if change=='stale':at+=timedelta(minutes=10)
    elif change=='future_retrieval':rows=(*rows[:-1],replace(rows[-1],retrieved_at=at+timedelta(seconds=1)))
    elif change=='missing':rows=rows[1:]
    else:context=replace(context,canonical_symbol='BTC/USD')
    with pytest.raises(ValueError):analyze(context,rows,at=at)


def test_exact_declared_policies():
    assert COSTS.entry_fee_rate==COSTS.exit_fee_rate==Decimal('.001')
    assert STATE_POLICY.caution_recovery==Decimal('.015')
    assert STATE_POLICY.defensive_recovery==Decimal('.035')
    assert STATE_POLICY.suspended_recovery==Decimal('.055')
    from app.risk.vocabulary import Environment
    from app.strategy_lab.lifecycle import StrategyState
    assert SAFETY_POLICY.lifecycle_by_environment[Environment.PAPER]==(StrategyState.PAPER,)
    assert SAFETY_POLICY.lifecycle_by_environment[Environment.LIVE]==()
    assert SAFETY_POLICY.minimum_completed_trades is None
    assert SAFETY_POLICY.required_validation_verdicts is None
    assert SAFETY_POLICY.require_data_sufficient is False


def test_fixed_api_auth_origin_and_no_financial_injection():
    user=SimpleNamespace(id=UUID(int=2),status='active')
    origin='http://localhost:5173'
    app=create_app(Settings(_env_file=None,frontend_url=origin,risk_inspector_user_ids=frozenset({user.id})))
    source=Mock();source.evaluate_latest.return_value={'status':'NO_TRADE','reason_codes':['NO_ENTRY']}
    app.dependency_overrides[get_trusted_risk_source]=lambda:source
    with TestClient(app) as client:
        assert client.post('/api/risk/paper-evaluations',json={},headers={'Origin':origin}).status_code==401
        app.dependency_overrides[get_current_user]=lambda:user
        assert client.post('/api/risk/paper-evaluations',json={}).status_code==403
        assert client.post('/api/risk/paper-evaluations',json={'quantity':'1'},headers={'Origin':origin}).status_code==422
        r=client.post('/api/risk/paper-evaluations',json={},headers={'Origin':origin})
        assert r.status_code==200 and r.json()['status']=='NO_TRADE'
        assert r.headers['cache-control']=='no-store'
        source.evaluate_latest.side_effect=RuntimeError('secret')
        r=client.post('/api/risk/paper-evaluations',json={},headers={'Origin':origin})
        assert r.status_code==503 and 'secret' not in r.text
