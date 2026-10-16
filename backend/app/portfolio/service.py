import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.portfolio.models import PaperPortfolio


def ensure_paper_portfolio(db: Session, user_id: uuid.UUID) -> PaperPortfolio:
    """Create if missing without replacing balances; caller owns the transaction."""
    db.execute(
        insert(PaperPortfolio)
        .values(id=uuid.uuid4(), user_id=user_id)
        .on_conflict_do_nothing(index_elements=[PaperPortfolio.user_id])
    )
    # A concurrent insert waits on the unique constraint; READ COMMITTED then sees
    # the winning row. There is deliberately no ON CONFLICT UPDATE/reset.
    portfolio = db.scalar(select(PaperPortfolio).where(PaperPortfolio.user_id == user_id))
    if portfolio is None:
        raise RuntimeError("Paper portfolio could not be initialized")
    return portfolio