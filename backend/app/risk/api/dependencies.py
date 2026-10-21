"""Existing session authentication plus explicit server-owned inspection grants."""
from datetime import datetime, timezone
from fastapi import Depends, HTTPException, Request
from app.auth.dependencies import get_current_user
from app.users.models import User


def inspection_permission(request: Request, user: User = Depends(get_current_user)):
    if user.status != "active" or user.id not in request.app.state.settings.risk_inspector_user_ids:
        raise HTTPException(403, "Risk inspection is restricted to authorized operators")
    if request.headers.get("origin") != request.app.state.settings.frontend_url:
        raise HTTPException(403, "Untrusted request origin")
    return user


def evaluation_time():
    return datetime.now(timezone.utc)
