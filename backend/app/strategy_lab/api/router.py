"""Authenticated, trusted-origin, synchronous stored-history research only."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from app.auth.dependencies import get_current_user
from app.db.session import get_session
from app.users.models import User
from .schemas import ExperimentRequest, ExperimentResponse
from .service import run_stored_experiment, ExperimentError
from .presentation import experiment_response

router=APIRouter(prefix="/strategy-lab",tags=["strategy-lab"])


def experiment_permission(request: Request,user: User=Depends(get_current_user)):
    if request.headers.get("origin") != request.app.state.settings.frontend_url:
        raise HTTPException(403,"Untrusted request origin",headers={"Cache-Control":"no-store"})
    return user


def experiment_time():
    return datetime.now(timezone.utc)


@router.post("/experiments",response_model=ExperimentResponse,dependencies=[Depends(experiment_permission)])
def experiment(payload: ExperimentRequest,response: Response,db: Session=Depends(get_session),
               as_of: datetime=Depends(experiment_time)):
    response.headers["Cache-Control"]="no-store"
    try:
        return experiment_response(run_stored_experiment(db,payload,as_of=as_of))
    except ExperimentError as error:
        raise HTTPException(error.status,{"code":error.code,"message":error.message},
            headers={"Cache-Control":"no-store"}) from None
