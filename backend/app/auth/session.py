import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.auth.models import AuthSession
from app.core.config import Settings

SESSION_COOKIE = "traderail_session"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user_id, settings: Settings) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    # Opportunistic expiry cleanup; no scheduler is required for this foundation.
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    db.add(AuthSession(token_hash=hash_token(token), user_id=user_id,
                       expires_at=now + timedelta(seconds=settings.session_max_age)))
    return token


def find_session(db: Session, token: str | None) -> AuthSession | None:
    if not token or len(token) > 128:
        return None
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    if session is None or session.expires_at <= datetime.now(timezone.utc):
        return None
    return session


def set_session_cookie(response: Response, token: str, settings: Settings):
    response.set_cookie(SESSION_COOKIE, token, max_age=settings.session_max_age,
                        httponly=True, secure=settings.session_cookie_secure,
                        samesite=settings.session_cookie_samesite, path="/")


def clear_session_cookie(response: Response, settings: Settings):
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True,
                           secure=settings.session_cookie_secure,
                           samesite=settings.session_cookie_samesite)