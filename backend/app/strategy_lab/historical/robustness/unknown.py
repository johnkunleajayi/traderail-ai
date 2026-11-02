"""Offline classifier diagnostics; no alternative classifier or threshold values."""
import ast
from collections import Counter,defaultdict
from datetime import timedelta
from decimal import Decimal
from functools import lru_cache
import inspect
from app.market_regimes.classification import classify_evidence
from app.market_regimes.config import REGIME_CONFIG
from app.market_regimes.types import Regime
from app.market_intelligence.numeric import calculation_context
from app.strategy_lab.numeric import difference,ratio
from .attribution import atr_band
from .statistics import describe

D=Decimal


@lru_cache(1)
def predicate_expressions():
    """Read ONLY boolean expressions from the trusted canonical Python function.
    They measure condition support; canonical classify_regimes still supplies every label.
    """
    tree=ast.parse(inspect.getsource(classify_evidence));expressions={};clauses={}
    names=('momentum_up','momentum_down','structure_up','structure_down','volatility_confirmation','up','down')
    for node in ast.walk(tree):
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id in names:
            expressions[node.targets[0].id]=node.value
        if isinstance(node,ast.If):
            text=ast.unparse(node.test)
            if 'high_volatility_ratio' in text:expressions['high_volatility']=node.test
            if 'range_spread' in text:expressions['range']=node.test
    if set(expressions)!=set(names)|{'high_volatility','range'}:raise ValueError('Canonical predicate instrumentation needs review')
    # Evaluate in dependency order, never source traversal order.
    expressions={name:expressions[name] for name in (*names,'high_volatility','range')}
    for name,node in expressions.items():
        parts=node.values if isinstance(node,ast.BoolOp) else [node]
        for n,part in enumerate(parts):clauses[name+':'+str(n)+':'+ast.unparse(part)]=part
    def code(node):return compile(ast.Expression(body=node),'canonical-classifier-diagnostic','eval')
    return ({k:code(v) for k,v in expressions.items()},{k:code(v) for k,v in clauses.items()})


def predicate_snapshot(evidence):
    expressions,clauses=predicate_expressions();scope={'e':evidence,'c':REGIME_CONFIG}
    globals={'__builtins__':{},'abs':abs,'max':max}
    result={}
    with calculation_context():
        for name,code in expressions.items():
            try:value=bool(eval(code,globals,scope))
            except (TypeError,ArithmeticError):value=None
            scope[name]=value;result[name]=value
        for name,code in clauses.items():
            try:result[name]=bool(eval(code,globals,scope))
            except (TypeError,ArithmeticError):result[name]=None
    return result


def summarize_regimes(observations,candles,index,end):
    """Observations are canonical revalidated regime/feature snapshots at each time."""
    total=Counter();unknown_reasons=Counter();primary=Counter();candidates=Counter();bands=defaultdict(Counter)
    predicates=defaultdict(Counter);unknown_predicates=defaultdict(Counter);forward={}
    for time,regime,evidence in observations:
        total[regime.value]+=1;band=atr_band(evidence.normalized_atr)
        bands[band]['observations']+=1
        if regime is Regime.UNKNOWN:
            unknown_reasons.update(evidence.reason_codes);candidates[evidence.candidate.value]+=1;bands[band]['unknown']+=1
            primary[next((r for r in ('CONFIRMATION_PENDING','CONFLICTING_EVIDENCE','INSUFFICIENT_EVIDENCE') if r in evidence.reason_codes),'OTHER_RECORDED_REASON')]+=1
        for name,value in predicate_snapshot(evidence).items():
            key='unavailable' if value is None else 'passed' if value else 'failed'
            predicates[name][key]+=1
            if regime is Regime.UNKNOWN:unknown_predicates[name][key]+=1
    for hours,threshold in ((24,D('0.02')),(72,D('0.05'))):
        groups={}
        for unknown in (True,False):
            all_moves=[];spaced=[];next_index=-1
            for time,regime,evidence in observations:
                if (regime is Regime.UNKNOWN)!=unknown:continue
                i=index[time];j=i+hours
                if j>=len(candles) or candles[j].open_time>=end:continue
                move=ratio(difference(candles[j].close,candles[i].close),candles[i].close)
                all_moves.append(move)
                if i>=next_index:spaced.append(move);next_index=i+hours
            def summarize(moves):
                return {'returns':describe(moves),'absolute_moves':describe(v.copy_abs() for v in moves),
                    'absolute_threshold':threshold,'threshold_hits':sum(v.copy_abs()>=threshold for v in moves)}
            groups['UNKNOWN' if unknown else 'OTHER']={'all_anchors_overlapping':summarize(all_moves),'greedy_nonoverlapping_anchors':summarize(spaced)}
        forward[str(hours)+'h']=groups
    return {'counts':dict(total),'unknown_reason_counts':dict(unknown_reasons),'unknown_primary_reason_counts':dict(primary),'unknown_candidate_counts':dict(candidates),
        'atr_bands':{k:{**dict(v),'unknown_fraction':ratio(D(v['unknown']),D(v['observations']))} for k,v in bands.items()},
        'predicate_support':{k:dict(v) for k,v in predicates.items()},
        'predicate_support_when_unknown':{k:dict(v) for k,v in unknown_predicates.items()},
        'predicate_method':'Exact canonical source expressions evaluated independently on measured evidence; branch precedence/labels remain canonical; not a replacement classifier',
        'forward_moves':forward,'forward_limitations':'Hindsight diagnostics only; overlapping anchors and cross-group windows are dependent. Spacing removes overlap within each group, not dependence. Threshold means absolute price move, not statistical significance or profitable opportunity.',
        'warmup':'Every observation has 100 candles; UNKNOWN does not imply missing candles. Actual reason codes distinguish ambiguity and confirmation pending.'}
