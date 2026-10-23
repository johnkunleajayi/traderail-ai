"""Thin transaction/HTTP adapter. All economics and revalidation remain in 11D."""
from sqlalchemy.exc import SQLAlchemyError
from app.risk.canonical import require, timestamp
from app.risk.vocabulary import Environment, Direction
from app.execution.paper import PaperCommand, execute_paper
from .schemas import PaperExecutionResponse
from app.execution.paper.production import ProductionPaperCommandSource


class PaperAPIError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def _rejection(error):
    # Domain text is used only for classification, never reflected to the client.
    message = str(error).lower()
    if 'transaction failed' in message:
        return PaperAPIError(503, 'paper_storage_unavailable',
            'Paper execution storage is unavailable; reconcile using the same request')
    for needle, code, public in (
        ('insufficient paper cash', 'insufficient_paper_cash', 'Insufficient Paper cash'),
        ('market evidence', 'paper_evidence_invalid', 'Fresh trusted execution evidence is required'),
        ('expired', 'paper_authorization_expired', 'Paper authorization is expired or not yet valid'),
        ('future-dated', 'paper_authorization_expired', 'Paper authorization is expired or not yet valid'),
        ('stale', 'paper_evidence_invalid', 'Fresh trusted execution evidence is required'),
        ('conflict', 'paper_execution_conflict', 'Execution identity conflicts with existing evidence'),
        ('already reserved', 'paper_execution_conflict', 'Execution requires explicit reconciliation'),
    ):
        if needle in message:
            return PaperAPIError(409, code, public)
    return PaperAPIError(409, 'paper_execution_rejected', 'Paper execution was rejected; fresh trusted context may be required')


def run_paper_execution(source, begin, user_id, payload, evaluated_at):
    at = timestamp(evaluated_at)
    if isinstance(source,ProductionPaperCommandSource):
        try:
            with begin() as connection:
                command=source.resolve_in_transaction(connection,user_id=user_id,intent_identity=payload.intent_identity,
                    decision_identity=payload.decision_identity,request_identity=payload.request_identity,evaluated_at=at)
                # The existing execution path participates in this exact transaction;
                # its nested context is a savepoint, not a second connection/commit.
                from contextlib import contextmanager
                @contextmanager
                def same_transaction():
                    yield connection
                class Resolved:
                    def resolve(self,**kwargs): return command
                return run_paper_execution(Resolved(),same_transaction,user_id,payload,at)
        except PaperAPIError:raise
        except LookupError:
            raise PaperAPIError(404,'paper_request_unavailable','Paper execution request is unavailable') from None
        except (ValueError,ArithmeticError) as error:
            raise _rejection(error) from None
        except Exception:
            raise PaperAPIError(503,'trusted_paper_evidence_unavailable','Authoritative Paper execution evidence is unavailable') from None
    try:
        command = source.resolve(user_id=user_id, intent_identity=payload.intent_identity,
            decision_identity=payload.decision_identity, request_identity=payload.request_identity,
            evaluated_at=at)
        require(command, PaperCommand, 'trusted Paper command')
    except Exception:
        raise PaperAPIError(503, 'trusted_paper_evidence_unavailable', 'Authoritative Paper execution evidence is unavailable') from None
    r = command.request
    intent = r.authorization.intent
    if (intent.scope.user_id != user_id or r.intent_identity != payload.intent_identity
            or r.risk_decision_identity != payload.decision_identity or r.request_identity != payload.request_identity):
        raise PaperAPIError(404, 'paper_request_unavailable', 'Paper execution request is unavailable')
    if r.environment is not Environment.PAPER or intent.direction is not Direction.LONG:
        raise PaperAPIError(409, 'paper_action_unsupported', 'Only PAPER LONG/CASH execution is supported')
    try:
        with begin() as connection:
            result = execute_paper(connection, command, as_of=at)
            response = PaperExecutionResponse(
                result_identity=result.result_identity, intent_identity=result.intent_identity,
                decision_identity=result.decision_identity, request_identity=result.request_identity,
                order_identity=result.order_identity, fill_identity=result.fill_identity,
                position_id=result.position_id, position_effect='OPENED' if result.action == 'ENTER_LONG' else 'CLOSED',
                action=result.action, quantity=result.quantity, execution_price=result.execution_price,
                gross_notional=result.gross_notional, fee=result.fee, currency=intent.scope.base_currency,
                cash_after=result.cash_after, gross_realized_pnl=result.gross_realized_pnl,
                net_realized_pnl=result.net_realized_pnl, executed_at=result.executed_at)
        return response
    except SQLAlchemyError:
        raise PaperAPIError(503, 'paper_storage_unavailable', 'Paper execution storage is unavailable; reconcile using the same request') from None
    except (ValueError, ArithmeticError) as error:
        raise _rejection(error) from None
    except Exception:
        raise PaperAPIError(503, 'paper_execution_unavailable', 'Paper execution is unavailable; reconcile using the same request') from None
