"""Explicit research thesis transitions; no automatic reversal or execution."""
from app.strategies.inputs import PositionState
from app.strategies.decisions import DecisionType

_TRANSITIONS = {
    (PositionState.CASH, DecisionType.ENTER_LONG): PositionState.LONG,
    (PositionState.CASH, DecisionType.ENTER_SHORT): PositionState.SHORT,
    (PositionState.LONG, DecisionType.EXIT_LONG): PositionState.CASH,
    (PositionState.SHORT, DecisionType.EXIT_SHORT): PositionState.CASH,
}


def transition_state(state: PositionState, decision: DecisionType) -> PositionState:
    if not isinstance(state, PositionState) or not isinstance(decision, DecisionType):
        raise ValueError("Typed thesis state and strategy decision are required")
    if decision is DecisionType.NO_ACTION:
        return state
    try:
        return _TRANSITIONS[(state, decision)]
    except KeyError:
        raise ValueError("Invalid research thesis transition; reversal is not automatic") from None
