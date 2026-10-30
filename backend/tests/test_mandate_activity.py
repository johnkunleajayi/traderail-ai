"""Offline owner-scoped activity reads; no physical database or trading calls."""
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.sql.dml import Insert,Update,Delete
from app.main import create_app
from app.core.config import Settings
from app.mandate_runner.models import work
from app.mandate_performance import activity
from test_mandate_performance import api
from test_mandate_paper import MID,USER


def row(at,**changes):
    result={column.name:None for column in work.c}
    result.update(work_id=uuid4(),mandate_id=MID,candle_id=uuid4(),runner_version='paper-runner-v1',
        state='COMPLETED',attempts=1,created_at=at,updated_at=at,last_attempt_at=at,
        fresh_until=at+timedelta(minutes=5),outcome_code='NO_TRADE',reason_code='NO_TRADE')
    return {**result,**changes}


@pytest.mark.parametrize('state,outcome,category',[
    ('PENDING',None,'PENDING'),('RETRY_WAIT','STORAGE_FAILURE','PENDING'),
    ('COMPLETED','NO_TRADE','NO_TRADE'),('COMPLETED','PAPER_EXECUTED','ENTRY_EXECUTED'),
    ('COMPLETED','POSITION_HELD','POSITION_HELD'),('COMPLETED','POSITION_CLOSED','POSITION_CLOSED'),
    ('COMPLETED','RISK_REJECTED','RISK_REJECTED'),('COMPLETED','EXIT_RISK_REJECTED','RISK_REJECTED'),
    ('SKIPPED','STALE_CANDLE','SKIPPED'),('RECONCILIATION_REQUIRED','UNKNOWN','FAILURE')])
def test_persisted_states_are_distinct_and_never_prove_runner_online(api,state,outcome,category):
    a=api('empty');a.db.rows['paper_mandate_work']=[row(a.at,state=state,outcome_code=outcome)]
    before=deepcopy(a.db.rows)
    response=a.client.get(f'/api/mandates/{MID}/activity')
    assert response.status_code==200,response.text
    result=response.json()
    assert result['latest_evaluation']['category']==category
    assert result['latest_evaluation']['recorded_at']
    assert result['portfolio_cash']==format(a.db.rows['paper_portfolios'][0]['cash_balance'],'f')
    assert result['runner_health']=='UNVERIFIED'
    assert response.headers['cache-control']=='no-store'
    assert a.session.options=={'execution_options':{'isolation_level':'REPEATABLE READ','postgresql_readonly':True}}
    assert a.db.rows==before
    assert not any(isinstance(s,(Insert,Update,Delete)) for s in a.db.statements)


def test_empty_history_is_not_a_no_trade_decision(api):
    a=api('empty');a.db.rows['paper_mandate_work']=[]
    response=a.client.get(f'/api/mandates/{MID}/activity')
    assert response.status_code==200,response.text
    assert response.json()['latest_evaluation'] is None


def test_latest_work_excludes_other_mandates_and_preserves_exact_cash(api):
    a=api('empty');p=a.db.rows['paper_portfolios'][0]
    from decimal import Decimal
    p['cash_balance']=Decimal('9999.123456789123456789123456789')
    a.db.rows['paper_mandate_work']=[row(a.at-timedelta(hours=1)),row(a.at,outcome_code='POSITION_HELD'),
        row(a.at+timedelta(hours=1),mandate_id=uuid4(),outcome_code='POSITION_CLOSED')]
    response=a.client.get(f'/api/mandates/{MID}/activity')
    assert response.status_code==200,response.text
    assert response.json()['latest_evaluation']['outcome_code']=='POSITION_HELD'
    assert response.json()['portfolio_cash']=='9999.123456789123456789123456789'


def test_activity_ownership_authentication_and_no_store(api):
    a=api('empty')
    assert a.client.get(f'/api/mandates/{uuid4()}/activity').status_code==404
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response=client.get(f'/api/mandates/{MID}/activity')
        assert response.status_code==401 and response.headers['cache-control']=='no-store'
    assert a.client.post(f'/api/mandates/{MID}/activity').status_code==405


def test_failed_read_is_unavailable_not_zero_or_empty(api,monkeypatch):
    a=api('empty')
    monkeypatch.setattr(a.db,'execute',lambda *args:(_ for _ in ()).throw(RuntimeError('private host password')))
    response=a.client.get(f'/api/mandates/{MID}/activity')
    assert response.status_code==503 and 'private' not in response.text
    assert response.headers['cache-control']=='no-store'


def test_activity_does_not_call_strategy_risk_or_execution(api,monkeypatch):
    from app.mandate_paper import service as trading
    from app.risk import authorization_service
    a=api('empty');a.db.rows['paper_mandate_work']=[]
    def forbidden(*args,**kwargs):raise AssertionError('Read cannot trade')
    monkeypatch.setattr(trading,'run',forbidden)
    monkeypatch.setattr(authorization_service,'evaluate_and_register',forbidden)
    assert a.client.get(f'/api/mandates/{MID}/activity?price=1&quantity=9').status_code==200



def test_other_owner_cannot_read_existing_mandate(api):
    a=api('empty');a.db.rows['trading_mandates'][0]['user_id']=uuid4()
    response=a.client.get(f'/api/mandates/{MID}/activity')
    assert response.status_code==404
    assert 'portfolio_cash' not in response.json()


@pytest.mark.parametrize('currency',[None,'USD'])
def test_missing_or_unsupported_portfolio_does_not_become_zero_cash(api,currency):
    a=api('empty');a.db.rows['paper_mandate_work']=[]
    if currency is None:a.db.rows['paper_portfolios']=[]
    else:a.db.rows['paper_portfolios'][0]['base_currency']=currency
    response=a.client.get(f'/api/mandates/{MID}/activity')
    assert response.status_code==200,response.text
    assert response.json()['portfolio_state']=='UNAVAILABLE'
    assert response.json()['portfolio_cash'] is None


def test_unsafe_stored_reason_is_not_reflected(api):
    a=api('empty');a.db.rows['paper_mandate_work']=[row(a.at,reason_code='private host password')]
    response=a.client.get(f'/api/mandates/{MID}/activity')
    assert response.status_code==503 and 'private' not in response.text
