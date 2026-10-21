"""10G inspection security and real four-gate composition; no live database."""
import ast
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from app.main import create_app
from app.core.config import Settings
from app.auth.dependencies import get_current_user
from app.db.session import get_session
from app.risk.integrated import evaluate_risk
from app.risk.api.dependencies import evaluation_time
from app.risk.api.trusted import get_trusted_risk_source
from app.risk.api.service import run_inspection
from test_integrated_risk import inputs, AT, OTHER, assumptions, inventory, switch, health, H
from test_trade_risk_sizing import D

URL = '/api/risk/evaluations'
ORIGIN = 'http://localhost:5173'


@pytest.fixture
def api(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No provider, ingestion or external network allowed')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    from app.market_data import historical_service, candle_store
    monkeypatch.setattr(historical_service, 'ingest_historical_candles', forbidden)
    monkeypatch.setattr(candle_store, 'persist_candles', forbidden)
    i = inputs()
    owner = SimpleNamespace(id=i.intent.scope.user_id, status='active')
    app = create_app(Settings(_env_file=None, frontend_url=ORIGIN, risk_inspector_user_ids=frozenset({owner.id})))
    db = Mock()
    source = Mock()
    source.resolve.return_value = i
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: owner
    app.dependency_overrides[evaluation_time] = lambda: AT
    app.dependency_overrides[get_trusted_risk_source] = lambda: source
    with TestClient(app) as client:
        yield client, app, source, owner, i, db
    for name in ('add', 'add_all', 'execute', 'commit', 'flush', 'delete', 'merge'):
        getattr(db, name).assert_not_called()


def post(api, body=None, origin=ORIGIN):
    return api[0].post(URL, json=body if body is not None else {'intent_identity':api[4].intent.intent_identity},
        headers={} if origin is None else {'Origin':origin})


def test_authorized_exact_approval_uses_original_engine(api):
    response = post(api)
    assert response.status_code == 200
    body = response.json()
    i = api[4]
    expected = evaluate_risk(i)
    assert body['outcome'] == 'APPROVED'
    assert body['decision_identity'] == expected.decision_identity
    assert body['intent_identity'] == i.intent.intent_identity
    assert body['proposed_quantity'] == body['evaluated_quantity'] == '1'
    assert Decimal(body['modeled_risk']) == Decimal('10') and isinstance(body['modeled_risk'], str)
    assert body['valuation_currency'] == 'USDT'
    assert body['evaluated_at'].endswith('Z') and body['approval_expires_at'].endswith('Z')
    assert body['blocking_reason_codes'] == []
    assert [g['gate'] for g in body['gates']] == ['TRADE_RISK','PORTFOLIO_RISK','RISK_STATE','SAFETY_GATE']
    assert all(g['outcome'] == 'PASS' and g['result_identity'] for g in body['gates'])
    assert body['evidence_identities']['portfolio_snapshot'] == i.snapshot.snapshot_identity
    api[2].resolve.assert_called_once_with(user_id=api[3].id, intent_identity=i.intent.intent_identity, evaluated_at=AT)
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert 'snapshot' not in body and 'intent' not in body and 'source_evidence' not in str(body)


def test_unauthenticated_never_resolves_evidence(api):
    del api[1].dependency_overrides[get_current_user]
    r = post(api)
    assert r.status_code == 401 and r.headers['cache-control'] == 'no-store'
    api[2].resolve.assert_not_called()


@pytest.mark.parametrize('kind', ['non_owner', 'no_grants', 'disabled', 'spoofed_email'])
def test_server_side_inspection_grant_required(api, kind):
    client, app, source, owner, _, _ = api
    if kind == 'no_grants': app.state.settings.risk_inspector_user_ids = frozenset()
    elif kind == 'disabled': owner.status = 'disabled'
    else:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=UUID(int=999), status='active', email='owner@example.invalid', is_admin=True)
    r = client.post(URL, json={'intent_identity':api[4].intent.intent_identity},
        headers={'Origin':ORIGIN, 'X-Role':'owner', 'X-User-Id':str(owner.id)})
    assert r.status_code == 403
    source.resolve.assert_not_called()


@pytest.mark.parametrize('origin', [None, 'null', 'https://untrusted.invalid', ORIGIN+'/'])
def test_exact_trusted_origin_required(api, origin):
    assert post(api, origin=origin).status_code == 403
    api[2].resolve.assert_not_called()


def test_real_existing_cookie_session_authentication(api):
    client, app, source, owner, _, db = api
    del app.dependency_overrides[get_current_user]
    db.scalar.return_value = SimpleNamespace(user_id=owner.id, expires_at=datetime.now(timezone.utc)+timedelta(days=1))
    db.get.return_value = owner
    from app.auth.session import SESSION_COOKIE
    client.cookies.set(SESSION_COOKIE, 'a'*64)
    # Retain the existing session service's real expiry check.
    assert post(api).status_code == 200
    db.get.assert_called_once()


def test_active_system_and_unhealthy_rejections_preserved(api):
    i = inputs(switch_inventory=inventory(switches=(switch(),)), health_evidence=health(state=H.UNHEALTHY))
    api[2].resolve.return_value = i
    body = post(api).json()
    assert body['outcome'] == 'REJECTED' and body['approval_expires_at'] is None
    assert {'SYSTEM_KILL_SWITCH_ACTIVE','STRATEGY_HEALTH_UNHEALTHY'} <= set(body['blocking_reason_codes'])
    assert body['gates'][-1]['outcome'] == 'REJECTED'


