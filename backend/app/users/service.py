from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.users.models import User


def save_google_user(db: Session, claims: dict) -> User:
    # Authlib has already verified the ID token signature, issuer, audience and nonce.
    google_id = claims.get("sub")
    email = claims.get("email")
    if (claims.get("email_verified") is not True or not isinstance(google_id, str)
            or not google_id or len(google_id) > 255 or not isinstance(email, str)
            or "@" not in email or len(email) > 320):
        raise HTTPException(400, "Google identity could not be verified")
    email = email.strip().lower()
    user = db.scalar(select(User).where(User.google_id == google_id))
    email_owner = db.scalar(select(User).where(User.email == email))
    if email_owner is not None and (user is None or email_owner.id != user.id):
        # Never link accounts on matching email alone.
        raise HTTPException(409, "This email is already associated with another identity")
    if user is not None and user.status != "active":
        raise HTTPException(403, "Account is unavailable")
    name = claims.get("name")
    name = name.strip()[:255] if isinstance(name, str) and name.strip() else email
    avatar = claims.get("picture")
    if (not isinstance(avatar, str) or len(avatar) > 2048
            or urlsplit(avatar).scheme != "https" or not urlsplit(avatar).hostname
            or urlsplit(avatar).username or urlsplit(avatar).password):
        avatar = None
    if user is None:
        user = User(google_id=google_id, email=email, name=name, avatar_url=avatar)
        db.add(user)
    else:
        user.email, user.name, user.avatar_url = email, name, avatar
    db.flush()
    return user