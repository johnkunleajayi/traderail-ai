"""Offline CLOSE assertion regressions; retained figures are not a new PG run."""
from contextlib import contextmanager
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID
import pytest
from integration import runner_postgres as h


@pytest.mark.parametrize('net,expected_cash',[
    ('43.02130420080266843672929112912857981348812551508150555840',
     '10043.02130420080266843672929112912857981348812551508150555840'),
    ('-43.02130420080266843672929112912857981348812551508150555840',
     '9956.97869579919733156327070887087142018651187448491849444160'),
])
@pytest.mark.parametrize('mismatch',[False,True])
def test_close_assertion_is_exact_under_precision_28(monkeypatch,net,expected_cash,mismatch):
    # Profit values are the retained PostgreSQL receipts; loss is an explicit
    # deterministic negative-P&L fixture. Neither runs Strategy/Risk/Execution.
    net=Decimal(net);cash=Decimal(expected_cash)
    if mismatch:
        # One final decimal-place discrepancy must still fail strict equality.
        cash=Decimal(expected_cash[:-1]+'1')
    receipts=[{'decision_identity':'entry'},
        {'decision_identity':'fresh_exit','net_realized_pnl':net}]
    position={'status':'CLOSED','quantity':Decimal('0'),'closed_at':h.ScenarioTimeline().exit_at}
    class Connection:
        def execute(self,statement,*args):
            sql=str(statement)
            if 'paper_accounting_entries' in sql:
                return SimpleNamespace(mappings=lambda:SimpleNamespace(all=lambda:receipts))
            if 'paper_positions' in sql:
                return SimpleNamespace(mappings=lambda:SimpleNamespace(one=lambda:position))
            if 'SELECT cash_balance' in sql:
                return SimpleNamespace(scalar_one=lambda:cash)
            return None  # Fixture candle insertion, never sent to a database.
    @contextmanager
    def connection():yield Connection()
    engine=SimpleNamespace(begin=connection,connect=connection)
    cycles=iter([
        SimpleNamespace(outcome_code='PAPER_EXECUTED',persisted=True),
        SimpleNamespace(outcome_code='POSITION_HELD',persisted=True),
        SimpleNamespace(outcome_code='POSITION_CLOSED',persisted=True),
        SimpleNamespace(reused=True),
    ])
    monkeypatch.setattr(h,'cycle_once',lambda *args,**kwargs:next(cycles))
    monkeypatch.setattr(h,'counts',lambda engine:{})
    checked=Mock();monkeypatch.setattr(h,'expected_counts',checked)
    monkeypatch.setattr(h,'sessionmaker',lambda **kwargs:'offline_sessions')
    from app.mandate_runner import process
    monkeypatch.setattr(process,'due',lambda *args:False)
    fixture={'timeline':h.ScenarioTimeline(),'rows':[{}]*219,
        'portfolio':UUID(int=1),'mandate':UUID(int=2),'user':UUID(int=3)}
    with localcontext() as context:
        context.prec=28
        # Reproduce the original bug without using it as the expected oracle.
        assert Decimal('10000')+net != Decimal(expected_cash)
        if mismatch:
            with pytest.raises(h.Refused,match='^PNL_CASH_RECONCILIATION_FAILED$'):
                h.close_lifecycle(engine,fixture)
        else:
            h.close_lifecycle(engine,fixture)
            assert checked.call_args.args==(engine,2)
        assert context.prec==28
