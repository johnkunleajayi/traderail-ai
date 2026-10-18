"""Read stored rows, freeze an explicit dataset, compose the existing research pipeline."""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.instruments.models import Instrument
from app.market_data.models import MarketCandle
from app.market_intelligence.types import Candle, DatasetContext, InvalidCandleData, TIMEFRAME_SECONDS
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
from app.strategy_lab.dataset import create_dataset, ResearchDataset
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.replay import replay_dataset
from app.strategy_lab.replay.types import REPLAY_VERSION
from app.strategy_lab.execution import simulate_execution
from app.strategy_lab.accounting import account_simulation, ResearchResult
from app.strategy_lab.accounting.config import ACCOUNTING_CONFIG
from app.strategy_lab.experiment import create_experiment, ExperimentSpecification
from .schemas import ExperimentRequest, MIN_WARMUP_CANDLES, API_VERSION


class ExperimentError(ValueError):
    def __init__(self,status,code,message):
        self.status,self.code,self.message=status,code,message
        super().__init__(code)


@dataclass(frozen=True)
class CompletedExperiment:
    dataset: ResearchDataset
    specification: ExperimentSpecification
    result: ResearchResult


def run_stored_experiment(db: Session, request: ExperimentRequest, *, as_of: datetime):
    if request.evaluation_end > as_of:
        raise ExperimentError(422,"invalid_evaluation_boundary","Evaluation end must describe completed history")
    instrument=db.scalar(select(Instrument).where(Instrument.canonical_symbol==request.canonical_symbol).execution_options(autoflush=False))
    if instrument is None:
        raise ExperimentError(404,"instrument_not_found","Canonical instrument not found")
    query=select(MarketCandle).where(MarketCandle.instrument_id==instrument.id,
        MarketCandle.provider==request.provider,MarketCandle.timeframe==request.timeframe,
        MarketCandle.open_time < request.evaluation_end).order_by(MarketCandle.open_time.desc()).limit(request.requested_candle_count)
    stored=db.scalars(query.execution_options(autoflush=False)).all()
    if len(stored)!=request.requested_candle_count:
        raise ExperimentError(409,"insufficient_stored_history","The exact requested history is unavailable; no history was ingested")
    candles=tuple(Candle(**{field:getattr(row,field) for field in Candle.__dataclass_fields__}) for row in reversed(stored))
    duration=timedelta(seconds=TIMEFRAME_SECONDS[request.timeframe])
    if (candles[-1].open_time+duration != request.evaluation_end
        or not any(row.open_time==request.evaluation_start for row in candles)):
        raise ExperimentError(422,"invalid_evaluation_boundary","Evaluation boundaries must match the supplied stored candle window")
    if sum(row.open_time < request.evaluation_start for row in candles) < MIN_WARMUP_CANDLES:
        raise ExperimentError(409,"insufficient_warmup","At least 52 requested candles must precede evaluation; no hidden history is added")
    context=DatasetContext(instrument.id,instrument.canonical_symbol,request.provider,request.timeframe)
    try:
        dataset=create_dataset(context,candles,warmup_start=candles[0].open_time,
            evaluation_start=request.evaluation_start,evaluation_end=request.evaluation_end,as_of=as_of)
    except InvalidCandleData as error:
        raise ExperimentError(409,"invalid_candle_dataset","Stored candles failed research validation: "+error.code) from None
    except ValueError:
        raise ExperimentError(409,"invalid_candle_dataset","Stored candles failed research validation") from None
    strategy=AdaptiveTrendStrategy()  # Fixed family/version/defaults; request cannot supply parameters.
    model=ResearchCostModel(request.execution.fee_rate,request.execution.full_spread_rate,
        request.execution.slippage_rate,duration*request.execution.delay_candles)
    try:
        replay=replay_dataset(dataset,strategy,as_of=as_of)
        execution=simulate_execution(dataset,replay,quantity=request.quantity,cost_model=model,as_of=as_of)
        result=account_simulation(dataset,replay,execution,initial_capital=request.initial_capital,as_of=as_of)
    except (ValueError,ArithmeticError):
        raise ExperimentError(409,"research_execution_failed","Research replay, execution or accounting could not complete with the declared assumptions") from None
    specification=create_experiment(dataset,strategy_family=strategy.definition.family,strategy_version=strategy.definition.version,
        created_at=as_of,strategy_configuration={"api_version":API_VERSION,
            "strategy":dict(strategy.definition.parameters),"strategy_configuration_identity":strategy.definition.configuration_identity,
            "execution":execution.configuration.configuration(),"accounting":asdict(ACCOUNTING_CONFIG),
            "initial_capital":request.initial_capital,"quantity":request.quantity,
            "requested_candle_count":request.requested_candle_count,"replay_version":REPLAY_VERSION,"initial_state":"CASH"})
    return CompletedExperiment(dataset,specification,result)
