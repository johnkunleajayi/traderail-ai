"""Compatibility only. Synthetic mapping tests are hypotheses, never production policy."""
import ast
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4
import json
import os
import subprocess
import sys
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
from app.auth.dependencies import get_current_user
from app.core.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.instruments.models import Instrument
from app.mandates.models import TradingMandate
from app.mandate_eligibility.contracts import (MandateContext, InstrumentReference, EligibilityPolicy,
    TechnicalMapping, TechnicalConfiguration, EligibilityStatus, EligibilityReason as R,
    RiskConfigurationReference)
from app.mandate_eligibility.policy import POLICY, POLICY_V1, POLICY_V2, ADAPTIVE_TREND_REFERENCE
from app.mandate_eligibility.resolver import resolve
from app.mandate_eligibility.service import resolution_time

AT=datetime(2026,10,25,tzinfo=timezone.utc)
ID=UUID('11111111-1111-4111-8111-111111111111')
OWNER=UUID('22222222-2222-4222-8222-222222222222')
MARKET=InstrumentReference(id=UUID('33333333-3333-4333-8333-333333333333'),canonical_symbol='BTC/USDT',market_type='SPOT',status='active')

def context(**changes):
    return MandateContext(id=ID,user_id=OWNER,environment='PAPER',capital_allocation=Decimal('500.00'),allocation_currency='USDT',instrument='BTC/USDT',risk_level='BALANCED',trading_style='MEDIUM_TERM',direction_capability='LONG_ONLY',status='ACTIVE',created_at=AT,updated_at=AT).model_copy(update=changes)

def hypothetical_policy():
    # Explicit test-only product hypothesis. This mapping is NOT installed in POLICY.
    rule=TechnicalMapping(trading_style='MEDIUM_TERM',risk_level='BALANCED',configurations=(TechnicalConfiguration(strategy_family='TEST_ONLY',strategy_version='hypothesis-v1',configuration_identity='sha256:'+'a'*64,timeframe='1h'),))
    return EligibilityPolicy(version='test-hypothesis-only',risk_references=POLICY.risk_references,technical_mappings=(rule,))

def run(mandate=None,policy=POLICY,instrument=MARKET,at=AT):
    return resolve(mandate or context(),policy,instrument,resolved_at=at)


def test_historical_v1_remains_fail_closed_without_mapping():
    result=run(policy=POLICY_V1)
    assert result.status is EligibilityStatus.INELIGIBLE
    assert result.user_intent.trading_style=='MEDIUM_TERM'
    assert result.technical_eligibility.eligible_configurations==()
    assert result.reason_codes==(R.STRATEGY_MAPPING_UNCONFIGURED,R.STYLE_MAPPING_UNCONFIGURED)
    assert POLICY_V1.technical_mappings==()
    assert result.policy_version=='mandate-eligibility-v1' and result.policy_identity==POLICY_V1.policy_identity


def test_complete_synthetic_server_policy_can_express_eligible_without_any_evaluation():
    result=run(policy=hypothetical_policy())
    assert result.status is EligibilityStatus.ELIGIBLE
    assert result.reason_codes==(R.TECHNICAL_CONFIGURATION_ELIGIBLE,)
    assert result.technical_eligibility.eligible_configurations[0].strategy_family=='TEST_ONLY'
    assert result.scope=='TECHNICAL_ELIGIBILITY_ONLY'


@pytest.mark.parametrize('status,reason',[('DRAFT',R.MANDATE_DRAFT),('PAUSED',R.MANDATE_PAUSED),('RETIRED',R.MANDATE_RETIRED),('UNKNOWN',R.MANDATE_STATUS_UNSUPPORTED)])
def test_nonactive_mandates_never_eligible(status,reason):
    result=run(context(status=status),hypothetical_policy())
    assert result.status is EligibilityStatus.INELIGIBLE and reason in result.reason_codes
    assert not result.technical_eligibility.eligible_configurations


