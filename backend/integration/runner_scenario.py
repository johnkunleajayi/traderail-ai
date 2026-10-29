"""Integration-only historical time. Never an application or database clock override."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True, slots=True)
class ScenarioClock:
    at: datetime

    def __post_init__(self):
        if type(self.at) is not datetime or self.at.tzinfo is None or self.at.utcoffset()!=timedelta(0):
            raise ValueError("Explicit UTC scenario time required")

    def __call__(self):
        return self.at


@dataclass(frozen=True, slots=True)
class ScenarioTimeline:
    """9C long_profit fixture: entry candle 130, HOLD 131, final exit 218.

    Database recording timestamps and statement_timestamp() remain real. These
    explicit times describe simulated trading events in disposable schemas only.
    """
    entry_at: datetime = datetime(2026, 1, 15, 12, 1, tzinfo=timezone.utc)

    def __post_init__(self):
        ScenarioClock(self.entry_at)
        if (self.entry_at.minute,self.entry_at.second,self.entry_at.microsecond)!=(1,0,0):
            raise ValueError("Scenario entry must be one minute after its UTC boundary")

    @property
    def hold_at(self):return self.entry_at+timedelta(hours=1)

    @property
    def exit_at(self):return self.entry_at+timedelta(hours=88)

    @property
    def retry_at(self):return self.entry_at+timedelta(seconds=30)

    @property
    def stale_at(self):return self.entry_at.replace(minute=10)

    @property
    def expired_at(self):return self.entry_at+timedelta(minutes=5)

    @property
    def terminal_retry_at(self):return self.exit_at+timedelta(days=2)

    def clock(self,at):
        return ScenarioClock(at)

    def to_dict(self):
        return {'version':'runner-pg-scenario-v1','time_mode':'SIMULATED_HISTORICAL',
            **{name:getattr(self,name).isoformat() for name in (
                'entry_at','hold_at','exit_at','retry_at','stale_at','expired_at','terminal_retry_at')}}

    @classmethod
    def from_dict(cls,value):
        scenario=cls(datetime.fromisoformat(value['entry_at']))
        if scenario.to_dict()!=value:raise ValueError("Exact scenario version and timeline required")
        return scenario
