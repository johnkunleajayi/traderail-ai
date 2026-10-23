"""No-store, authenticated, trusted-Origin operator inspection."""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Response
from app.users.models import User
from .dependencies import inspection_permission, evaluation_time
from .trusted import get_trusted_risk_source, TrustedRiskSource
from .schemas import EvaluationRequest, EvaluationResponse
from .service import run_inspection, RiskInspectionError
from pydantic import BaseModel,ConfigDict


class ControlledPaperSetup(BaseModel):
    model_config = ConfigDict(extra="forbid")

router = APIRouter(prefix="/risk", tags=["risk-inspection"])


@router.post("/paper-evaluations")
def evaluate_paper(payload: ControlledPaperSetup, response: Response,
                   user: User = Depends(inspection_permission),
                   source = Depends(get_trusted_risk_source),
                   at: datetime = Depends(evaluation_time)):
    """Fixed server-owned first-trade setup; the request body is exactly {}."""
    response.headers["Cache-Control"]="no-store"
    try:
        return source.evaluate_latest(user_id=user.id,evaluated_at=at)
    except Exception:
        raise HTTPException(503,{"code":"trusted_paper_risk_unavailable",
            "message":"Authoritative first-trade PAPER evidence is unavailable"},
            headers={"Cache-Control":"no-store"}) from None


@router.post("/evaluations", response_model=EvaluationResponse)
def evaluate(payload: EvaluationRequest, response: Response,
             user: User = Depends(inspection_permission),
             source: TrustedRiskSource = Depends(get_trusted_risk_source),
             at: datetime = Depends(evaluation_time)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return run_inspection(source, user.id, payload.intent_identity, at)
    except RiskInspectionError as error:
        raise HTTPException(error.status, {"code": error.code, "message": error.message},
            headers={"Cache-Control": "no-store"}) from None
