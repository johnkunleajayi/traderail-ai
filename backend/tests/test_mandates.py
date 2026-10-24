"""Isolated domain/HTTP tests, not physical PostgreSQL verification."""
import ast
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, Numeric
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
from app.auth.dependencies import get_current_user
from app.core.config import Settings
from app.db.session import get_session
from app.instruments.models import Instrument
from app.main import create_app
from app.mandates.domain import MandateError, MandateStatus, require_transition
from app.mandates.models import TradingMandate
from app.mandates.schemas import MandateIntent
AT=datetime(2026,10,24,tzinfo=timezone.utc)
ORIGIN="http://localhost:5173"
BODY={"capital_allocation":"2000.000000000000000001","risk_level":"BALANCED","trading_style":"MEDIUM_TERM"}

@pytest.fixture
def api(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError("No Risk/execution/provider/portfolio side effects")
    monkeypatch.setattr(httpx.HTTPTransport,"handle_request",forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport,"handle_async_request",forbidden)
    from app.risk.integrated import evaluator
    from app.risk import authorization_service
    from app.execution.paper import service as paper
    from app.portfolio import service as portfolio
    for obj,name in [(evaluator,"evaluate_risk"),(authorization_service,"evaluate_and_register"),(paper,"execute_paper"),(portfolio,"ensure_paper_portfolio")]:monkeypatch.setattr(obj,name,forbidden)
    db=Mock();rows={};committed={};pending=[];queries=[]
    def scalar(query):
        queries.append(query)
        if query.column_descriptions[0]["entity"] is Instrument:
            return SimpleNamespace(canonical_symbol="BTC/USDT") if db.market_available else None
        params=query.compile().params;row=rows.get(params["id_1"])
        return row if row and row.user_id==params["user_id_1"] else None
    def scalars(query):
        queries.append(query);params=query.compile().params
        return SimpleNamespace(all=lambda:[r for r in rows.values() if r.user_id==params["user_id_1"]])
    def flush():
        for row in pending:
            row.id=uuid4();row.created_at=AT;row.updated_at=AT;rows[row.id]=row
        pending.clear()
    def commit():committed.clear();committed.update(deepcopy(rows))
    def rollback():pending.clear();rows.clear();rows.update(deepcopy(committed))
    db.scalar.side_effect=scalar;db.scalars.side_effect=scalars;db.add.side_effect=pending.append
    db.flush.side_effect=flush;db.commit.side_effect=commit;db.rollback.side_effect=rollback;db.market_available=True
    user=SimpleNamespace(id=uuid4(),status="active");app=create_app(Settings(_env_file=None,frontend_url=ORIGIN))
    app.dependency_overrides[get_current_user]=lambda:user
    app.dependency_overrides[get_session]=lambda:db
    with TestClient(app) as client:yield SimpleNamespace(client=client,user=user,db=db,rows=rows,queries=queries)

def post(api,suffix="",payload=None):return api.client.post("/api/mandates"+suffix,json=BODY if payload is None else payload,headers={"Origin":ORIGIN})
def created(api):
    result=post(api);assert result.status_code==201,result.text
    return result.json()

def test_create_owned_draft_exact_decimal_and_utc(api):
    row=created(api)
    assert row["capital_allocation"]==BODY["capital_allocation"] and row["user_id"]==str(api.user.id)
    assert (row["environment"],row["allocation_currency"],row["instrument"],row["direction_capability"],row["status"])==("PAPER","USDT","BTC/USDT","LONG_ONLY","DRAFT")
    assert row["created_at"].endswith("Z")
    assert isinstance(next(iter(api.rows.values())).capital_allocation,Decimal)
    api.db.commit.assert_called_once()

@pytest.mark.parametrize("field,value",[("environment","LIVE"),("allocation_currency","USD"),("instrument","ETH/USDT"),("direction_capability","SHORT"),("risk_level","AGGRESSIVE"),("trading_style","1h"),("capital_allocation","0"),("capital_allocation","-1"),("capital_allocation","NaN"),("capital_allocation","Infinity"),("capital_allocation","-Infinity"),("capital_allocation",2.5),("capital_allocation",True),("capital_allocation",None),("user_id","injected"),("status","ACTIVE"),("balance","10000"),("quantity","1"),("price","100"),("strategy_parameters",{}),("created_at",AT.isoformat())])
def test_invalid_or_untrusted_fields(api,field,value):
    assert post(api,payload={**BODY,field:value}).status_code==422
    api.db.add.assert_not_called();api.db.commit.assert_not_called()

@pytest.mark.parametrize("missing",list(BODY))
def test_required_intent(api,missing):assert post(api,payload={k:v for k,v in BODY.items() if k!=missing}).status_code==422

@pytest.mark.parametrize("risk,style",list(product(["CONSERVATIVE","BALANCED","GROWTH"],["SHORT_TERM","MEDIUM_TERM","LONGER_TERM"])))
def test_all_preferences_are_intent_only(api,risk,style):
    response=post(api,payload={**BODY,"risk_level":risk,"trading_style":style})
    assert response.status_code==201 and response.json()["risk_level"]==risk and response.json()["trading_style"]==style

def test_anonymous():
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.get("/api/mandates").status_code==401
        assert client.post("/api/mandates",json=BODY,headers={"Origin":ORIGIN}).status_code==401

@pytest.mark.parametrize("origin",[None,"https://evil.example",ORIGIN+"/","http://localhost:5173.evil.example"])
def test_exact_origin(api,origin):
    response=api.client.post("/api/mandates",json=BODY,headers={} if origin is None else {"Origin":origin})
    assert response.status_code==403 and response.headers["cache-control"]=="no-store"
    api.db.commit.assert_not_called()

def test_ownership_isolation_all_routes(api):
    assert api.client.get("/api/mandates").json()==[]
    row=created(api);path="/api/mandates/"+row["id"]
    assert api.client.get(path).json()==row
    assert api.client.get("/api/mandates").json()==[row]
    api.user.id=uuid4()
    assert api.client.get("/api/mandates").json()==[]
    assert api.client.get(path).status_code==404
    for action in ["activate","pause","retire","intent"]:
        assert post(api,"/"+row["id"]+"/"+action,BODY if action=="intent" else {}).status_code==404
    assert api.client.get("/api/mandates/"+str(uuid4())).status_code==404

def test_lifecycle_safe_editing_and_no_trading_side_effects(api):
    suffix="/"+created(api)["id"]
    assert post(api,suffix+"/pause",{}).status_code==409
    assert post(api,suffix+"/intent",{**BODY,"capital_allocation":"1500.01"}).json()["capital_allocation"]=="1500.01"
    assert post(api,suffix+"/activate",{}).json()["status"]=="ACTIVE"
    assert post(api,suffix+"/activate",{}).status_code==200
    assert post(api,suffix+"/intent",BODY).status_code==409
    assert post(api,suffix+"/pause",{}).json()["status"]=="PAUSED"
    assert post(api,suffix+"/intent",{**BODY,"risk_level":"CONSERVATIVE"}).status_code==200
    assert post(api,suffix+"/activate",{}).status_code==200
    assert post(api,suffix+"/retire",{}).json()["status"]=="RETIRED"
    for action in ["activate","pause","intent"]:assert post(api,suffix+"/"+action,BODY if action=="intent" else {}).status_code==409
    assert post(api,suffix+"/retire",{}).status_code==200

@pytest.mark.parametrize("current,target",list(product(list(MandateStatus),list(MandateStatus))))
def test_transition_matrix(current,target):
    allowed={"DRAFT":{"ACTIVE","RETIRED"},"ACTIVE":{"PAUSED","RETIRED"},"PAUSED":{"ACTIVE","RETIRED"},"RETIRED":set()}
    if current==target or target in allowed[current]:require_transition(current,target)
    else:
        with pytest.raises(MandateError):require_transition(current,target)

@pytest.mark.parametrize("field,value",[("capital_allocation",None),("capital_allocation",Decimal("0")),("risk_level",None),("trading_style",None)])
def test_activation_revalidates_persisted_intent(api,field,value):
    row=created(api);setattr(api.rows[UUID(row["id"])],field,value)
    assert post(api,"/"+row["id"]+"/activate",{}).status_code==409

def test_activation_market_unavailable(api):
    row=created(api);api.db.market_available=False
    result=post(api,"/"+row["id"]+"/activate",{})
    assert result.status_code==409 and result.json()["detail"]["code"]=="mandate_market_unavailable"

def test_lifecycle_extra_fields_rejected(api):
    assert post(api,"/"+created(api)["id"]+"/activate",{"user_id":str(uuid4()),"quantity":"1"}).status_code==422

def test_owner_immutable_intent_frozen():
    row=TradingMandate(user_id=uuid4())
    with pytest.raises(ValueError,match="ownership"):row.user_id=uuid4()
    intent=MandateIntent(**BODY)
    with pytest.raises(ValidationError):intent.capital_allocation=Decimal("2")

def test_owner_scoped_locks(api):
    post(api,"/"+created(api)["id"]+"/activate",{})
    sql=[str(q.compile(dialect=postgresql.dialect())) for q in api.queries]
    assert any("trading_mandates.user_id =" in q and "FOR UPDATE" in q for q in sql)
    assert any("instruments.canonical_symbol =" in q and "FOR SHARE" in q for q in sql)

def test_rollback_and_sanitized_db_error(api):
    api.db.flush.side_effect=OperationalError("secret query",{},Exception("private credential"))
    response=post(api)
    assert response.status_code==503 and "secret" not in response.text and "private" not in response.text
    api.db.rollback.assert_called_once();api.db.commit.assert_not_called()
    assert not api.rows and response.headers["cache-control"]=="no-store"

def test_no_store_and_invalid_uuid(api):
    row=created(api)
    for url in ["/api/mandates","/api/mandates/"+row["id"]]:
        response=api.client.get(url)
        assert response.headers["cache-control"]=="no-store" and response.headers["referrer-policy"]=="no-referrer"
    assert api.client.get("/api/mandates/not-a-uuid").status_code==422

def test_database_contract():
    table=TradingMandate.__table__
    assert isinstance(table.c.capital_allocation.type,Numeric) and table.c.capital_allocation.type.scale is None and table.c.capital_allocation.type.asdecimal
    assert table.c.created_at.type.timezone and table.c.updated_at.type.timezone
    assert all(not c.nullable for c in table.columns)
    checks={str(c.sqltext) for c in table.constraints if isinstance(c,CheckConstraint)}
    assert "environment = 'PAPER'" in checks and "allocation_currency = 'USDT'" in checks
    assert any("NaN" in text and "Infinity" in text for text in checks)
    assert {fk.target_fullname for fk in table.foreign_keys}=={"users.id","instruments.canonical_symbol"}

def test_no_domain_import_connection_to_trading():
    for path in (Path(__file__).parents[1]/"app/mandates").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.ImportFrom):assert not (node.module or "").startswith(("app.risk","app.execution","app.strategies","app.portfolio","app.market_data"))
