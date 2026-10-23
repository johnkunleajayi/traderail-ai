"""Versioned data codec. Fixed type allowlist, no pickle/eval or payload imports.

Every constructor and every derived field/identity is revalidated on recovery.
Tagged maps preserve Decimal vs string and enum keys in nested evidence.
"""
import importlib
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from functools import lru_cache
from uuid import UUID
from app.risk.canonical import timestamp

MODULES = (
    'app.risk.context','app.risk.intent','app.risk.decision','app.risk.configuration',
    'app.risk.portfolio','app.risk.reasons','app.risk.vocabulary',
    'app.risk.trade_risk.assumptions','app.risk.trade_risk.models',
    'app.risk.portfolio_risk.models','app.risk.portfolio_risk.policy',
    'app.risk.portfolio_risk.context','app.risk.portfolio_risk.correlation',
    'app.risk.risk_states.models','app.risk.risk_states.policy','app.risk.risk_states.context',
    'app.risk.risk_states.measurement','app.risk.safety_gate.models','app.risk.safety_gate.policy',
    'app.risk.safety_gate.targets','app.risk.safety_gate.kill_switches','app.risk.safety_gate.health',
    'app.risk.integrated.inputs','app.risk.integrated.models','app.risk.integrated.inspection',
    'app.market_intelligence.types','app.market_regimes.types','app.strategies.decisions',
    'app.strategies.configuration','app.strategy_lab.experiment',
    'app.strategy_lab.lifecycle','app.strategy_lab.validation_evidence.models','app.strategy_lab.costs',
    'app.execution.authorization','app.execution.request','app.execution.paper.contracts',
    'app.execution.paper_fill.context','app.execution.registry.contracts',
)


@lru_cache
def types():
    result={}
    for module_name in MODULES:
        module=importlib.import_module(module_name)
        for name,kind in vars(module).items():
            if isinstance(kind,type) and kind.__module__==module_name and (is_dataclass(kind) or issubclass(kind,Enum)):
                result[module_name+'.'+name]=kind
    return result


def encode(value):
    if isinstance(value,Enum):return ['enum',_name(type(value)),value.value]
    if value is None or type(value) in (bool,int,str):return ['scalar',value]
    if isinstance(value,Decimal):
        if not value.is_finite():raise ValueError('Nonfinite registry Decimal')
        return ['decimal',str(value)]
    if isinstance(value,datetime):return ['utc',timestamp(value).isoformat()]
    if isinstance(value,timedelta):return ['duration',(value.days*86400+value.seconds)*1000000+value.microseconds]
    if isinstance(value,UUID):return ['uuid',str(value)]
    if is_dataclass(value) and not isinstance(value,type):
        return ['record',_name(type(value)),{f.name:encode(getattr(value,f.name)) for f in fields(value)}]
    if isinstance(value,Mapping):
        pairs=[[encode(k),encode(v)] for k,v in value.items()]
        return ['map',sorted(pairs,key=lambda p:json.dumps(p[0],sort_keys=True))]
    if isinstance(value,(tuple,list)):return ['sequence',[encode(v) for v in value]]
    raise ValueError('Unsupported registry data type')


def _name(kind):
    name=kind.__module__+'.'+kind.__name__
    if types().get(name) is not kind:raise ValueError('Registry type is not allowlisted')
    return name


def decode(node):
    if not isinstance(node,list) or not node:raise ValueError('Malformed registry data')
    tag=node[0]
    if tag=='scalar' and len(node)==2 and (node[1] is None or type(node[1]) in (bool,int,str)):return node[1]
    if tag=='decimal' and len(node)==2:
        value=Decimal(node[1])
        if not value.is_finite():raise ValueError('Nonfinite registry Decimal')
        return value
    if tag=='utc' and len(node)==2:return timestamp(datetime.fromisoformat(node[1]))
    if tag=='duration' and len(node)==2 and type(node[1]) is int:return timedelta(microseconds=node[1])
    if tag=='uuid' and len(node)==2:return UUID(node[1])
    if tag=='sequence' and len(node)==2:return tuple(decode(v) for v in node[1])
    if tag=='map' and len(node)==2:
        pairs=[(decode(k),decode(v)) for k,v in node[1]]
        result=dict(pairs)
        if len(result)!=len(pairs):raise ValueError('Duplicate registry map key')
        return result
    if tag=='enum' and len(node)==3:
        kind=types().get(node[1])
        if kind is None or not issubclass(kind,Enum):raise ValueError('Unknown registry enum')
        return kind(node[2])
    if tag=='record' and len(node)==3:
        kind=types().get(node[1])
        if kind is None or not is_dataclass(kind):raise ValueError('Unknown registry record')
        fs=fields(kind)
        if set(node[2])!={f.name for f in fs}:raise ValueError('Registry record fields differ')
        if node[1]=='app.strategies.configuration.StrategyDefinition':
            result=kind(**{name:decode(node[2][name]) for name in ('family','version','parameters')})
        else:
            result=kind(**{f.name:decode(node[2][f.name]) for f in fs if f.init})
        if encode(result)!=node:raise ValueError('Registry derived fields or identities differ')
        return result
    raise ValueError('Malformed registry tag')
