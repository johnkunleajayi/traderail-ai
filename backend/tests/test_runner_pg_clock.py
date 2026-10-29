"""Offline measurement and UTC-boundary tests. No PostgreSQL/provider connections."""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from integration import runner_postgres as h
from integration.runner_clock import ClockSample,ClockMeasurement,measure_clock,validate_real_clock_boundaries
from test_runner_pg_harness import target_files

BASE=datetime(2026,10,28,12,tzinfo=timezone.utc)


def sample(*,elapsed_ms=100,offset_ms=0,wall_step_ms=0):
    before=BASE
    return ClockSample(before,before+timedelta(milliseconds=elapsed_ms//2+offset_ms),
        before+timedelta(milliseconds=elapsed_ms+wall_step_ms),elapsed_ms*1000000)


@pytest.mark.parametrize('offset',[0,500,-500,1800,-1800])
def test_low_latency_within_tolerance(offset):
    value=sample(offset_ms=offset)
    assert value.status=='WITHIN_TOLERANCE'
    assert value.lower<=timedelta(milliseconds=offset)<=value.upper


@pytest.mark.parametrize('offset',[3000,-3000,10000,-10000])
def test_proven_clock_offset_outside_tolerance(offset):
    assert sample(offset_ms=offset).status=='OUTSIDE_TOLERANCE'


@pytest.mark.parametrize('offset',[0,1000,-1000])
def test_high_latency_is_uncertainty_not_proven_skew(offset):
    value=sample(elapsed_ms=10000,offset_ms=offset)
    assert value.status=='INCONCLUSIVE' and not value.wall_step
    report=ClockMeasurement((value,)).to_dict()
    assert report['reason']=='LATENCY_OR_TOLERANCE_UNCERTAINTY'


@pytest.mark.parametrize('step',[200,-200,3000,-3000])
def test_wall_clock_step_is_inconclusive_even_if_midpoint_looks_safe(step):
    value=sample(wall_step_ms=step)
    assert value.wall_step and value.status=='INCONCLUSIVE'
    assert ClockMeasurement((value,)).to_dict()['reason']=='WALL_CLOCK_STEP_OR_SAMPLING_DISCONTINUITY'


@pytest.mark.parametrize('offset',[1950,-1950,2000,-2000])
def test_tolerance_edge_requires_entire_interval_to_fit(offset):
    assert sample(elapsed_ms=0,offset_ms=offset).status=='INCONCLUSIVE'


def sampling_doubles(values):
    walls=[];mono=[];servers=[];start=0
    for value in values:
        shift=timedelta(microseconds=start//1000)
        walls.extend((value.client_before+shift,value.client_after+shift))
        servers.append(value.server_at+shift)
        mono.extend((start,start+value.elapsed_ns));start+=value.elapsed_ns+1000000000
    wall=iter(walls);monotonic=iter(mono);server=iter(servers)
    connection=SimpleNamespace(execute=Mock(side_effect=lambda q:SimpleNamespace(scalar_one=lambda:next(server))))
    return connection,lambda:next(wall),lambda:next(monotonic)


def test_read_only_samples_are_bounded_and_inconclusive_fails_closed():
    connection,wall,mono=sampling_doubles([sample(elapsed_ms=10000)]*3)
    report=measure_clock(connection,wall_clock=wall,monotonic_ns=mono)
    assert report.status=='INCONCLUSIVE' and len(report.samples)==3
    assert connection.execute.call_count==3
    assert all(str(call.args[0])=='SELECT clock_timestamp()' for call in connection.execute.call_args_list)


def test_only_latency_uncertainty_gets_another_sample():
    connection,wall,mono=sampling_doubles([sample(elapsed_ms=10000),sample()])
    report=measure_clock(connection,wall_clock=wall,monotonic_ns=mono)
    assert report.status=='WITHIN_TOLERANCE' and len(report.samples)==2


@pytest.mark.parametrize('value',[sample(offset_ms=3000),sample(wall_step_ms=500)])
def test_skew_or_clock_step_does_not_get_retried(value):
    connection,wall,mono=sampling_doubles([value])
    report=measure_clock(connection,wall_clock=wall,monotonic_ns=mono)
    assert len(report.samples)==1 and report.status!='WITHIN_TOLERANCE'


def test_transport_failure_is_not_retried_or_printed(capsys):
    connection=SimpleNamespace(execute=Mock(side_effect=RuntimeError('private connection metadata')))
    with pytest.raises(RuntimeError):measure_clock(connection,wall_clock=lambda:BASE,monotonic_ns=lambda:0)
    connection.execute.assert_called_once()
    assert not capsys.readouterr().out


def test_timestamp_normalization_and_naive_rejection():
    local=timezone(timedelta(hours=1))
    value=ClockSample(BASE.astimezone(local),BASE,BASE,0)
    assert value.client_before==BASE and value.status=='WITHIN_TOLERANCE'
    with pytest.raises(ValueError):ClockSample(BASE.replace(tzinfo=None),BASE,BASE,0)


@pytest.mark.parametrize('minute,second',[(0,0),(9,59),(10,0),(45,0),(59,59)])
def test_real_clock_lane_checks_canonical_boundaries_without_hour_scheduling(minute,second):
    at=BASE.replace(minute=minute,second=second)
    report=ClockMeasurement((ClockSample(at-timedelta(milliseconds=50),at-timedelta(milliseconds=25),at,50000000),))
    result=validate_real_clock_boundaries(report,h.fixture_rows)
    assert result['time_mode']=='REAL_CLOCK_WITH_SYNTHETIC_BOUNDARY_PROBES'
    assert 'NOT_LIVE_OR_STORED' in result['market_evidence_source']
    probes={row['probe']:row for row in result['checks']}
    assert probes['AT_HOUR']['expected_open']==(BASE-timedelta(hours=1)).isoformat()
    assert probes['BEFORE_HOUR']['expected_open']==(BASE-timedelta(hours=2)).isoformat()
    assert probes['AT_HOUR']['synthetic_current_prefix']=='ACCEPTED'
    assert probes['BEFORE_FRESHNESS_DEADLINE']['synthetic_current_prefix']=='ACCEPTED'
    assert probes['AT_FRESHNESS_DEADLINE']['synthetic_current_prefix']=='REJECTED_STALE'
    assert all(row['synthetic_prior_prefix']=='REJECTED_STALE' for row in probes.values())
    expected='ACCEPTED' if minute<10 else 'REJECTED_STALE'
    assert probes['ACTUAL_CLIENT_OBSERVATION']['synthetic_current_prefix']==expected


def test_real_clock_boundary_lane_refuses_inconclusive_measurement():
    factory=Mock(side_effect=AssertionError('Must not create evidence'))
    with pytest.raises(ValueError,match='Accepted clock'):
        validate_real_clock_boundaries(ClockMeasurement((sample(elapsed_ms=10000),)),factory)
    factory.assert_not_called()


@pytest.mark.parametrize('measurement',[ClockMeasurement((sample(elapsed_ms=10000),)),ClockMeasurement((sample(offset_ms=3000),))])
def test_main_retains_failed_measurement_and_stops_before_baseline(target_files,monkeypatch,capsys,measurement):
    connection=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),get_isolation_level=lambda:'READ COMMITTED')
    @contextmanager
    def connect():yield connection
    engine=SimpleNamespace(connect=connect,dispose=lambda:None)
    monkeypatch.setattr(h,'engine_for',lambda *a:engine)
    monkeypatch.setattr(h,'require_public_state',lambda *a:None)
    monkeypatch.setattr(h,'verify_timeouts',lambda *a:None)
    monkeypatch.setattr(h,'start_preflight_watchdog',lambda:SimpleNamespace(cancel=lambda:None))
    monkeypatch.setattr(h,'measure_clock',lambda c:measurement)
    baseline=Mock();monkeypatch.setattr(h,'baseline',baseline)
    output=h.ROOT/'work/clock-tests'
    args=['--phase','preflight','--approval',str(target_files.approval),'--confirm-child',
        target_files.data['child_branch_id'],'--artifacts',str(output)]
    assert h.main(args)==1
    baseline.assert_not_called()
    saved=json.loads(next(output.glob('*-clock-measurement.json')).read_text())
    assert saved['measurement']['status']==measurement.status
    assert saved['measurement']['time_mode']=='REAL_CLOCK_OBSERVATION'
    text=capsys.readouterr().out
    assert 'CLOCK_MEASUREMENT_'+measurement.status in text and 'SERVER_CLIENT_CLOCK_SKEW' not in text
    assert 'neon.tech' not in text and 'password' not in text


