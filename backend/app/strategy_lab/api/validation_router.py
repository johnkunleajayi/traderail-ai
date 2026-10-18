"""Authenticated API adapter; policy and probes are server-owned research inputs."""
from datetime import datetime
from fastapi import APIRouter,Depends,HTTPException,Response
from sqlalchemy.orm import Session
from app.db.session import get_session
from .router import experiment_permission,experiment_time
from .service import ExperimentError
from .validation_request import ValidationRequest
from .validation_response import ValidationResponse
from .validation_service import run_stored_validation
from .validation_presentation import validation_response

router=APIRouter(prefix="/strategy-lab",tags=["strategy-lab"])


@router.post("/validations",response_model=ValidationResponse,dependencies=[Depends(experiment_permission)])
def validation(payload:ValidationRequest,response:Response,db:Session=Depends(get_session),as_of:datetime=Depends(experiment_time)):
    response.headers['Cache-Control']='no-store'
    try:
        return validation_response(run_stored_validation(db,payload,as_of=as_of))
    except ExperimentError as error:
        raise HTTPException(error.status,{'code':error.code,'message':error.message},headers={'Cache-Control':'no-store'}) from None