def test_trade_failure_blocks_dependents_without_fabricating_results(api):
    api[2].resolve.return_value = inputs(sizing=assumptions(proposed_quantity=D('6')))
    body = post(api).json()
    assert body['outcome'] == 'REJECTED'
    assert [g['outcome'] for g in body['gates']] == ['REJECTED','BLOCKED','BLOCKED','PASS']
    assert body['gates'][1]['result_identity'] is None


@pytest.mark.parametrize('field', ['sizing_assumptions','portfolio_policy','portfolio_context','state_policy',
    'drawdown_evidence','safety_policy','switch_inventory','lifecycle_evidence','health_evidence'])
def test_unknown_required_gate_evidence_fails_closed(api, field):
    api[2].resolve.return_value = replace(api[4], **{field:None})
    r = post(api)
    assert r.status_code == 200
    assert r.json()['outcome'] == 'REJECTED' and r.json()['approval_expires_at'] is None


def test_default_authoritative_source_is_unavailable_not_fabricated(api):
    del api[1].dependency_overrides[get_trusted_risk_source]
    r = post(api)
    assert r.status_code == 503
    assert r.json()['detail']['code'] == 'trusted_risk_evidence_unavailable'


@pytest.mark.parametrize('value', [None, object(), {'equity':'10000'}])
def test_untyped_source_cannot_authorize(api, value):
    api[2].resolve.return_value = value
    assert post(api).status_code == 503


def test_wrong_owner_intent_is_not_disclosed(api):
    i = api[4]
    api[1].state.settings.risk_inspector_user_ids = frozenset({UUID(int=999)})
    api[1].dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=UUID(int=999),status='active')
    assert post(api).status_code == 404


def test_wrong_requested_intent_and_evaluation_time_fail_closed(api):
    assert post(api, {'intent_identity':OTHER}).status_code == 404
    api[2].resolve.return_value = replace(api[4], evaluated_at=AT+timedelta(seconds=1))
    assert post(api).status_code == 409


def test_integrated_context_mismatch_remains_rejection(api):
    i = api[4]
    api[2].resolve.return_value = replace(i, portfolio_context=replace(i.portfolio_context, snapshot_identity=OTHER))
    r = post(api)
    assert r.status_code == 200 and r.json()['outcome'] == 'REJECTED'
    assert 'INTEGRATED_CONTEXT_MISMATCH' in r.json()['blocking_reason_codes']


@pytest.mark.parametrize('field', ['snapshot','configuration','health_evidence','switch_inventory','user_id',
    'portfolio_id','override','outcome','quantity','evaluated_at'])
def test_http_cannot_supply_trusted_state_or_bypass(api, field):
    r = post(api, {'intent_identity':api[4].intent.intent_identity, field:'arbitrary'})
    assert r.status_code == 422
    api[2].resolve.assert_not_called()


@pytest.mark.parametrize('value', ['', 'BTCUSDT', 'sha256:bad', 42, None])
def test_malformed_intent_identity_rejected(api, value):
    assert post(api, {'intent_identity':value}).status_code == 422


@pytest.mark.parametrize('error', [RuntimeError('PRIVATE_SENTINEL'), SQLAlchemyError('PRIVATE_SENTINEL')])
def test_source_failures_are_sanitized(api, error):
    api[2].resolve.side_effect = error
    r = post(api)
    assert r.status_code == 503 and 'PRIVATE_SENTINEL' not in r.text
    assert r.headers['cache-control'] == 'no-store'


def test_deterministic_exact_decimals_and_source_immutability(api):
    i = api[4]
    from app.risk.canonical import to_wire
    before = to_wire(i)
    first = post(api).json()
    assert first == post(api).json() and to_wire(i) == before
    variant = inputs(sizing=assumptions(proposed_quantity=D('1.000000000000000000')))
    api[2].resolve.return_value = variant
    last = post(api).json()
    assert last['proposed_quantity'] == last['evaluated_quantity'] == '1.000000000000000000'
    assert last['decision_identity'] == first['decision_identity']


def test_implicit_conservative_size_is_reported_from_engine(api):
    api[2].resolve.return_value = inputs(sizing=assumptions())
    body = post(api).json()
    assert body['proposed_quantity'] is None
    assert body['evaluated_quantity'] is not None and body['modeled_risk'] is not None


def test_no_order_execution_or_consumption_routes_or_code(api):
    routes = {(path,tuple(sorted(method.upper() for method in methods)))
        for path,methods in api[1].openapi()['paths'].items() if path.startswith('/api/risk')}
    assert routes == {(URL, ('POST',))}
    folder = Path(__file__).parents[1]/'app/risk/api'
    for path in folder.glob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                assert not ast.unparse(node.func).endswith(('.commit','.flush','.add','.execute','.submit_order'))
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [node.module or ''] if isinstance(node,ast.ImportFrom) else [n.name for n in node.names]
                assert not any(any(s in n for s in ('app.market_data','httpx','requests','app.db')) for n in names)
    assert not {'order','execution','consumed'} & set(post(api).json())


def test_empty_operator_grants_are_default_and_invalid_grants_rejected():
    assert not Settings(_env_file=None).risk_inspector_user_ids
    with pytest.raises(ValueError): Settings(_env_file=None, risk_inspector_user_ids=['not-a-uuid'])
