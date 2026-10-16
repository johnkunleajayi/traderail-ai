from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_session
from app.portfolio.schemas import PaperPortfolioResponse
from app.portfolio.service import ensure_paper_portfolio
from app.users.models import User

router = APIRouter(tags=["portfolio"])


@router.get("/portfolio", response_model=PaperPortfolioResponse)
def get_portfolio(response: Response, user: User = Depends(get_current_user),
                  db: Session = Depends(get_session)):
    portfolio = ensure_paper_portfolio(db, user.id)
    result = PaperPortfolioResponse.model_validate(portfolio)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result