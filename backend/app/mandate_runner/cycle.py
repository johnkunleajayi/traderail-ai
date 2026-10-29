"""One explicit PAPER cycle. No scheduler, loop, HTTP command or financial math."""
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.mandates.models import TradingMandate
from app.mandates.service import owned
from app.mandates.domain import MandateError
from app.mandate_eligibility.service import inspect_eligibility
from app.mandate_eligibility.contracts import EligibilityStatus
from app.mandate_strategy.contracts import MandateEvaluationError
from app.mandate_risk.contracts import MandateRiskError
from app.mandate_paper.service import run as run_paper
from app.market_data.models import MarketCandle
from app.market_data.completed import ExpectedCandle, ExpectedCandleMismatch
from .market import maintain_market, expected_candle, utc_now
from .repository import ensure_work, load_work, record_attempt, record_result, utc, TERMINAL, barrier_work

RUNNER_VERSION="paper-runner-v1"
RETRY_DELAY=timedelta(seconds=30)

@dataclass(frozen=True)
class CycleResult:
    mandate_id: UUID
    state: Literal["COMPLETED","SKIPPED","RETRY_WAIT","RECONCILIATION_REQUIRED"]
    outcome_code: str
    reason_code: str
    work_id: UUID | None = None
    persisted: bool = False
    reused: bool = False
    registry_identity: str | None = None
    request_identity: str | None = None
    position_id: UUID | None = None


def restored(mandate_id,row):
    return CycleResult(mandate_id,row['state'],row['outcome_code'],row['reason_code'] or 'STORED_RESULT',
        row['work_id'],True,True,row.get('registry_identity'),row.get('request_identity'),row.get('position_id'))


def barrier_result(mandate_id,row):
    if row['state']=='RECONCILIATION_REQUIRED':
        return restored(mandate_id,row)
    return CycleResult(mandate_id,'SKIPPED','POSITION_CLOSED','LIFECYCLE_ALREADY_CLOSED',
        row['work_id'],True,True,row.get('registry_identity'),row.get('request_identity'),row.get('position_id'))


def failure_state(code,status,at,until):
    if code in ('paper_run_reconciliation_required','mandate_authorization_mismatch','mandate_authorization_conflict'):
        return 'RECONCILIATION_REQUIRED','RECONCILIATION_REQUIRED',None
    if status==503 and at+RETRY_DELAY<until:
        return 'RETRY_WAIT','EVIDENCE_OR_SERVICE_UNAVAILABLE',at+RETRY_DELAY
    return 'SKIPPED','EVALUATION_UNAVAILABLE',None


