"""Bounded read-only verification of historical artifacts and canonical measurements."""
import json
from dataclasses import replace
from datetime import datetime,timedelta
from pathlib import Path
from app.market_intelligence.engine import calculate_features
from app.market_intelligence.numeric import calculation_context
from app.market_regimes.evidence import measure_evidence
from app.market_regimes.classification import classify_evidence
from app.market_regimes.config import REGIME_CONFIG
from app.market_regimes.types import Regime
from app.strategy_lab.execution.config import identity
from app.strategy_lab.historical.report import json_values,LABEL
from app.strategy_lab.historical.parity.data import file_hash
from app.strategy_lab.historical.parity.report import source_version


def read_json(path,limit=32*1024*1024):
    path=Path(path)
    if path.stat().st_size>limit:raise ValueError('Artifact size limit')
    return json.loads(path.read_text(encoding='utf-8'))


def checked_identity(value,key,namespace):
    body={k:v for k,v in value.items() if k!=key}
    if value.get(key)!=identity(namespace,body):raise ValueError('Artifact identity mismatch')


def material_hashes(source):
    prefixes=('backend/app/market_intelligence/','backend/app/market_regimes/','backend/app/strategies/adaptive_trend/',
        'backend/app/strategy_lab/accounting/','backend/app/strategy_lab/execution/')
    exact=('backend/app/strategy_lab/historical/parity/engine.py','backend/app/strategy_lab/metrics.py',
        'backend/app/strategy_lab/numeric.py','backend/app/strategy_lab/costs.py',
        'backend/app/strategy_lab/benchmark_evaluation/engine.py','backend/app/risk/paper_source/policies.py',
        'backend/app/risk/paper_source/market.py','backend/app/mandate_strategy/pipeline.py')
    clean={p.replace(chr(92),'/'):v for p,v in source['source_hashes'].items()}
    return {p:v for p,v in clean.items() if p.startswith(prefixes) or p in exact}


def verify_report(path,info,protocol):
    report=read_json(path)
    if report.get('status')!='COMPLETED_RESEARCH_ONLY' or report.get('label')!=LABEL:raise ValueError('Completed labelled research required')
    checked_identity(report,'report_identity','historical-parity-report/v1')
    if report['dataset']['normalized_sha256']!=info['normalized_sha256']:raise ValueError('Wrong dataset')
    if report['strategy']['configuration_identity']!=protocol['strategy_configuration_identity']:raise ValueError('Strategy configuration mismatch')
    current=source_version();expected=material_hashes(protocol['baseline_source'])
    if not expected or material_hashes(current)!=expected or material_hashes(report['source'])!=expected:
        raise ValueError('Immutable strategy/financial baseline changed')
    for key in ('capital','quantity','commitment_ceiling','repeated_research_cycles'):
        if report['configuration'][key]!=protocol['settings'][key]:raise ValueError('Protocol settings mismatch')
    old={p.replace(chr(92),'/'):v for p,v in report['source']['source_hashes'].items()}
    new={p.replace(chr(92),'/'):v for p,v in current['source_hashes'].items()}
    return report,{'parent_report_sha256':file_hash(path),'material_source_match':True,
        'metadata_source_drift':sorted(k for k in set(old)|set(new) if old.get(k)!=new.get(k)),
        'baseline_fingerprint':identity('m18-3-baseline/v1',{'source':expected,'settings':protocol['settings'],
            'strategy':protocol['strategy_configuration_identity']})}


def events(directory,period,*,limit=100000):
    name=period['artifacts']['events_file']
    if Path(name).name!=name:raise ValueError('Local artifact basename required')
    path=Path(directory)/name
    if path.stat().st_size>256*1024*1024 or file_hash(path)!=period['artifacts']['sha256']:raise ValueError('Event hash/size mismatch')
    chain=''
    with path.open(encoding='utf-8') as stream:
        for n,line in enumerate(stream):
            if n>=3*limit+1 or len(line)>65536:raise ValueError('Event bound exceeded')
            event=json.loads(line);saved=event.pop('event_identity')
            chain=identity('historical-parity-event/v1',{'previous':chain,'event':event})
            if saved!=chain:raise ValueError('Event chain mismatch')
            yield event
    if chain!=period['event_chain_identity']:raise ValueError('Incomplete event chain')


class Measurements:
    """Cache canonical latest-row measurements, never replace recorded regime labels.
    Reuse calculate_features/measure_evidence/classify_evidence on each 100-bar window.
    This avoids rerunning any fills or backtest and preserves confirmation evidence.
    """
    def __init__(self,context,candles):
        self.context=context;self.candles=candles;self.index={c.open_time:i for i,c in enumerate(candles)};self.cache={}

    def at(self,event):
        time=datetime.fromisoformat(event['timestamp']);opened=time.replace(minute=0,second=0,microsecond=0)
        i=self.index.get(opened)
        if i is None or i<99 or time!=self.candles[i].close_time:raise ValueError('Incomplete observation window')
        if i not in self.cache:
            window=self.candles[i-99:i+1]
            prefix=identity('historical-parity-window/v1',{'context':json_values(self.context),'window':json_values(window)})
            observed=max(window[-1].open_time+timedelta(hours=1),*(c.retrieved_at for c in window))
            features=calculate_features(self.context,window,as_of=observed)
            with calculation_context():
                measured=measure_evidence(features,99,REGIME_CONFIG)
                candidate,evidence=classify_evidence(measured,REGIME_CONFIG)
            self.cache[i]=(prefix,features[-1],candidate,evidence)
        prefix,feature,candidate,evidence=self.cache[i]
        if prefix!=event['prefix_identity'] or event['window_count']!=100:raise ValueError('Canonical window linkage mismatch')
        ev=event['decision']['evidence']
        if ev['candidate_regime']!=candidate.value:raise ValueError('Canonical candidate mismatch')
        for key in ('normalized_ema_spread','ema20_slope','ema50_slope','normalized_macd_histogram','normalized_atr','volatility_ratio','atr_ratio'):
            if json_values(getattr(evidence,key))!=ev[key]:raise ValueError('Recorded measurement mismatch')
        for key in ('close','ema_20','ema_50','rsi_14','macd','macd_signal','macd_histogram','realized_volatility'):
            if json_values(getattr(feature,key))!=ev[key]:raise ValueError('Recorded intelligence mismatch')
        if event['regime']!=ev['regime']:raise ValueError('Regime linkage mismatch')
        reasons=tuple(event['regime_reasons'])
        if reasons[:len(evidence.reason_codes)]!=evidence.reason_codes:raise ValueError('Canonical reason mismatch')
        result=replace(evidence,reason_codes=reasons,confirmation_count=ev['confirmation_count'],
            confirmation_required=ev['regime_confirmation_required'],retained_previous=ev['retained_previous'])
        return self.candles[i].open_time,Regime(event['regime']),result
