"""Read-only integration clock evidence. No product clock or policy overrides."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import time
from sqlalchemy import text

TOLERANCE=timedelta(seconds=2)
MAX_SAMPLES=3
# Conservative measurement slack; never expands the permitted two-second skew.
READ_SLACK=timedelta(milliseconds=50)


def utc_now():return datetime.now(timezone.utc)


def utc(value):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Aware clock timestamp required')
    return value.astimezone(timezone.utc)


def micros(value):return value//timedelta(microseconds=1)


@dataclass(frozen=True)
class ClockSample:
    client_before: datetime
    server_at: datetime
    client_after: datetime
    elapsed_ns: int
    between_sample_discontinuity: bool = False

    def __post_init__(self):
        for name in ('client_before','server_at','client_after'):
            object.__setattr__(self,name,utc(getattr(self,name)))
        if type(self.elapsed_ns) is not int or self.elapsed_ns<0:
            raise ValueError('Nonnegative monotonic elapsed nanoseconds required')

    @property
    def elapsed(self):return timedelta(microseconds=(self.elapsed_ns+999)//1000)

    @property
    def wall_elapsed(self):return self.client_after-self.client_before

    @property
    def wall_step(self):return (self.between_sample_discontinuity or
        abs(self.wall_elapsed-self.elapsed)>READ_SLACK or self.wall_elapsed<timedelta(0))

    @property
    def lower(self):return self.server_at-self.client_after-READ_SLACK

    @property
    def upper(self):return self.server_at-self.client_before+READ_SLACK

    @property
    def status(self):
        if self.wall_step:return 'INCONCLUSIVE'
        if self.lower>-TOLERANCE and self.upper<TOLERANCE:return 'WITHIN_TOLERANCE'
        if self.lower>=TOLERANCE or self.upper<=-TOLERANCE:return 'OUTSIDE_TOLERANCE'
        return 'INCONCLUSIVE'

    def to_dict(self):
        return {'client_before':self.client_before.isoformat(),'server_at':self.server_at.isoformat(),
            'client_after':self.client_after.isoformat(),'monotonic_elapsed_ns':self.elapsed_ns,
            'wall_elapsed_us':micros(self.wall_elapsed),
            'wall_monotonic_discrepancy_us':micros(self.wall_elapsed-self.elapsed),
            'offset_lower_us':micros(self.lower),'offset_upper_us':micros(self.upper),
            'midpoint_offset_estimate_us':micros(self.server_at-(self.client_before+self.wall_elapsed/2)),
            'status':self.status,'wall_step_detected':self.wall_step,
            'between_sample_discontinuity':self.between_sample_discontinuity}


@dataclass(frozen=True)
class ClockMeasurement:
    samples: tuple[ClockSample,...]

    @property
    def status(self):return self.samples[-1].status

    def to_dict(self):
        return {'time_mode':'REAL_CLOCK_OBSERVATION','status':self.status,
            'reason':'WALL_CLOCK_STEP_OR_SAMPLING_DISCONTINUITY' if self.samples[-1].wall_step else
                'LATENCY_OR_TOLERANCE_UNCERTAINTY' if self.status=='INCONCLUSIVE' else 'BOUNDED_OFFSET_INTERVAL',
            'tolerance_us':micros(TOLERANCE),'measurement_slack_us':micros(READ_SLACK),
            'sample_limit':MAX_SAMPLES,'samples':[sample.to_dict() for sample in self.samples]}


def measure_clock(connection,*,wall_clock=utc_now,monotonic_ns=time.monotonic_ns):
    """At most three SELECTs; only an uncertain interval gets another sample.

    Driver errors propagate: this never retries connections or economic work.
    A wall-clock step or an interval proven outside tolerance stops immediately.
    """
    samples=[];previous_end=None
    for _ in range(MAX_SAMPLES):
        start=monotonic_ns()
        before=utc(wall_clock())
        discontinuity=False
        if previous_end is not None:
            gap=timedelta(microseconds=(start-previous_end)//1000)
            discontinuity=(start<previous_end or abs((before-samples[-1].client_after)-gap)>READ_SLACK)
        server=connection.execute(text('SELECT clock_timestamp()')).scalar_one()
        after=wall_clock()
        end=monotonic_ns()
        elapsed=end-start
        sample=ClockSample(before,server,after,elapsed,discontinuity)
        previous_end=end
        samples.append(sample)
        if sample.wall_step or sample.status!='INCONCLUSIVE':break
    return ClockMeasurement(tuple(samples))


def validate_real_clock_boundaries(measurement,rows_factory):
    """Actual observed UTC selection plus explicitly SYNTHETIC boundary probes.

    Never claims fixtures are fresh stored/live market evidence. No provider, Risk
    or execution call; canonical market analysis rejects stale fixture history.
    """
    from uuid import UUID
    from app.mandate_runner.market import expected_candle
    from app.market_intelligence.types import Candle,DatasetContext
    from app.risk.paper_source.market import analyze,PaperEvidenceUnavailable
    from app.risk.paper_source.policies import MARKET_MAX_AGE,TIMEFRAME
    if measurement.status!='WITHIN_TOLERANCE':raise ValueError('Accepted clock measurement required')
    observed=measurement.samples[-1].client_after
    boundary=observed.replace(minute=0,second=0,microsecond=0)
    hour=timedelta(hours=1);ms=timedelta(milliseconds=1);us=timedelta(microseconds=1)
    iid=UUID(int=1);context=DatasetContext(iid,'BTC/USDT','BINANCE','1h')
    probes=(('ACTUAL_CLIENT_OBSERVATION',observed),('BEFORE_HOUR',boundary-us),
        ('AT_HOUR',boundary),('BEFORE_FRESHNESS_DEADLINE',boundary+MARKET_MAX_AGE[TIMEFRAME]-us),
        ('AT_FRESHNESS_DEADLINE',boundary+MARKET_MAX_AGE[TIMEFRAME]))
    checks=[]
    for label,at in probes:
        opened,closed,until=expected_candle(at)
        floor=at.replace(minute=0,second=0,microsecond=0)
        if (opened,closed,until)!=(floor-hour,floor-ms,floor+MARKET_MAX_AGE[TIMEFRAME]):
            raise ValueError('UTC completed-candle boundary mismatch')
        rows=rows_factory(iid,at)
        candles=tuple(Candle(**{key:r[key] for key in Candle.__dataclass_fields__}) for r in rows[31:131])
        accepted=True
        try:analyze(context,candles,at=at)
        except PaperEvidenceUnavailable:accepted=False
        expected=at<until
        if accepted!=expected:raise ValueError('Canonical freshness boundary mismatch')
        # The prior completed-candle prefix is stale at every probe, including
        # the exact UTC-hour rollover; it must never become current evidence.
        older=rows_factory(iid,at-hour)
        stale=tuple(Candle(**{key:r[key] for key in Candle.__dataclass_fields__}) for r in older[31:131])
        try:analyze(context,stale,at=at)
        except PaperEvidenceUnavailable:pass
        else:raise ValueError('Stale fixture evidence was accepted')
        checks.append({'probe':label,'as_of':at.isoformat(),'expected_open':opened.isoformat(),
            'expected_close':closed.isoformat(),'fresh_until':until.isoformat(),
            'synthetic_current_prefix':'ACCEPTED' if accepted else 'REJECTED_STALE',
            'synthetic_prior_prefix':'REJECTED_STALE'})
    server=measurement.samples[-1].server_at
    server_open,server_close,server_until=expected_candle(server)
    return {'time_mode':'REAL_CLOCK_WITH_SYNTHETIC_BOUNDARY_PROBES',
        'market_evidence_source':'SYNTHETIC_PRIVATE_IN_MEMORY_FIXTURE_NOT_LIVE_OR_STORED_DATA',
        'actual_client_observed_at':observed.isoformat(),'actual_server_observed_at':server.isoformat(),
        'server_expected_open':server_open.isoformat(),'server_expected_close':server_close.isoformat(),
        'server_fresh_until':server_until.isoformat(),'checks':checks}