def process_one(session_factory,mandate_id,*,clock=utc_now,provider_factory=None):
    """Owner is read from persisted mandate, never accepted as command input.

    Work and economics commit together. Return persisted=False when no canonical
    candle/fresh work can exist, or when commit outcome cannot be established.
    A later call must consult durable state; it must not trust an uncertain return.
    """
    if not isinstance(mandate_id,UUID):
        raise ValueError('Canonical mandate UUID required')
    work_id=None
    try:
        # Preflight transaction ends before market maintenance can fetch externally.
        with session_factory() as db:
            with db.begin():
                mandate=db.scalar(select(TradingMandate).where(TradingMandate.id==mandate_id))
                if mandate is None:
                    return CycleResult(mandate_id,'SKIPPED','MANDATE_UNAVAILABLE','MANDATE_UNAVAILABLE')
                user_id=mandate.user_id
                barrier=barrier_work(db.connection(),mandate_id)
                if barrier is not None:
                    return barrier_result(mandate_id,barrier)
                eligibility=inspect_eligibility(db,user_id,mandate_id,resolved_at=utc(clock()))
                if eligibility.status is not EligibilityStatus.ELIGIBLE:
                    return CycleResult(mandate_id,'SKIPPED','MANDATE_INELIGIBLE','MANDATE_INELIGIBLE')
        maintenance=maintain_market(session_factory,clock=clock,provider_factory=provider_factory)
        with session_factory() as db:
            with db.begin():
                # Lock order matches manual paper-run: mandate, work, book/position.
                mandate=owned(db,user_id,mandate_id,lock=True)
                at=utc(clock())
                eligibility=inspect_eligibility(db,user_id,mandate_id,resolved_at=at)
                c=db.connection()
                barrier=barrier_work(c,mandate_id,lock=True)
                if barrier is not None:
                    return barrier_result(mandate_id,barrier)
                candle=db.scalar(select(MarketCandle).where(
                    MarketCandle.instrument_id==maintenance.instrument_id,
                    MarketCandle.provider=='BINANCE',MarketCandle.timeframe=='1h',
                    MarketCandle.open_time==maintenance.expected_open_time).with_for_update(read=True))
                if candle is None:
                    # Work requires a real FK candle. Never fabricate a placeholder.
                    if eligibility.status is not EligibilityStatus.ELIGIBLE:
                        return CycleResult(mandate_id,'SKIPPED','MANDATE_INELIGIBLE','LIFECYCLE_OR_POLICY_CHANGED')
                    if at>=maintenance.fresh_until or expected_candle(at)[0]!=maintenance.expected_open_time:
                        return CycleResult(mandate_id,'SKIPPED','STALE_CANDLE','NO_FRESH_WORK_CREATED')
                    if maintenance.status=='INTEGRITY_CONFLICT':
                        return CycleResult(mandate_id,'RECONCILIATION_REQUIRED','MARKET_CONFLICT','STORED_CANDLE_CONFLICT')
                    return CycleResult(mandate_id,'RETRY_WAIT','MARKET_EVIDENCE_UNAVAILABLE',maintenance.reason_code)
                row=load_work(c,mandate_id,candle.id)
                if row is not None and row['state'] in TERMINAL:
                    return restored(mandate_id,row)
                if row is None:
                    if at>=maintenance.fresh_until or expected_candle(at)[0]!=maintenance.expected_open_time:
                        return CycleResult(mandate_id,'SKIPPED','STALE_CANDLE','NO_FRESH_WORK_CREATED')
                    row=ensure_work(c,mandate_id,candle.id,runner_version=RUNNER_VERSION,
                        created_at=at,fresh_until=maintenance.fresh_until)
                work_id=row['work_id']
                def finish(state,outcome,reason,*,retry_at=None,**refs):
                    saved=record_result(c,work_id,state=state,at=utc(clock()),outcome_code=outcome,
                        reason_code=reason,retry_at=retry_at,eligibility_identity=eligibility.eligibility_identity,**refs)
                    return CycleResult(mandate_id,state,outcome,reason,work_id,True,False,
                        saved.get('registry_identity'),saved.get('request_identity'),saved.get('position_id'))
                if eligibility.status is not EligibilityStatus.ELIGIBLE:
                    response=finish('SKIPPED','MANDATE_INELIGIBLE','LIFECYCLE_OR_POLICY_CHANGED')
                elif at>=row['fresh_until'] or expected_candle(at)[0]!=maintenance.expected_open_time:
                    response=finish('SKIPPED','STALE_CANDLE','FRESHNESS_EXPIRED')
                elif maintenance.status=='INTEGRITY_CONFLICT':
                    response=finish('RECONCILIATION_REQUIRED','MARKET_CONFLICT','STORED_CANDLE_CONFLICT')
                elif not maintenance.safe_for_evaluation:
                    retry=at+RETRY_DELAY
                    transient=maintenance.status in ('PROVIDER_FAILED','STORAGE_FAILED') and retry<row['fresh_until']
                    response=finish('RETRY_WAIT' if transient else 'SKIPPED','MARKET_EVIDENCE_UNAVAILABLE',
                        maintenance.reason_code,retry_at=retry if transient else None)
                elif row['retry_at'] is not None and at<row['retry_at']:
                    # RETRY_WAIT is not terminal, but cannot be claimed before due.
                    return CycleResult(mandate_id,'RETRY_WAIT',row['outcome_code'],row['reason_code'],work_id,True,True)
                else:
                    expected=ExpectedCandle(candle.instrument_id,candle.open_time,candle.close_time)
                    if (candle.id!=maintenance.candle_id or candle.close_time!=maintenance.expected_close_time):
                        response=finish('SKIPPED','EXPECTED_CANDLE_MISMATCH','EXPECTED_CANDLE_MISMATCH')
                    else:
                        record_attempt(c,work_id,at=at)
                        def execution_clock():
                            now=utc(clock())
                            if now<at or now>=row['fresh_until'] or expected_candle(now)[0]!=expected.open_time:
                                raise ExpectedCandleMismatch('Execution candle is no longer current')
                            return now
                        try:
                            # Rollback partial authority/economics before persisting
                            # any failed work result. Success shares the outer commit.
                            with db.begin_nested():
                                result=run_paper(db,user_id,mandate_id,evaluated_at=at,
                                    clock=execution_clock,expected_candle=expected)
                                # Even NO_TRADE/HOLD must finish inside freshness.
                                execution_clock()
                                outcome=str(result.status)
                                if outcome not in ('NO_TRADE','RISK_REJECTED','PAPER_EXECUTED','POSITION_HELD','EXIT_RISK_REJECTED','POSITION_CLOSED'):
                                    raise ValueError('Unsupported orchestration outcome')
                                execution=getattr(result,'execution',None)
                                refs=dict(registry_identity=getattr(result,'registry_identity',None),
                                    request_identity=getattr(execution,'request_identity',None),
                                    position_id=getattr(result,'position_id',None) or getattr(execution,'position_id',None))
                                terminal_close=outcome=='POSITION_CLOSED' and result.reused
                                response=finish('SKIPPED' if terminal_close else 'COMPLETED',outcome,
                                    'LIFECYCLE_ALREADY_CLOSED' if terminal_close else 'EVALUATION_COMPLETED',**refs)
                        except ExpectedCandleMismatch:
                            response=finish('SKIPPED','EXPECTED_CANDLE_MISMATCH','EXPECTED_CANDLE_MISMATCH')
                        except (MandateRiskError,MandateError,MandateEvaluationError) as error:
                            failed_at=utc(clock())
                            state,reason,retry=failure_state(error.code,getattr(error,'status',409),failed_at,row['fresh_until'])
                            response=finish(state,reason,reason,retry_at=retry)
                        except SQLAlchemyError:
                            # Failed savepoint is rolled back; no economic effect commits.
                            failed_at=utc(clock())
                            state,reason,retry=failure_state('storage',503,failed_at,row['fresh_until'])
                            response=finish(state,'STORAGE_FAILURE',reason,retry_at=retry)
                        except Exception:
                            response=finish('RECONCILIATION_REQUIRED','EVALUATION_FAILED','UNEXPECTED_EVALUATION_FAILURE')
            # A successful response is returned only after the outer commit.
        return response
    except MandateError:
        return CycleResult(mandate_id,'SKIPPED','MANDATE_UNAVAILABLE','MANDATE_UNAVAILABLE',work_id)
    except (SQLAlchemyError,ValueError,RuntimeError):
        # Includes uncertain COMMIT. Do not claim rollback or repeat execution here.
        return CycleResult(mandate_id,'RECONCILIATION_REQUIRED','DURABLE_STATE_UNAVAILABLE',
            'VERIFY_DURABLE_STATE_BEFORE_RETRY',work_id)
