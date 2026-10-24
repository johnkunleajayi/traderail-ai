"""Authenticated user-intent API. No trading action is reachable here."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session
from app.auth.dependencies import get_current_user
from app.db.session import get_session
from app.users.models import User
from . import service
from .domain import MandateError, MandateStatus
from .schemas import MandateIntent, MandateResponse, LifecycleAction

router = APIRouter(prefix="/mandates", tags=["trading-mandates"])


def write_permission(request: Request, user: User = Depends(get_current_user)) -> User:
    if request.headers.get("origin") != request.app.state.settings.frontend_url:
        raise HTTPException(403, "Untrusted request origin")
    return user


def save(db: Session, operation) -> MandateResponse:
    try:
        row = operation()
        db.flush()
        db.refresh(row)
        response = MandateResponse.model_validate(row)
        db.commit()
        return response
    except MandateError as error:
        db.rollback()
        raise HTTPException(error.status, {"code": error.code, "message": str(error)}) from None
    except ValidationError:
        db.rollback()
        raise HTTPException(409, {"code": "mandate_incomplete", "message": "Mandate intent is incomplete or invalid"}) from None
    except Exception:
        db.rollback()
        raise


@router.post("", response_model=MandateResponse, status_code=201)
def create_mandate(payload: MandateIntent, user: User = Depends(write_permission), db: Session = Depends(get_session)):
    return save(db, lambda: service.create(db, user.id, payload))


@router.get("", response_model=list[MandateResponse])
def list_mandates(user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    return service.list_owned(db, user.id)


@router.get("/{mandate_id}", response_model=MandateResponse)
def get_mandate(mandate_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    try:
        return service.owned(db, user.id, mandate_id)
    except MandateError as error:
        raise HTTPException(error.status, {"code": error.code, "message": str(error)}) from None


@router.post("/{mandate_id}/intent", response_model=MandateResponse)
def update_mandate(mandate_id: UUID, payload: MandateIntent, user: User = Depends(write_permission), db: Session = Depends(get_session)):
    return save(db, lambda: service.update(db, user.id, mandate_id, payload))


@router.post("/{mandate_id}/activate", response_model=MandateResponse)
def activate_mandate(mandate_id: UUID, payload: LifecycleAction = LifecycleAction(), user: User = Depends(write_permission), db: Session = Depends(get_session)):
    return save(db, lambda: service.transition(db, user.id, mandate_id, MandateStatus.ACTIVE))


@router.post("/{mandate_id}/pause", response_model=MandateResponse)
def pause_mandate(mandate_id: UUID, payload: LifecycleAction = LifecycleAction(), user: User = Depends(write_permission), db: Session = Depends(get_session)):
    return save(db, lambda: service.transition(db, user.id, mandate_id, MandateStatus.PAUSED))


@router.post("/{mandate_id}/retire", response_model=MandateResponse)
def retire_mandate(mandate_id: UUID, payload: LifecycleAction = LifecycleAction(), user: User = Depends(write_permission), db: Session = Depends(get_session)):
    return save(db, lambda: service.transition(db, user.id, mandate_id, MandateStatus.RETIRED))
