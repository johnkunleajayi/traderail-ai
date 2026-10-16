from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.session import SESSION_COOKIE, find_session
from app.core.config import Settings
from app.db.session import get_session
from app.users.models import User


def require_auth_configuration(request: Request) -> Settings:
    settings = request.app.state.settings
    if not settings.auth_enabled:
        raise HTTPException(503, "Google authentication is not configured")
    return settings


def get_current_user(request: Request, db: Session = Depends(get_session)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "Authentication required")
    session = find_session(db, token)
    user = db.get(User, session.user_id) if session else None
    if user is None or user.status != "active":
        raise HTTPException(401, "Authentication required")
    return user