import type { ResearchData } from './research'
import type { Timeframe } from '../history/api'

export interface ExecutionAssumptions {
  fee_rate: string
  full_spread_rate: string
  slippage_rate: string
  delay_candles: number
}
export interface ExperimentRequest {
  canonical_symbol: string
  provider: 'BINANCE'
  timeframe: Timeframe
  requested_candle_count: number
  evaluation_start: string
  evaluation_end: string
  initial_capital: string
  quantity: string
  execution: ExecutionAssumptions
  strategy_family: 'ADAPTIVE_TREND'
  strategy_version: '1.0.0'
}
export interface ExperimentMetadata extends Omit<ExperimentRequest, 'execution'> {
  experiment_identity: string
  dataset_identity: string
  research_result_identity: string
  strategy_configuration_identity: string
  candle_count: number
  warmup_start: string
  warmup_candle_count: number
  required_warmup_candles: number
  evaluation_candle_count: number
  execution: ExecutionAssumptions & {
    timing: string
    cost_model_identity: string
    execution_configuration_identity: string
  }
}
export const metricKeys = ['ending_equity', 'total_return', 'net_return', 'max_drawdown',
  'win_rate', 'profit_factor', 'expectancy', 'exposure', 'turnover', 'fee_total',
  'spread_cost_total', 'slippage_cost_total'] as const
export type MetricKey = typeof metricKeys[number]
export type ExperimentSummary = Record<MetricKey, string | null> & {
  initial_capital: string
  gross_return: string | null
  completed_trade_count: number
}
// Exact backend rows are retained; malformed detail data does not replace the summary.
export interface ExperimentResult {
  experiment: ExperimentMetadata
  summary: ExperimentSummary
  open_position: { direction: 'LONG' | 'SHORT'; quantity: string } | null
  unfilled_decision_count: number
  research: ResearchData | null
  research_error: string
}