@pytest.mark.parametrize('field,value,reason',[
    ('environment','LIVE',R.ENVIRONMENT_UNSUPPORTED),('instrument','ETH/USDT',R.INSTRUMENT_UNSUPPORTED),
    ('allocation_currency','USD',R.CURRENCY_UNSUPPORTED),('direction_capability','SHORT',R.DIRECTION_UNSUPPORTED),
    ('trading_style','1h',R.TRADING_STYLE_UNSUPPORTED),('risk_level','UNKNOWN',R.RISK_PREFERENCE_UNSUPPORTED)])
def test_unsupported_combinations_no_nearest_fallback(field,value,reason):
    result=run(context(**{field:value}),hypothetical_policy())
    assert result.status is EligibilityStatus.INELIGIBLE and reason in result.reason_codes
    assert not result.technical_eligibility.eligible_configurations
    assert getattr(result.user_intent,field)==value


@pytest.mark.parametrize('style',['SHORT_TERM','LONGER_TERM'])
def test_unsupported_styles_remain_without_mapping(style):
    result=run(context(trading_style=style))
    assert R.STYLE_MAPPING_UNCONFIGURED in result.reason_codes and not result.technical_eligibility.eligible_configurations


@pytest.mark.parametrize('preference',['CONSERVATIVE','GROWTH'])
def test_no_fallback_to_balanced(preference):
    result=run(context(risk_level=preference))
    assert R.RISK_PREFERENCE_UNSUPPORTED in result.reason_codes
    assert result.technical_eligibility.risk_configuration is None


def test_existing_balanced_policy_referenced_without_thresholds_or_authorization():
    from app.risk.paper_policy import PAPER_RISK_V1
    ref=run().technical_eligibility.risk_configuration
    assert ref.version==PAPER_RISK_V1.version and ref.configuration_identity==PAPER_RISK_V1.configuration_identity
    assert set(ref.model_dump())=={'preference','version','configuration_identity'}


@pytest.mark.parametrize('market',[None,MARKET.model_copy(update={'status':'disabled'}),MARKET.model_copy(update={'canonical_symbol':'ETH/USDT'}),MARKET.model_copy(update={'market_type':'FUTURE'})])
def test_authoritative_registry_required(market):
    result=run(policy=hypothetical_policy(),instrument=market)
    assert result.status is EligibilityStatus.INELIGIBLE and R.INSTRUMENT_UNAVAILABLE in result.reason_codes


def test_missing_policy_fails_closed():
    assert run(policy=None).reason_codes==(R.POLICY_UNAVAILABLE,)


def test_immutable_exact_decimal_capital_is_context_not_quantity():
    amount=Decimal('500.12345678901234567890123456789')
    result=run(context(capital_allocation=amount))
    assert result.user_intent.capital_allocation==amount
    wire=result.model_dump(mode='json')
    assert wire['user_intent']['capital_allocation']==format(amount,'f')
    assert 'quantity' not in json.dumps(wire) and 'notional' not in json.dumps(wire)
    with pytest.raises(ValidationError):result.status=EligibilityStatus.INELIGIBLE
    with pytest.raises(ValidationError):result.user_intent.capital_allocation=Decimal('1')
    with pytest.raises(ValidationError):POLICY.version='injected'


@pytest.mark.parametrize('amount',[Decimal('0'),Decimal('-1'),Decimal('NaN'),Decimal('Infinity'),0.1])
def test_invalid_financial_context_cannot_be_constructed(amount):
    data=context().model_dump();data['capital_allocation']=amount
    with pytest.raises(ValidationError):MandateContext(**data)


def test_canonical_decimal_and_utc_equivalence_no_hidden_request_time_identity():
    first=run()
    second=run(context(capital_allocation=Decimal('500.0000'),created_at=AT.astimezone(timezone(timedelta(hours=1))),updated_at=AT.astimezone(timezone(timedelta(hours=1)))),at=AT+timedelta(seconds=1))
    assert first.eligibility_identity==second.eligibility_identity
    assert first.resolved_at!=second.resolved_at
    assert run(at=AT.astimezone(timezone(timedelta(hours=-3))))==first
    with pytest.raises(ValidationError):run(at=AT.replace(tzinfo=None))


