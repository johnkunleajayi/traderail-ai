"""Owned, read-only mandate bridge. No Risk, authorization or execution service."""
from decimal import DecimalException
from sqlalchemy import select
from app.portfolio.models import PaperPortfolio
from app.execution.paper.book import capture_book
from app.mandate_eligibility.service import inspect_eligibility
from app.risk.paper_source.market import PaperEvidenceUnavailable
from .contracts import MandateEvaluationError
from .pipeline import require_eligible, observe


def cash_context(db, user_id, intent):
    portfolio = db.scalar(select(PaperPortfolio.id).where(PaperPortfolio.user_id == user_id))
    if portfolio is None:
        raise MandateEvaluationError("position_context_unavailable", "An authoritative Paper portfolio is required")
    try:
        book = capture_book(db.connection(), portfolio, lock=False)
    except (ValueError, DecimalException):
        raise MandateEvaluationError("position_context_unavailable", "Authoritative Paper position context could not be reconstructed") from None
    if (book.user_id != user_id or book.environment != intent.environment or book.base_currency != intent.allocation_currency
        or book.status != "active" or book.positions):
        raise MandateEvaluationError("position_context_unavailable", "A verified CASH context is required; mandate-specific held-position attribution is not available")
    return book


def evaluate_mandate(db, user_id, mandate_id, *, evaluated_at):
    eligibility = inspect_eligibility(db, user_id, mandate_id, resolved_at=evaluated_at)
    # Must precede book/market reads and any analytical work.
    require_eligible(eligibility)
    book = cash_context(db, user_id, eligibility.user_intent)
    try:
        return observe(db.connection(), eligibility, book, at=evaluated_at)
    except MandateEvaluationError:
        raise
    except (PaperEvidenceUnavailable, ValueError, DecimalException, OverflowError):
        raise MandateEvaluationError("market_evidence_unavailable",
            "Stored BTC/USDT 1h evidence is missing, stale, future-dated, insufficient or invalid. Refresh history explicitly before retrying.") from None
