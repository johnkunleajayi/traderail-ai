from datetime import datetime, timezone
from fastapi import Depends, HTTPException, Request
from app.auth.dependencies import get_current_user
from app.db.session import get_engine
from app.users.models import User


def paper_permission(request: Request, user: User = Depends(get_current_user)):
    if user.status != 'active':
        raise HTTPException(401, 'Authentication required')
    if request.headers.get('origin') != request.app.state.settings.frontend_url:
        raise HTTPException(403, 'Untrusted request origin')
    return user


def execution_time():
    return datetime.now(timezone.utc)


def paper_transactions(request: Request):
    # Deferred until trusted evidence/ownership is resolved. Transaction exits
    # inside the handler, before a successful HTTP response can be returned.
    def begin():
        return get_engine(str(request.app.state.settings.database_url)).begin()
    return begin
