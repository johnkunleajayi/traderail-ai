"""Intent-bound PAPER close attribution. Legacy intents retain entry semantics."""
from app.risk.canonical import identity
from app.risk.vocabulary import Direction
from app.strategies.decisions import DecisionType


def intent_action(intent):
    marker=intent.entry_context.get("paper_action")
    if "paper_action" not in intent.entry_context:
        if "paper_position_identity" in intent.entry_context:
            raise ValueError("Position attribution requires an explicit PAPER close proposal")
        return DecisionType.ENTER_LONG if intent.direction is Direction.LONG else DecisionType.ENTER_SHORT
    if marker != DecisionType.EXIT_LONG.value or intent.direction is not Direction.LONG:
        raise ValueError("Unsupported explicit PAPER action")
    identity(intent.entry_context.get("paper_position_identity"),"exact closing position")
    return DecisionType.EXIT_LONG
