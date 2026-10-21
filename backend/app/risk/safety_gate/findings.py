"""Accumulate every blocker; canonical ordering is independent of discovery order."""
from app.risk.canonical import values
from app.risk.reasons import RiskFinding
from app.strategy_lab.canonical import canonical_json


class Findings:
    def __init__(self):
        self.rows={}

    def add(self,code,**observation):
        self.rows.setdefault(code,[]).append(observation)

    def freeze(self):
        return tuple(RiskFinding(code,{"observations":tuple(sorted(self.rows[code],key=lambda row:canonical_json(values(row))))})
                     for code in sorted(self.rows,key=lambda code:code.value))
