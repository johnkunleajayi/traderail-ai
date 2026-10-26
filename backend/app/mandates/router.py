"""Owned mandate intent, inspection and explicitly requested PAPER entry actions."""
from uuid import UUID
from datetime import datetime
from app.mandate_eligibility.contracts import EligibilityResult
from app.mandate_eligibility.service import inspect_eligibility, resolution_time
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session
from app.auth.dependencies import get_current_user
from app.db.session import get_session
from app.users.models import User
from app.mandate_strategy.contracts import MandateEvaluationError, MandateObservationResult
from app.mandate_strategy.service import evaluate_mandate
from app.mandate_risk.contracts import MandateRiskResult, MandateRiskError
from app.mandate_paper.contracts import PositionRunResult, PaperRunResult
from app.mandate_performance.contracts import Performance
from app.mandate_paper.service import execution_clock
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


@router.get("/{mandate_id}/eligibility", response_model=EligibilityResult)
def mandate_eligibility(mandate_id: UUID, user: User = Depends(get_current_user),
                        db: Session = Depends(get_session), at: datetime = Depends(resolution_time)):
    try:
        return inspect_eligibility(db, user.id, mandate_id, resolved_at=at)
    except MandateError as error:
        raise HTTPException(error.status, {"code": error.code, "message": str(error)}) from None


@router.post("/{mandate_id}/evaluate", response_model=MandateObservationResult)
def evaluate_mandate_strategy(mandate_id: UUID, payload: LifecycleAction = LifecycleAction(),
        user: User = Depends(write_permission), db: Session = Depends(get_session),
        at: datetime = Depends(resolution_time)):
    try:
        return evaluate_mandate(db, user.id, mandate_id, evaluated_at=at)
    except MandateError as error:
        raise HTTPException(error.status, {"code": error.code, "message": str(error)}) from None
    except MandateEvaluationError as error:
        raise HTTPException(409, {"code": error.code, "message": str(error), "reason_codes": error.reasons}) from None


@router.post("/{mandate_id}/risk-evaluate", response_model=MandateRiskResult)
def evaluate_mandate_risk(mandate_id: UUID, payload: LifecycleAction = LifecycleAction(),
        user: User = Depends(write_permission), db: Session = Depends(get_session),
        at: datetime = Depends(resolution_time)):
    from app.mandate_risk.service import evaluate
    try:
        result = evaluate(db, user.id, mandate_id, evaluated_at=at)
        db.commit()
        return result
    except MandateRiskError as error:
        db.rollback()
        raise HTTPException(error.status, {"code": error.code, "message": str(error)}) from None
    except MandateError as error:
        db.rollback()
        raise HTTPException(error.status, {"code": error.code, "message": str(error)}) from None
    except MandateEvaluationError as error:
        db.rollback()
        raise HTTPException(409, {"code": error.code, "message": str(error), "reason_codes": error.reasons}) from None
    except Exception:
        db.rollback()
        raise


@router.post("/{mandate_id}/paper-run", response_model=PaperRunResult | PositionRunResult)
def run_mandate_paper(mandate_id: UUID, payload: LifecycleAction = LifecycleAction(),
        user: User = Depends(write_permission), db: Session = Depends(get_session),
        at: datetime = Depends(resolution_time), clock = Depends(execution_clock)):
    from app.mandate_paper.service import run
    try:
        result=run(db,user.id,mandate_id,evaluated_at=at,clock=clock)
        db.commit()
        return result
    except MandateRiskError as error:
        db.rollback()
        raise HTTPException(error.status,{"code":error.code,"message":str(error)}) from None
    except MandateError as error:
        db.rollback()
        raise HTTPException(error.status,{"code":error.code,"message":str(error)}) from None
    except MandateEvaluationError as error:
        db.rollback()
        raise HTTPException(409,{"code":error.code,"message":str(error),"reason_codes":error.reasons}) from None
    except Exception:
        db.rollback()
        raise


@router.get("/{mandate_id}/performance", response_model=Performance)
def mandate_performance(mandate_id: UUID, response: Response,
        user: User = Depends(get_current_user), db: Session = Depends(get_session, use_cache=False),
        at: datetime = Depends(resolution_time)):
    # Authentication uses its existing session; reporting needs a fresh read-only snapshot.
    from app.mandate_performance.service import inspect
    response.headers['Cache-Control']='no-store'
    try:
        return inspect(db,user.id,mandate_id,evaluated_at=at)
    except MandateError as error:
        raise HTTPException(error.status,{'code':error.code,'message':str(error)},headers={'Cache-Control':'no-store'}) from None
    except Exception:
        raise HTTPException(503,{'code':'mandate_performance_unavailable',
            'message':'Authoritative mandate reporting evidence is unavailable'},headers={'Cache-Control':'no-store'}) from None
