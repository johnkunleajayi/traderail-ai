"""OAuth HTTP calls and SQLAlchemy session boundaries are mocked; no SQLite."""
import base64
import hashlib
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey
from pydantic import ValidationError
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from app.portfolio.models import PaperPortfolio
from app.auth.models import AuthSession
from app.auth.session import SESSION_COOKIE, hash_token
from app.core.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.users.models import User
from app.users.schemas import UserResponse
from app.users.service import save_google_user

CLIENT_ID = "test-client.apps.googleusercontent.com"
TEST_SECRET = "test-only-signing-value-not-a-real-secret-12345"


def settings(**overrides):
    values = dict(_env_file=None, google_client_id=CLIENT_ID,
                  google_client_secret="test-provider-secret", session_secret=TEST_SECRET)
    values.update(overrides)
    return Settings(**values)


def example_user(**overrides):
    values = dict(id=uuid.uuid4(), google_id="google-subject", email="trader@example.com",
                  name="Test Trader", avatar_url=None, country=None, base_currency="USD",
                  status="active", created_at=datetime.now(timezone.utc),
                  updated_at=datetime.now(timezone.utc))
    values.update(overrides)
    return User(**values)


@pytest.fixture
def flow():
    configuration = settings()
    app = create_app(configuration)
    db = Mock()
    user = example_user()
    sessions = {}

    def add(value):
        if isinstance(value, AuthSession):
            sessions[value.token_hash] = value

    def scalar(statement):
        if statement.column_descriptions[0]["entity"] is AuthSession:
            return sessions.get(statement.compile().params["token_hash_1"])
        if statement.column_descriptions[0]["entity"] is PaperPortfolio:
            return PaperPortfolio(user_id=user.id)
        return None

    def get(model, key):
        return user if model is User and key == user.id else sessions.get(key)

    db.add.side_effect = add
    db.scalar.side_effect = scalar
    db.get.side_effect = get
    db.delete.side_effect = lambda value: sessions.pop(value.token_hash, None)
    app.dependency_overrides[get_session] = lambda: db
    key = RSAKey.generate_key(parameters={"kid": "test-key"})
    context = SimpleNamespace(app=app, db=db, user=user, sessions=sessions,
                              nonce="", claims={}, key=key, signing_key=key,
                              token_error=False, calls=[], token_form=None)
    metadata = {
        "issuer": "https://accounts.google.com",
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
        "id_token_signing_alg_values_supported": ["RS256"],
    }

    def provider(request):
        context.calls.append(str(request.url))
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json=metadata)
        if request.url.path == "/oauth2/v3/certs":
            return httpx.Response(200, json={"keys": [key.as_dict()]})
        if request.url.path == "/token":
            context.token_form = parse_qs(request.content.decode())
            if context.token_error:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PRIVATE_PROVIDER_DETAIL"})
            now = int(time.time())
            claims = {"iss": metadata["issuer"], "sub": user.google_id, "aud": CLIENT_ID,
                      "iat": now, "exp": now + 300, "nonce": context.nonce,
                      "email": user.email, "email_verified": True, "name": user.name}
            claims.update(context.claims)
            encoded = jwt.encode({"alg": "RS256", "kid": "test-key"}, claims, context.signing_key)
            return httpx.Response(200, json={"access_token": "test-access-token",
                                           "token_type": "Bearer", "id_token": encoded})
        raise AssertionError(f"Unexpected external HTTP request: {request.url}")

    app.state.oauth.google.client_kwargs["transport"] = httpx.MockTransport(provider)
    context.client = TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False)

    def start():
        response = context.client.get("/api/auth/google")
        assert response.status_code == 302
        params = parse_qs(urlsplit(response.headers["location"]).query)
        context.nonce = params["nonce"][0]
        context.params = params
        return params["state"][0]

    context.start = start
    return context


def test_model_schema():
    columns = User.__table__.columns
    assert set(columns.keys()) == {"id", "google_id", "email", "name", "avatar_url",
                                   "country", "base_currency", "status", "created_at", "updated_at"}
    assert isinstance(columns.id.type, PGUUID)
    unique = {tuple(column.name for column in constraint.columns)
              for constraint in User.__table__.constraints if isinstance(constraint, UniqueConstraint)}
    assert ("google_id",) in unique and ("email",) in unique
    assert columns.created_at.type.timezone and columns.updated_at.type.timezone
    assert columns.base_currency.server_default.arg == "USD"
    assert columns.status.server_default.arg == "active"
    assert columns.updated_at.onupdate is not None
    assert "google_id" not in UserResponse.model_validate(example_user()).model_dump()
    assert AuthSession.__table__.columns.expires_at.type.timezone


def test_disabled_auth_and_anonymous_me():
    client = TestClient(create_app(Settings(_env_file=None)))
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/google").status_code == 503


