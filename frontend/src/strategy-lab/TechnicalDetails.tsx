import type { ExperimentResult } from './types'
export function utcLabel(value: string) { return value.replace('T', ' ').replace(/(?:Z|\+00:00)$/, ' UTC') }
export default function TechnicalDetails({ result }: { result: ExperimentResult }) {
  const e = result.experiment
  const details = [ ['Dataset identity', e.dataset_identity], ['Experiment identity', e.experiment_identity],
    ['Research result identity', e.research_result_identity], ['Strategy configuration identity', e.strategy_configuration_identity],
    ['Stored candles supplied', String(e.candle_count)], ['Warm-up candles excluded', String(e.warmup_candle_count)],
    ['Warm-up start', utcLabel(e.warmup_start)], ['Initial capital', e.initial_capital], ['Fixed base quantity', e.quantity],
    ['Trading fee rate', e.execution.fee_rate], ['Full spread rate', e.execution.full_spread_rate], ['Slippage rate', e.execution.slippage_rate],
    ['Additional whole-candle delay', String(e.execution.delay_candles)], ['Execution convention', e.execution.timing],
    ['Cost model identity', e.execution.cost_model_identity], ['Execution configuration identity', e.execution.execution_configuration_identity] ]
  return <details className="lab-technical"><summary>Technical details &amp; exact assumptions</summary><dl>{details.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl><h3>Exact backend summary values</h3><dl>{Object.entries(result.summary).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{value === null ? 'Unavailable' : String(value)}</dd></div>)}</dl></details>
}
