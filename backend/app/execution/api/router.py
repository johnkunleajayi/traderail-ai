from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from app.users.models import User
from .dependencies import paper_permission, execution_time, paper_transactions
from .trusted import get_trusted_paper_source, TrustedPaperCommandSource
from .schemas import PaperExecutionRequest, PaperExecutionResponse
from .service import run_paper_execution, PaperAPIError

router = APIRouter(prefix='/paper', tags=['paper-execution'])


@router.post('/executions', response_model=PaperExecutionResponse)
def execute(payload: PaperExecutionRequest, user: User = Depends(paper_permission),
            source: TrustedPaperCommandSource = Depends(get_trusted_paper_source),
            begin=Depends(paper_transactions), at: datetime = Depends(execution_time)):
    try:
        return run_paper_execution(source, begin, user.id, payload, at)
    except PaperAPIError as error:
        raise HTTPException(error.status, {'code': error.code, 'message': error.message},
            headers={'Cache-Control': 'no-store'}) from None