@pytest.mark.parametrize("overrides", [
    {"google_client_id": ""}, {"session_secret": "short"},
    {"session_cookie_samesite": "none"},
    {"frontend_url": "https://traderail.example"},
    {"frontend_url": "http://evil.example"},
    {"frontend_url": "http://127.0.0.1:5173/path"},
    {"google_redirect_uri": "https://user:password@example.com/callback"},
    {"cors_origins": ["*"]},
])
def test_unsafe_auth_configuration(overrides):
    with pytest.raises(ValidationError):
        settings(**overrides)


def test_auth_environment(monkeypatch):
    monkeypatch.setenv("TRADERAIL_GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("TRADERAIL_GOOGLE_CLIENT_SECRET", "test-provider-secret")
    monkeypatch.setenv("TRADERAIL_SESSION_SECRET", TEST_SECRET)
    configuration = Settings(_env_file=None)
    assert configuration.auth_enabled
    assert "test-provider-secret" not in repr(configuration)
    assert TEST_SECRET not in repr(configuration)


def test_redirect_state_nonce_and_pkce(flow):
    state = flow.start()
    assert len(state) >= 20
    assert len(flow.nonce) >= 20
    assert flow.params["code_challenge_method"] == ["S256"]
    assert flow.params["redirect_uri"] == ["http://127.0.0.1:8000/api/auth/google/callback"]
    assert set(flow.params["scope"][0].split()) == {"openid", "email", "profile"}
    assert "client_secret" not in flow.params
    cookie = flow.client.cookies.get("traderail_oauth")
    assert cookie
    response = flow.client.get("/api/auth/google")
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()


def test_missing_or_wrong_state_never_contacts_token_endpoint(flow):
    flow.start()
    for query in ("code=x", "code=x&state=invalid"):
        assert flow.client.get("/api/auth/google/callback?" + query).status_code == 400
    assert not any(url.endswith("/token") for url in flow.calls)
    flow.db.commit.assert_not_called()


def test_tampered_oauth_cookie(flow):
    state = flow.start()
    flow.client.cookies.clear()
    flow.client.cookies.set("traderail_oauth", "tampered")
    assert flow.client.get("/api/auth/google/callback", params={"state": state, "code": "code"}).status_code == 400
    assert not any(url.endswith("/token") for url in flow.calls)


def test_verified_callback_session_and_logout(flow, monkeypatch):
    state = flow.start()
    monkeypatch.setattr("app.auth.router.save_google_user", lambda db, claims: flow.user)
    result = flow.client.get("/api/auth/google/callback", params={"state": state, "code": "test-code"})
    assert result.status_code == 303
    inserts = [call.args[0] for call in flow.db.execute.call_args_list
               if call.args[0].table.name == "paper_portfolios"]
    assert len(inserts) == 1 and inserts[0].compile().params["user_id"] == flow.user.id
    assert result.headers["location"] == "http://127.0.0.1:5173/?auth=success"
    token = flow.client.cookies.get(SESSION_COOKIE)
    assert token and hash_token(token) in flow.sessions
    assert token not in flow.sessions
    assert "test-access-token" not in result.headers.get("set-cookie", "")
    assert "HttpOnly" in result.headers["set-cookie"]
    assert "SameSite=lax" in result.headers["set-cookie"]
    verifier = flow.token_form["code_verifier"][0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == flow.params["code_challenge"][0]
    me = flow.client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == flow.user.email
    assert me.headers["cache-control"] == "no-store"
    assert "google_id" not in me.json()
    assert flow.client.get("/api/auth/google/callback", params={"state": state, "code": "test-code"}).status_code == 400
    assert flow.client.post("/api/auth/logout", headers={"Origin": "https://attacker.example"}).status_code == 403
    assert flow.client.post("/api/auth/logout").status_code == 403
    logout = flow.client.post("/api/auth/logout", headers={"Origin": "http://127.0.0.1:5173"})
    assert logout.status_code == 204
    assert not flow.sessions and "Max-Age=0" in logout.headers["set-cookie"]
    flow.client.cookies.set(SESSION_COOKIE, token)
    assert flow.client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("claims", [
    {"nonce": "incorrect"}, {"aud": "wrong-audience"}, {"iss": "https://attacker.example"},
    {"exp": int(time.time()) - 600}, {"email_verified": False}, {"sub": ""},
])
def test_invalid_identity_rejected(flow, claims):
    state = flow.start()
    flow.claims = claims
    response = flow.client.get("/api/auth/google/callback", params={"state": state, "code": "code"})
    assert response.status_code == 400
    assert not flow.sessions
    flow.db.commit.assert_not_called()


def test_invalid_signature(flow):
    state = flow.start()
    flow.signing_key = RSAKey.generate_key(parameters={"kid": "test-key"})
    result = flow.client.get("/api/auth/google/callback", params={"state": state, "code": "code"})
    assert result.status_code == 400
    flow.db.commit.assert_not_called()


def test_provider_error_is_sanitized(flow):
    state = flow.start()
    flow.token_error = True
    result = flow.client.get("/api/auth/google/callback", params={"state": state, "code": "code"})
    assert result.status_code == 400
    assert "PRIVATE_PROVIDER_DETAIL" not in result.text


def test_expired_and_disabled_sessions(flow):
    token = "test-session-token"
    record = AuthSession(token_hash=hash_token(token), user_id=flow.user.id,
                         expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    flow.sessions[record.token_hash] = record
    flow.client.cookies.set(SESSION_COOKIE, token)
    assert flow.client.get("/api/auth/me").status_code == 401
    record.expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    flow.user.status = "disabled"
    assert flow.client.get("/api/auth/me").status_code == 401
    flow.client.cookies.set(SESSION_COOKIE, "unknown-token")
    assert flow.client.get("/api/auth/me").status_code == 401


def test_secure_cookie_settings():
    configuration = settings(frontend_url="https://app.traderail.example",
                             google_redirect_uri="https://api.traderail.example/api/auth/google/callback",
                             cors_origins=["https://app.traderail.example"],
                             session_cookie_secure=True)
    app = create_app(configuration)
    from starlette.responses import Response
    from app.auth.session import set_session_cookie
    response = Response()
    set_session_cookie(response, "opaque-test-token", configuration)
    assert "Secure" in response.headers["set-cookie"]
    assert "httponly" in response.headers["set-cookie"].lower()
    assert app.state.settings.auth_enabled


def test_user_creation_update_and_email_collision():
    db = Mock()
    claims = {"sub": "google-subject", "email": "TRADER@example.com",
              "email_verified": True, "name": "Trader", "picture": "https://example.com/avatar"}
    db.scalar.side_effect = [None, None]
    user = save_google_user(db, claims)
    assert user.google_id == "google-subject" and user.email == "trader@example.com"
    db.add.assert_called_once_with(user)
    db.flush.assert_called_once()
    existing = example_user(country="NG", base_currency="NGN")
    db.reset_mock()
    db.scalar.side_effect = [existing, existing]
    claims["name"] = "Updated Trader"
    claims["picture"] = "javascript:alert(1)"
    assert save_google_user(db, claims) is existing
    assert existing.name == "Updated Trader" and existing.avatar_url is None
    assert existing.country == "NG" and existing.base_currency == "NGN"
    db.add.assert_not_called()
    db.scalar.side_effect = [None, existing]
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        save_google_user(db, claims)
    assert error.value.status_code == 409


def test_database_failure_is_sanitized(flow):
    from sqlalchemy.exc import OperationalError
    flow.db.scalar.side_effect = OperationalError("private-sql", {}, Exception("private-password"))
    flow.client.cookies.set(SESSION_COOKIE, "session")
    result = flow.client.get("/api/auth/me")
    assert result.status_code == 503
    assert "private-password" not in result.text and "private-sql" not in result.text

def test_oauth_cookie_expiry(flow, monkeypatch):
    import itsdangerous.timed
    state = flow.start()
    original_time = itsdangerous.timed.time.time
    monkeypatch.setattr(itsdangerous.timed.time, "time", lambda: original_time() + 601)
    response = flow.client.get("/api/auth/google/callback", params={"state": state, "code": "code"})
    assert response.status_code == 400
    assert not any(url.endswith("/token") for url in flow.calls)


def test_session_rotation(flow, monkeypatch):
    monkeypatch.setattr("app.auth.router.save_google_user", lambda db, claims: flow.user)
    state = flow.start()
    assert flow.client.get("/api/auth/google/callback", params={"state": state, "code": "first"}).status_code == 303
    first = flow.client.cookies.get(SESSION_COOKIE)
    state = flow.start()
    assert flow.client.get("/api/auth/google/callback", params={"state": state, "code": "second"}).status_code == 303
    second = flow.client.cookies.get(SESSION_COOKIE)
    assert first != second
    assert hash_token(first) not in flow.sessions and hash_token(second) in flow.sessions


def test_disabled_user_cannot_reauthenticate():
    from fastapi import HTTPException
    db = Mock()
    disabled = example_user(status="disabled")
    db.scalar.side_effect = [disabled, disabled]
    with pytest.raises(HTTPException) as error:
        save_google_user(db, {"sub": disabled.google_id, "email": disabled.email, "email_verified": True})
    assert error.value.status_code == 403
    db.flush.assert_not_called()


def test_auth_cors_credentials(flow):
    response = flow.client.options("/api/auth/logout", headers={
        "Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


def test_configuration_errors_do_not_expose_secrets():
    private_value = "test-only-secret-must-not-appear-in-errors"
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, google_client_secret=private_value)
    assert private_value not in str(error.value)