def test_deterministic_safety_phase_never_measures_client_clock(target_files,monkeypatch):
    from app.mandate_runner import runtime
    connection=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),get_isolation_level=lambda:'READ COMMITTED',
        execute=lambda q:SimpleNamespace(scalar_one=lambda:BASE))
    @contextmanager
    def connect():yield connection
    engine=SimpleNamespace(connect=connect,dispose=lambda:None)
    monkeypatch.setattr(h,'engine_for',lambda *a:engine)
    monkeypatch.setattr(h,'require_public_state',lambda *a:None)
    monkeypatch.setattr(h,'baseline',lambda e:{})
    monkeypatch.setattr(h,'make_lab',lambda *a:None)
    monkeypatch.setattr(runtime,'verify_schema',lambda e:None)
    seed=Mock(return_value={});monkeypatch.setattr(h,'seed',seed)
    monkeypatch.setattr(h,'contracts',lambda *a,**kwargs:None)
    measure=Mock(side_effect=AssertionError('No real-clock fallback'));monkeypatch.setattr(h,'measure_clock',measure)
    output=h.ROOT/'work/deterministic';output.mkdir()
    (output/'baseline.json').write_text(json.dumps({'public_baseline':{},'target_sha256':target_files.data['target_sha256'],
        'child_branch_id':target_files.data['child_branch_id']}))
    args=['--phase','contracts','--approval',str(target_files.approval),'--confirm-child',
        target_files.data['child_branch_id'],'--artifacts',str(output)]
    assert h.main(args)==0
    measure.assert_not_called()
    manifest=json.loads(next(output.glob('*-fixture-manifest.json')).read_text())
    assert manifest['scenario']['time_mode']=='SIMULATED_HISTORICAL'
    assert seed.call_args.args[1]==h.ScenarioTimeline()



def test_clock_step_between_samples_cannot_be_hidden_by_a_good_next_interval():
    connection,wall,mono=sampling_doubles([sample(elapsed_ms=10000),sample()])
    readings=[wall() for _ in range(4)]
    readings[2]+=timedelta(seconds=1);readings[3]+=timedelta(seconds=1)
    shifted=iter(readings)
    report=measure_clock(connection,wall_clock=lambda:next(shifted),monotonic_ns=mono)
    assert report.status=='INCONCLUSIVE' and report.samples[-1].between_sample_discontinuity


def test_later_sample_can_establish_outside_tolerance_after_latency_uncertainty():
    connection,wall,mono=sampling_doubles([sample(elapsed_ms=10000),sample(offset_ms=3000)])
    report=measure_clock(connection,wall_clock=wall,monotonic_ns=mono)
    assert report.status=='OUTSIDE_TOLERANCE' and len(report.samples)==2
