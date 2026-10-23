"""Exact approved product settings; no database/provider or execution activity."""
from dataclasses import FrozenInstanceError,fields,replace
from datetime import timedelta
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID
import os,sys,subprocess,json
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.risk.context import PortfolioScope
from app.risk.vocabulary import Environment,RiskProfile
from app.risk import paper_policy as p
from app.risk.api.schemas import EvaluationRequest
from app.execution.api.schemas import PaperExecutionRequest


def scope(user=1,portfolio=2,environment=Environment.PAPER):
    return PortfolioScope(UUID(int=user),UUID(int=portfolio),environment,'USD')


def test_exact_approved_values_and_types():
    c=p.configuration_for_paper_portfolio(scope())
    assert c.version=='paper-risk-v1' and c.environment is Environment.PAPER and c.profile is RiskProfile.BALANCED
    expected={
        'max_trade_risk_fraction':('0.02','0.01'),
        'max_open_risk_fraction':('0.06','0.03'),
        'max_gross_exposure_fraction':('0.50','0.30'),
        'max_directional_exposure_fraction':('0.40','0.25'),
        'max_instrument_concentration_fraction':('0.25','0.15'),
        'max_strategy_concentration_fraction':('0.35','0.20'),
        'max_correlated_exposure_fraction':('0.35','0.20'),
        'max_drawdown_fraction':('0.10','0.06')}
    for name,(hard,operating) in expected.items():
        assert type(getattr(c.platform_hard_limits,name)) is Decimal
        assert type(getattr(c.operating_limits,name)) is Decimal
        assert getattr(c.platform_hard_limits,name)==Decimal(hard)
        assert getattr(c.operating_limits,name)==Decimal(operating)
    assert c.platform_hard_limits.max_positions==4 and c.operating_limits.max_positions==3
    assert c.platform_hard_limits.max_approval_age==timedelta(minutes=10)
    assert c.operating_limits.max_approval_age==timedelta(minutes=5)
    assert {f.name for f in fields(c.operating_limits) if f.init}==set(expected)|{'max_positions','max_approval_age'}


def test_all_paper_users_portfolios_get_same_immutable_balanced_policy():
    a=p.configuration_for_paper_portfolio(scope())
    b=p.configuration_for_paper_portfolio(scope(user=5,portfolio=9))
    assert a is b and a.configuration_identity==b.configuration_identity
    assert type(p._POLICIES) is MappingProxyType
    with pytest.raises(FrozenInstanceError):a.version='override'
    with pytest.raises(FrozenInstanceError):a.operating_limits.max_positions=100
    with pytest.raises(TypeError):p._POLICIES['override']=a


def test_version_is_part_of_existing_deterministic_identity():
    c=p.PAPER_RISK_V1
    assert replace(c).configuration_identity==c.configuration_identity
    assert replace(c,version='other-version').configuration_identity!=c.configuration_identity
    assert replace(c,operating_limits=replace(c.operating_limits,max_trade_risk_fraction=Decimal('.005'))).configuration_identity!=c.configuration_identity
    assert p.configuration_for_paper_portfolio(scope(),version='paper-risk-v1') is c


@pytest.mark.parametrize('environment',[Environment.LAB,Environment.SHADOW,Environment.LIVE])
def test_no_non_paper_configuration(environment):
    with pytest.raises(p.PaperRiskPolicyUnavailable):p.configuration_for_paper_portfolio(scope(environment=environment))


@pytest.mark.parametrize('version',[None,'','unknown','paper-risk-v2',RiskProfile.CONSERVATIVE,RiskProfile.GROWTH])
def test_unknown_version_or_profile_has_no_fallback(version):
    with pytest.raises(p.PaperRiskPolicyUnavailable):p.configuration_for_paper_portfolio(scope(),version=version)


@pytest.mark.parametrize('catalog',[None,{}, {'paper-risk-v1':None}])
def test_missing_configuration_fails_closed(monkeypatch,catalog):
    monkeypatch.setattr(p,'_POLICIES',catalog)
    with pytest.raises(p.PaperRiskPolicyUnavailable):p.configuration_for_paper_portfolio(scope())


@pytest.mark.parametrize('name',[f.name for f in fields(p.PAPER_RISK_V1.operating_limits) if f.init])
def test_existing_operating_cannot_exceed_hard_validation_remains_authoritative(name):
    c=p.PAPER_RISK_V1;hard=getattr(c.platform_hard_limits,name)
    delta=timedelta(seconds=1) if isinstance(hard,timedelta) else 1 if type(hard) is int else Decimal('.01')
    with pytest.raises(ValueError,match='Operating limits cannot exceed platform hard limits'):
        replace(c,operating_limits=replace(c.operating_limits,**{name:hard+delta}))


@pytest.mark.parametrize('field',['version','profile','risk_profile','operating_limits','platform_hard_limits','max_trade_risk_fraction'])
def test_http_contracts_cannot_override_policy(field):
    identity='sha256:'+'a'*64
    with pytest.raises(ValidationError):EvaluationRequest.model_validate({'intent_identity':identity,field:{'max_positions':99}})
    with pytest.raises(ValidationError):PaperExecutionRequest.model_validate({
        'intent_identity':identity,'decision_identity':identity,'request_identity':identity,field:{'max_positions':99}})
    expected=p.PaperRiskPolicyUnavailable if field=='version' else TypeError
    with pytest.raises(expected):p.configuration_for_paper_portfolio(scope(),**{field:'override'})


def test_policy_identity_is_stable_across_four_fresh_hash_seeds():
    script="""import json
from app.risk.paper_policy import PAPER_RISK_V1
p=PAPER_RISK_V1
print(json.dumps([p.configuration_identity,p.operating_limits.limits_identity,p.platform_hard_limits.limits_identity]))
"""
    results=[]
    for seed in ('1','2','3','4'):
        result=subprocess.run([sys.executable,'-B','-c',script],cwd=Path(__file__).parents[1],
            env=dict(os.environ,PYTHONHASHSEED=seed),capture_output=True,text=True,check=True)
        results.append(json.loads(result.stdout))
    assert all(result==results[0] for result in results)
