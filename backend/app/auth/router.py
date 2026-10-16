from authlib.integrations.base_client.errors import OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from httpx import HTTPError
from joserfc.errors import JoseError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from app.auth.dependencies import get_current_user, require_auth_configuration
from app.auth.models import AuthSession
from app.auth.session import SESSION_COOKIE, clear_session_cookie, create_session, hash_token, set_session_cookie
from app.core.config import Settings
from app.db.session import get_session
from app.users.models import User
from app.users.schemas import UserResponse
from app.users.service import save_google_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/google")
async def google_login(request: Request, settings: Settings = Depends(require_auth_configuration)):
    request.session.clear()
    try:
        response = await request.app.state.oauth.google.authorize_redirect(
            request, settings.google_redirect_uri, prompt="select_account"
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    except (OAuthError, HTTPError, JoseError, ValueError):
        request.session.clear()
        raise HTTPException(503, "Google authentication is temporarily unavailable") from None


@router.get("/google/callback")
async def google_callback(request: Request, settings: Settings = Depends(require_auth_configuration),
                          db: Session = Depends(get_session)):
    try:
        # State/nonce/PKCE plus OIDC signature, issuer, audience and expiry validation.
        token = await request.app.state.oauth.google.authorize_access_token(request)
    except (OAuthError, HTTPError, JoseError, ValueError):
        raise HTTPException(400, "Google sign-in failed. Please try again.") from None
    finally:
        request.session.clear()
    claims = token.get("userinfo")
    if not claims or not token.get("id_token"):
        raise HTTPException(400, "Google identity could not be verified")
    try:
        user = save_google_user(db, claims)
        # Reauthentication rotates and revokes the current TradeRail session.
        old_token = request.cookies.get(SESSION_COOKIE)
        if old_token and len(old_token) <= 128:
            old_session = db.get(AuthSession, hash_token(old_token))
            if old_session:
                db.delete(old_session)
        session_token = create_session(db, user.id, settings)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Identity conflict. Please try signing in again.") from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Authentication storage is temporarily unavailable") from None
    response = RedirectResponse(settings.frontend_url + "/?auth=success", status_code=303)
    set_session_cookie(response, session_token, settings)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/me", response_model=UserResponse)
def me(response: Response, user: User = Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store"
    return user


@router.post("/logout", status_code=204)
def logout(request: Request, settings: Settings = Depends(require_auth_configuration),
           db: Session = Depends(get_session)):
    # Exact Origin validation protects this cookie-authenticated mutation from CSRF.
    if request.headers.get("origin") != settings.frontend_url:
        raise HTTPException(403, "Untrusted request origin")
    token = request.cookies.get(SESSION_COOKIE)
    if token and len(token) <= 128:
        session = db.get(AuthSession, hash_token(token))
        if session:
            db.delete(session)
            db.commit()
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    clear_session_cookie(response, settings)
    request.session.clear()
    return response