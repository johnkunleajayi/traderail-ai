"""Bounded polling/discovery only. process_one owns the authoritative SQL claim."""
from dataclasses import dataclass
from uuid import UUID
from sqlalchemy import select
from app.mandates.models import TradingMandate
from app.mandates.service import owned
from app.mandate_eligibility.service import inspect_eligibility
from app.mandate_eligibility.contracts import EligibilityStatus
from app.market_data.models import MarketCandle
from app.instruments.models import Instrument
from .models import work
from .repository import barrier_work, load_work, record_result, utc, TERMINAL
from .market import expected_candle, utc_now
from .cycle import process_one

class RunnerStopping(BaseException):
    """Cooperative cancellation must unwind ALL cycle/savepoint transactions."""

@dataclass(frozen=True)
class Candidate:
    mandate_id: UUID
    user_id: UUID


def discover_page(session_factory,after,page_size):
    with session_factory() as db:
        with db.begin():
            query=select(TradingMandate.id,TradingMandate.user_id).where(
                TradingMandate.status=='ACTIVE',TradingMandate.environment=='PAPER')
            if after is not None:query=query.where(TradingMandate.id>after)
            rows=db.execute(query.order_by(TradingMandate.id).limit(page_size)).all()
            return tuple(Candidate(mid,uid) for mid,uid in rows)


def due(session_factory,candidate,at):
    """Advisory read, never a claim. Every condition is rechecked by process_one."""
    opened,_,until=expected_candle(at)
    if at>=until:return False
    with session_factory() as db:
        with db.begin():
            eligibility=inspect_eligibility(db,candidate.user_id,candidate.mandate_id,resolved_at=at)
            if eligibility.status is not EligibilityStatus.ELIGIBLE:return False
            if barrier_work(db.connection(),candidate.mandate_id) is not None:return False
            instrument_id=db.execute(select(Instrument.id).where(Instrument.canonical_symbol=='BTC/USDT',
                Instrument.status=='active',Instrument.market_type=='SPOT')).scalar_one_or_none()
            if instrument_id is None:return False
            candle_id=db.execute(select(MarketCandle.id).where(MarketCandle.instrument_id==instrument_id,
                MarketCandle.provider=='BINANCE',MarketCandle.timeframe=='1h',
                MarketCandle.open_time==opened)).scalar_one_or_none()
            if candle_id is None:return True  # maintenance may supply the completed candle
            row=db.execute(select(work).where(work.c.mandate_id==candidate.mandate_id,
                work.c.candle_id==candle_id)).mappings().one_or_none()
            return row is None or (row['state'] not in TERMINAL and at<row['fresh_until']
                and (row['retry_at'] is None or at>=row['retry_at']))


def recover_uncertain(session_factory,candidate,result,at):
    """No execution: acquire mandate/work locks to settle an uncertain COMMIT.

    Atomic Step 4 guarantees economics cannot commit without its work. Under the
    mandate lock, an absent work row proves that cycle did not commit. Existing
    incomplete work is quarantined durably, not resubmitted. Failure to inspect
    remains unresolved and this process must suppress the mandate.
    """
    with session_factory() as db:
        with db.begin():
            owned(db,candidate.user_id,candidate.mandate_id,lock=True)
            barrier=barrier_work(db.connection(),candidate.mandate_id,lock=True)
            if barrier is not None:return 'BARRIER'
            if result.work_id is None:
                # process_one assigns work_id before it can invoke orchestration.
                return 'NO_EXECUTION_ATTEMPTED' if result.outcome_code=='DURABLE_STATE_UNAVAILABLE' else 'UNRESOLVED'
            row=db.execute(select(work).where(work.c.work_id==result.work_id,
                work.c.mandate_id==candidate.mandate_id).with_for_update()).mappings().one_or_none()
            if row is None:return 'ROLLED_BACK'
            if row['state'] in TERMINAL:return 'COMMITTED'
            record_result(db.connection(),result.work_id,state='RECONCILIATION_REQUIRED',at=at,
                outcome_code='UNCERTAIN_CYCLE',reason_code='OPERATOR_RECONCILIATION_REQUIRED')
            return 'QUARANTINED'


class PaperRunner:
    def __init__(self,session_factory,settings,stop,emit,*,clock=utc_now,processor=process_one,
                 provider_factory=None):
        self.sessions=session_factory;self.settings=settings;self.stop=stop;self.emit=emit
        self.clock=clock;self.processor=processor;self.provider_factory=provider_factory
        self.suppressed=set()

    def checked_clock(self):
        if self.stop.is_set():raise RunnerStopping()
        return utc(self.clock())

    def poll_once(self):
        if not self.settings.paper_runner_enabled or self.stop.is_set():return 0
        at=self.checked_clock()
        if at>=expected_candle(at)[2]:return 0
        after=None;processed=0
        while not self.stop.is_set():
            try:
                page=discover_page(self.sessions,after,self.settings.paper_runner_page_size)
            except Exception:
                self.emit('DISCOVERY_FAILED',reason_code='DATABASE_OR_DISCOVERY_UNAVAILABLE')
                break
            if not page:break
            after=page[-1].mandate_id
            for candidate in page:
                if self.stop.is_set():break
                if candidate.mandate_id in self.suppressed:continue
                try:
                    ready=due(self.sessions,candidate,self.checked_clock())
                except RunnerStopping:
                    raise
                except Exception:
                    # Admission is read-only: retrying this read next poll is safe.
                    self.emit('ADMISSION_FAILED',mandate_id=candidate.mandate_id,reason_code='ELIGIBILITY_OR_STORAGE_UNAVAILABLE')
                    continue
                if not ready:continue
                try:
                    result=self.processor(self.sessions,candidate.mandate_id,clock=self.checked_clock,
                        provider_factory=self.provider_factory)
                    processed+=1
                    self.emit('CYCLE_RESULT',mandate_id=candidate.mandate_id,work_id=result.work_id,
                        state=result.state,outcome_code=result.outcome_code,reason_code=result.reason_code,
                        persisted=result.persisted,reused=result.reused)
                    if result.state=='RECONCILIATION_REQUIRED' and not result.persisted:
                        try:
                            recovered=recover_uncertain(self.sessions,candidate,result,self.checked_clock())
                        except Exception:
                            recovered='UNRESOLVED'
                        self.emit('RECOVERY_RESULT',mandate_id=candidate.mandate_id,reason_code=recovered)
                        # Suppress until process restart or operator intervention.
                        # Restart performs fresh durable discovery; no cached claim.
                        self.suppressed.add(candidate.mandate_id)
                except RunnerStopping:
                    raise
                except Exception:
                    # An unexpected code failure is not an invitation to retry.
                    self.suppressed.add(candidate.mandate_id)
                    self.emit('MANDATE_FAILED',mandate_id=candidate.mandate_id,reason_code='MANDATE_PROCESSING_FAILED')
            if len(page)<self.settings.paper_runner_page_size:break
        return processed

    def serve(self):
        if not self.settings.paper_runner_enabled:
            self.emit('RUNNER_DISABLED');return
        self.emit('RUNNER_STARTED')
        try:
            while not self.stop.is_set():
                self.poll_once()
                self.stop.wait(self.settings.paper_runner_poll_seconds)
        except RunnerStopping:
            pass
        finally:
            self.emit('RUNNER_STOPPED')
