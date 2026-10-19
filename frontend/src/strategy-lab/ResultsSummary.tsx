import { formatMarketValue, formatVolatility } from '../markets/format'
import type { ExperimentResult, MetricKey } from './types'
const metrics: readonly [MetricKey, string, boolean?][] = [
  ['ending_equity', 'Ending equity'], ['total_return', 'Total return', true], ['net_return', 'Net return', true],
  ['max_drawdown', 'Maximum drawdown', true], ['win_rate', 'Win rate', true], ['profit_factor', 'Profit factor'],
  ['expectancy', 'Expectancy'], ['exposure', 'Exposure · %'], ['turnover', 'Turnover · ratio'],
  ['fee_total', 'Trading fees'], ['spread_cost_total', 'Spread cost'], ['slippage_cost_total', 'Slippage cost'],
]
export default function ResultsSummary({ result }: { result: ExperimentResult }) {
  const quote = result.experiment.canonical_symbol.split('/')[1]
  return <section className="lab-results" aria-labelledby="lab-result-title">
    <div className="lab-subheading"><div><p className="section-label">HISTORICAL RESEARCH RESULT</p><h2 id="lab-result-title">Experiment overview</h2></div><span className="lab-tag">Quote currency · {quote}</span></div>
    <p className="lab-caption">Historical results describe this dataset and these assumptions. They do not establish future performance or authorize deployment. Hover or focus a value to inspect its exact backend decimal.</p>
    <dl className="lab-metrics">{metrics.map(([key, label, percent]) => {
      const raw = result.summary[key]
      return <div className="lab-metric" key={key}><dt>{label}</dt><dd tabIndex={raw === null ? undefined : 0} title={raw ?? undefined}>{raw === null ? 'Unavailable' : key === 'exposure' ? formatMarketValue(raw) + '%' : percent ? formatVolatility(raw) : formatMarketValue(raw)}</dd></div>
    })}<div className="lab-metric"><dt>Completed trades</dt><dd>{result.summary.completed_trade_count}</dd></div></dl>
    {result.open_position && <p className="lab-note">An open {result.open_position.direction} research position remains at the dataset end. No exit was fabricated.</p>}
    {result.unfilled_decision_count > 0 && <p className="lab-note">{result.unfilled_decision_count} decision(s) remain unfilled at the dataset boundary.</p>}
  </section>
}
