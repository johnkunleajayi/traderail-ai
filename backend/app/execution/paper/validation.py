"""Reconcile approved inputs with locked authoritative PAPER state."""
from app.risk.vocabulary import Direction,Environment
from app.risk.integrated.models import GateName
from app.strategy_lab.numeric import exact_product,exact_sum
from ..paper_fill.pricing import PaperLegCalculation
from app.strategies.decisions import DecisionType
from .contracts import PaperExecutionError


def reconcile(command,book,at):
    r=command.request;i=r.authorization.intent;s=r.authorization.decision.portfolio_snapshot
    if i.direction is not Direction.LONG:raise PaperExecutionError('SHORT portfolio execution unsupported; no margin/collateral model')
    if r.environment is not Environment.PAPER or book.environment!='PAPER':raise PaperExecutionError('PAPER environment required')
    if (i.scope.user_id,i.scope.portfolio_id,i.scope.base_currency)!=(book.user_id,book.portfolio_id,book.base_currency):raise PaperExecutionError('Portfolio/user/currency mismatch')
    if book.status!='active':raise PaperExecutionError('Paper Portfolio inactive')
    if command.executed_at!=at:raise PaperExecutionError('First economic mutation requires the explicit current execution instant')
    r.authorization.validate_at(at)
    if s.valuation_identity!=book.state_identity or s.available_cash!=book.cash_balance:raise PaperExecutionError('Approved snapshot no longer matches authoritative Paper book')
    if s.open_positions is None or {p.position_id for p in s.open_positions}!={p['position_id'] for p in book.positions}:raise PaperExecutionError('Approved positions do not match authoritative book')
    if s.as_of<book.updated_at or not s.as_of<=at<s.expires_at:raise PaperExecutionError('Portfolio snapshot time does not reconcile')
    equity=[book.cash_balance]
    by_id={p['position_id']:p for p in book.positions}
    for p in s.open_positions:
        actual=by_id[p.position_id]
        if (p.instrument_id,p.canonical_symbol,p.direction,p.quantity,p.strategy_configuration_identity)!=(actual['instrument_id'],actual['canonical_symbol'],Direction.LONG,actual['quantity'],actual['strategy_configuration_identity']):raise PaperExecutionError('Position context changed')
        if p.mark_price is None or p.exposure_amount is None or p.open_risk_amount is None:raise PaperExecutionError('Required trusted position evidence unavailable')
        if p.exposure_amount!=exact_product(p.quantity,p.mark_price):raise PaperExecutionError('Position valuation inconsistent')
        if p.instrument_id==i.instrument.instrument_id and p.mark_price!=command.market.reference_price:raise PaperExecutionError('Position mark differs from current trusted reference')
        equity.append(p.exposure_amount)
    if exact_sum(equity)!=s.equity:raise PaperExecutionError('Snapshot equity does not reconcile with trusted book and marks')
    if command.market.source_identity!=i.market_context.data_identity:raise PaperExecutionError('Market evidence source differs from approved data context')
    trade=next(g.result for g in r.authorization.report.gates if g.gate is GateName.TRADE_RISK)
    if command.market.reference_price!=trade.sizing.loss_model.assumed_entry_price:raise PaperExecutionError('Market price changed; fresh risk approval required')
    # Reuse 11B arithmetic to ensure assumptions remain the Risk-approved cost
    # convention. Exit approval remains conservatively subject to all current gates.
    approved_entry=PaperLegCalculation(Direction.LONG,DecisionType.ENTER_LONG,command.market.reference_price,r.quantity,command.assumptions)
    loss=trade.sizing.loss_model
    if (approved_entry.modeled_execution_price!=loss.modeled_adverse_entry_price
        or approved_entry.fee!=exact_product(loss.entry_fee,r.quantity)
        or approved_entry.spread_cost!=exact_product(loss.entry_spread_cost,r.quantity)
        or approved_entry.slippage_cost!=exact_product(loss.entry_slippage_cost,r.quantity)):
        raise PaperExecutionError('Execution costs differ from the approved risk assumptions')
