"""Scoped kill-switch/strategy-health domain. No runtime wiring or side effects."""
from .evaluator import evaluate_safety_gate
from .targets import StrategyTarget,InstrumentTarget,DataTarget,KillSwitchTarget
from .kill_switches import SwitchCause,KillSwitchEvidence,KillSwitchInventory
from .health import StrategyHealth,HealthMetrics,StrategyHealthEvidence,StrategyLifecycleEvidence
from .policy import SafetyGatePolicy
from .models import SAFETY_GATE_VERSION,KillSwitchStatus,SafetyGateStatus,SafetyGateResult

__all__=["evaluate_safety_gate","StrategyTarget","InstrumentTarget","DataTarget","KillSwitchTarget","SwitchCause",
         "KillSwitchEvidence","KillSwitchInventory","StrategyHealth","HealthMetrics","StrategyHealthEvidence",
         "StrategyLifecycleEvidence","SafetyGatePolicy","SAFETY_GATE_VERSION","KillSwitchStatus","SafetyGateStatus","SafetyGateResult"]
