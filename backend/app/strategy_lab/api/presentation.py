"""Projection only; all financial measurements come from the shared domain primitives."""
from app.strategy_lab.metrics import drawdown_series
from .schemas import (API_VERSION,MIN_WARMUP_CANDLES,ExperimentResponse,ExperimentMetadata,
    ExecutionResponse,SummaryResponse,TradeResponse,OpenPositionResponse,EquityResponse,
    DecisionResponse,RegimeResponse,UnfilledResponse)
from .service import CompletedExperiment


def experiment_response(completed: CompletedExperiment):
    data,spec,result=completed.dataset,completed.specification,completed.result
    replay,execution,metrics=result.replay,result.execution,result.performance
    configuration=execution.configuration
    model=configuration.cost_model
    metadata=ExperimentMetadata(api_version=API_VERSION,experiment_identity=spec.experiment_id,
        dataset_identity=data.dataset_fingerprint,research_result_identity=result.result_identity,
        strategy_family=replay.strategy_definition.family,strategy_version=replay.strategy_definition.version,
        strategy_configuration_identity=replay.strategy_definition.configuration_identity,
        canonical_symbol=data.context.canonical_symbol,provider=data.context.provider,timeframe=data.context.timeframe,
        requested_candle_count=data.candle_count,candle_count=data.candle_count,warmup_start=data.warmup_start,
        warmup_candle_count=len(data.warmup_candles),required_warmup_candles=MIN_WARMUP_CANDLES,
        evaluation_start=data.evaluation_start,evaluation_end=data.evaluation_end,
        evaluation_candle_count=len(data.evaluation_candles),initial_capital=result.initial_capital,quantity=execution.quantity,
        execution=ExecutionResponse(fee_rate=model.trading_fee_rate,full_spread_rate=model.spread_rate,
            slippage_rate=model.slippage_rate,delay_candles=configuration.delay_candles,
            timing=configuration.configuration()["timing"],cost_model_identity=configuration.cost_model_identity,
            execution_configuration_identity=configuration.configuration_identity),
        accounting_configuration_identity=result.accounting_configuration.configuration_identity,
        market_intelligence_version=replay.feature_configuration.engine_version,
        regime_engine_version=replay.regime_configuration.engine_version)
    summary=SummaryResponse(initial_capital=result.initial_capital,ending_equity=result.equity_curve[-1].total_equity,
        total_return=metrics.total_return,gross_return=metrics.gross_return,net_return=metrics.net_return,
        max_drawdown=metrics.maximum_drawdown,completed_trade_count=metrics.trade_count,win_rate=metrics.win_rate,
        profit_factor=metrics.profit_factor,expectancy=metrics.expectancy,exposure=metrics.exposure_percentage,
        turnover=metrics.turnover,fee_total=metrics.total_fees,spread_cost_total=metrics.total_spread_cost,
        slippage_cost_total=metrics.total_slippage_cost)
    trades=[TradeResponse(trade_identity=row.trade_identity,direction=row.direction,
        entry_decision_time=row.entry_fill.decision_timestamp,entry_fill_time=row.entry_timestamp,
        entry_reference_price=row.entry_reference_price,entry_fill_price=row.entry_fill_price,
        exit_decision_time=row.exit_fill.decision_timestamp,exit_fill_time=row.exit_timestamp,
        exit_reference_price=row.exit_reference_price,exit_fill_price=row.exit_fill_price,quantity=row.quantity,
        gross_pnl=row.gross_pnl,fees=row.costs.fees,spread_cost=row.costs.spread_cost,
        slippage_cost=row.costs.slippage_cost,net_pnl=row.net_pnl,
        entry_reason_codes=row.entry_fill.decision.reason_codes,exit_reason_codes=row.exit_fill.decision.reason_codes)
        for row in result.completed_trades]
    position=result.open_position
    open_position=None if position is None else OpenPositionResponse(direction=position.direction,quantity=position.quantity,
        entry_decision_time=position.entry_fill.decision_timestamp,entry_fill_time=position.entry_fill.eligible_fill_timestamp,
        entry_reference_price=position.entry_reference_price,entry_price=position.entry_price,
        entry_fee_recognized=position.entry_fee_recognized,mark_price=position.mark_price,mark_timestamp=position.mark_timestamp,
        unrealized_pnl=position.unrealized_pnl,reserved_capital=position.reserved_capital,
        restricted_short_proceeds=position.restricted_short_proceeds,entry_reason_codes=position.entry_fill.decision.reason_codes)
    # Same shared initial-capital drawdown convention used by accounting.performance.
    drawdowns=drawdown_series((result.initial_capital,*(row.total_equity for row in result.equity_curve)))[1:]
    equity=[EquityResponse(timestamp=row.timestamp,cash=row.cash,realized_capital=row.realized_capital,
        realized_pnl=row.realized_pnl,unrealized_pnl=row.unrealized_pnl,total_equity=row.total_equity,
        drawdown=drawdown,position_state=row.position_state) for row,drawdown in zip(result.equity_curve,drawdowns)]
    decisions=[DecisionResponse(open_time=row.open_time,timestamp=row.decision.timestamp,
        decision_identity=row.decision.decision_identity,decision=row.decision.decision,
        reason_codes=row.decision.reason_codes,position_before=row.position_before,position_after=row.position_after)
        for row in replay.records]
    regimes=[RegimeResponse(open_time=row.open_time,timestamp=row.regime.close_time,regime=row.regime.regime,
        candidate_regime=row.regime.evidence.candidate,confirmation_count=row.regime.evidence.confirmation_count,
        confirmation_required=row.regime.evidence.confirmation_required,retained_previous=row.regime.evidence.retained_previous,
        previous_regime=row.regime.evidence.previous_regime,reason_codes=row.regime.evidence.reason_codes) for row in replay.records]
    unfilled=[UnfilledResponse(decision_identity=row.decision.decision_identity,decision=row.decision.decision,
        decision_time=row.decision_timestamp,eligible_fill_time=row.eligible_fill_timestamp,filled_state=row.filled_state,
        reason_code=row.reason_code) for row in execution.unfilled_decisions]
    return ExperimentResponse(experiment=metadata,summary=summary,trades=trades,open_position=open_position,
        equity_curve=equity,decisions=decisions,regimes=regimes,unfilled_decisions=unfilled)