@pytest.mark.parametrize('changes',[{'capital_allocation':Decimal('501')},{'risk_level':'GROWTH'},{'trading_style':'LONGER_TERM'},{'updated_at':AT+timedelta(seconds=1)},{'status':'PAUSED'},{'user_id':uuid4()}])
def test_material_intent_changes_identity(changes):
    assert run(context(**changes)).eligibility_identity!=run().eligibility_identity


def test_policy_and_registry_material_changes_bind_identity():
    assert run(policy=POLICY.model_copy(update={'version':'next-policy'})).eligibility_identity!=run().eligibility_identity
    assert run(instrument=MARKET.model_copy(update={'id':uuid4()})).eligibility_identity!=run().eligibility_identity


def test_policy_order_is_not_semantic():
    ref=RiskConfigurationReference(preference='CONSERVATIVE',version='test-only',configuration_identity='sha256:'+'b'*64)
    left=EligibilityPolicy(version='test-only',risk_references=POLICY.risk_references+(ref,))
    right=EligibilityPolicy(version='test-only',risk_references=(ref,)+POLICY.risk_references)
    assert left.policy_identity==right.policy_identity and run(policy=left).eligibility_identity==run(policy=right).eligibility_identity


@pytest.fixture
def api(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('No analysis, Risk, execution, network or portfolio mutation')
    from app.risk.integrated import evaluator
    from app.risk import authorization_service
    from app.execution.paper import service as paper
    from app.portfolio import service as portfolio
    from app.strategies.adaptive_trend import AdaptiveTrendStrategy
    for obj,name in [(evaluator,'evaluate_risk'),(authorization_service,'evaluate_and_register'),(paper,'execute_paper'),(portfolio,'ensure_paper_portfolio'),(AdaptiveTrendStrategy,'decide')]:monkeypatch.setattr(obj,name,forbidden)
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',forbidden)
    owner=SimpleNamespace(id=OWNER,status='active');row=SimpleNamespace(**context().model_dump());market=SimpleNamespace(**MARKET.model_dump())
    db=Mock();queries=[]
    def scalar(query):
        queries.append(query)
        if query.column_descriptions[0]['entity'] is Instrument:return market
        params=query.compile().params
        return row if params.get('id_1')==row.id and params.get('user_id_1')==row.user_id else None
    db.scalar.side_effect=scalar
    app=create_app(Settings(_env_file=None));app.dependency_overrides[get_current_user]=lambda:owner
    app.dependency_overrides[get_session]=lambda:db;app.dependency_overrides[resolution_time]=lambda:AT
    with TestClient(app) as client:yield SimpleNamespace(client=client,owner=owner,row=row,db=db,queries=queries)


def test_authenticated_readonly_api_no_financial_side_effects(api):
    response=api.client.get(f'/api/mandates/{ID}/eligibility')
    assert response.status_code==200,response.text
    data=response.json()
    assert data['status']=='ELIGIBLE' and data['user_intent']['capital_allocation']=='500.00'
    assert data['resolved_at'].endswith('Z') and data['policy_version']==POLICY.version
    assert response.headers['cache-control']=='no-store'
    for method in ('add','flush','commit','execute','delete'):getattr(api.db,method).assert_not_called()
    sql=[str(query.compile(dialect=postgresql.dialect())) for query in api.queries]
    assert any('trading_mandates.user_id =' in query for query in sql)
    assert all('FOR UPDATE' not in query and 'FOR SHARE' not in query for query in sql)


@pytest.mark.parametrize('status',['DRAFT','PAUSED','RETIRED'])
def test_nonactive_api_returns_explicit_ineligible(api,status):
    api.row.status=status
    response=api.client.get(f'/api/mandates/{ID}/eligibility')
    assert response.status_code==200 and 'MANDATE_'+status in response.json()['reason_codes']


def test_ownership_and_missing_id(api):
    api.owner.id=uuid4()
    for identity in (ID,uuid4()):assert api.client.get(f'/api/mandates/{identity}/eligibility').status_code==404


def test_api_anonymous_requires_auth():
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.get(f'/api/mandates/{ID}/eligibility').status_code==401


def test_db_failure_is_sanitized(api):
    api.db.scalar.side_effect=OperationalError('private query',{},Exception('secret credential'))
    response=api.client.get(f'/api/mandates/{ID}/eligibility')
    assert response.status_code==503 and 'private' not in response.text and 'secret' not in response.text


def test_read_is_deterministic_and_has_no_write_api(api):
    first=api.client.get(f'/api/mandates/{ID}/eligibility').json()
    assert api.client.get(f'/api/mandates/{ID}/eligibility').json()==first
    assert api.client.post(f'/api/mandates/{ID}/eligibility',json={'quantity':'99','strategy':'override'}).status_code==405


def test_pure_resolver_has_no_runtime_reads_or_evaluation_imports():
    path=Path(__file__).parents[1]/'app/mandate_eligibility/resolver.py'
    source=path.read_text()
    assert 'datetime.now' not in source and 'execute(' not in source
    for node in ast.walk(ast.parse(source)):
        if isinstance(node,ast.ImportFrom):assert not (node.module or '').startswith(('sqlalchemy','app.risk','app.execution','app.strategies','app.market_data'))


def test_four_fresh_hash_seeds():
    code="""from datetime import datetime,timezone
from decimal import Decimal
from uuid import UUID
from app.mandate_eligibility.contracts import MandateContext,InstrumentReference
from app.mandate_eligibility.policy import POLICY
from app.mandate_eligibility.resolver import resolve
at=datetime(2026,10,25,tzinfo=timezone.utc)
row=MandateContext(id=UUID(int=1),user_id=UUID(int=2),environment='PAPER',capital_allocation=Decimal('500.00'),allocation_currency='USDT',instrument='BTC/USDT',risk_level='GROWTH',trading_style='MEDIUM_TERM',direction_capability='LONG_ONLY',status='PAUSED',created_at=at,updated_at=at)
market=InstrumentReference(id=UUID(int=3),canonical_symbol='BTC/USDT',market_type='SPOT',status='active')
r=resolve(row,POLICY,market,resolved_at=at)
print(r.model_dump_json())
supported=row.model_copy(update={'risk_level':'BALANCED','status':'ACTIVE'})
print(resolve(supported,POLICY,market,resolved_at=at).model_dump_json())
"""
    outputs=[]
    for seed in ('1','7','41','123'):
        env={**os.environ,'PYTHONHASHSEED':seed}
        outputs.append(subprocess.check_output([sys.executable,'-B','-c',code],cwd=Path(__file__).parents[1],env=env,text=True,timeout=40))
    assert len(set(outputs))==1


def test_exact_approved_production_combination_and_canonical_identity():
    from app.strategies.adaptive_trend import AdaptiveTrendStrategy
    from app.risk.paper_policy import PAPER_RISK_V1
    result=run()
    assert POLICY is POLICY_V2 and result.status is EligibilityStatus.ELIGIBLE
    assert result.policy_version=='mandate-eligibility-v2'
    assert result.policy_identity==POLICY_V2.policy_identity!=POLICY_V1.policy_identity
    assert len(POLICY_V2.technical_mappings)==1
    assert len(result.technical_eligibility.eligible_configurations)==1
    technical=result.technical_eligibility.eligible_configurations[0]
    assert technical==ADAPTIVE_TREND_REFERENCE
    assert technical.strategy_family=='ADAPTIVE_TREND' and technical.strategy_version=='1.0.0'
    assert technical.configuration_identity==AdaptiveTrendStrategy().definition.configuration_identity
    assert technical.timeframe=='1h' and technical.timeframe_role=='SETUP_EVALUATION'
    assert technical.position_capabilities==('LONG','CASH')
    assert result.technical_eligibility.risk_configuration.configuration_identity==PAPER_RISK_V1.configuration_identity
    assert result.technical_eligibility.risk_configuration.version=='paper-risk-v1'
    assert result.user_intent.capital_allocation==Decimal('500.00')
    assert run(policy=POLICY_V1).status is EligibilityStatus.INELIGIBLE


def test_only_one_product_combination_supported_no_additional_mappings():
    from itertools import product
    for risk,style,status in product(('CONSERVATIVE','BALANCED','GROWTH'),('SHORT_TERM','MEDIUM_TERM','LONGER_TERM'),('DRAFT','ACTIVE','PAUSED','RETIRED')):
        result=run(context(risk_level=risk,trading_style=style,status=status))
        assert (result.status is EligibilityStatus.ELIGIBLE)==(risk=='BALANCED' and style=='MEDIUM_TERM' and status=='ACTIVE')
        if result.status is EligibilityStatus.INELIGIBLE:assert not result.technical_eligibility.eligible_configurations


def test_no_extra_timeframes_or_holding_period_instruction():
    result=run().model_dump(mode='json')
    configuration=result['technical_eligibility']['eligible_configurations'][0]
    assert configuration['timeframe']=='1h'
    assert set(configuration)=={'strategy_family','strategy_version','configuration_identity','timeframe','timeframe_role','position_capabilities'}
    assert 'parameters' not in configuration
    assert not {'confirmation_timeframe','entry_timeframe','context_timeframe','holding_period','quantity','target_exposure','reserved_capital'} & set(configuration)


def test_v2_references_are_immutable():
    with pytest.raises(ValidationError):POLICY_V2.version='overridden'
    with pytest.raises(ValidationError):ADAPTIVE_TREND_REFERENCE.timeframe='4h'


def test_v1_snapshot_identity_preserved_by_v2_addition():
    expected=EligibilityPolicy(version='mandate-eligibility-v1',risk_references=POLICY_V1.risk_references,technical_mappings=())
    assert POLICY_V1==expected and POLICY_V1.policy_identity==expected.policy_identity


def test_supported_api_does_not_construct_strategy_or_authorize_risk(api,monkeypatch):
    from app.strategies.adaptive_trend import AdaptiveTrendStrategy
    def forbidden(*args,**kwargs):raise AssertionError('No strategy instantiation/evaluation at request time')
    monkeypatch.setattr(AdaptiveTrendStrategy,'__post_init__',forbidden)
    response=api.client.get(f'/api/mandates/{ID}/eligibility')
    assert response.status_code==200 and response.json()['status']=='ELIGIBLE'
    technical=response.json()['technical_eligibility']['eligible_configurations'][0]
    assert technical['position_capabilities']==['LONG','CASH']
    assert technical['timeframe_role']=='SETUP_EVALUATION'
    assert response.json()['policy_version']=='mandate-eligibility-v2'
    for name in ('add','flush','commit','delete','execute'):getattr(api.db,name).assert_not_called()


@pytest.mark.parametrize('changes',[
    {'environment':'LAB'},{'environment':'SHADOW'},{'environment':'LIVE'},
    {'instrument':'ETH/USDT'},{'allocation_currency':'USD'},
    {'direction_capability':'SHORT'},{'direction_capability':'LONG_SHORT'},
    {'trading_style':'SHORT_TERM'},{'trading_style':'LONGER_TERM'},
    {'risk_level':'CONSERVATIVE'},{'risk_level':'GROWTH'},
])
def test_v2_does_not_fall_back_for_any_unsupported_intent(changes):
    row=context(**changes)
    # Even a matching active registry record cannot expand the product policy.
    market=MARKET.model_copy(update={'canonical_symbol':row.instrument})
    result=run(row,instrument=market)
    assert result.status is EligibilityStatus.INELIGIBLE
    assert result.technical_eligibility.eligible_configurations==()
    for key,value in changes.items():assert getattr(result.user_intent,key)==value
