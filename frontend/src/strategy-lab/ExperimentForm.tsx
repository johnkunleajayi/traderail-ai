import { historicalSymbols, timeframes } from '../history/api'
import type { ExperimentFormValues } from './model'

export default function ExperimentForm({ values, onChange, onRun, busy, suggesting, historyMessage, onSuggest }: {
  values: ExperimentFormValues; onChange: (next: ExperimentFormValues) => void; onRun: () => void
  busy: boolean; suggesting: boolean; historyMessage: string; onSuggest: () => void
}) {
  function field(key: keyof ExperimentFormValues, label: string, options: { integer?: boolean; date?: boolean } = {}) {
    return <label className="lab-field" htmlFor={`lab-${key}`} key={key}><span>{label}</span><input id={`lab-${key}`}
      type={options.date ? 'datetime-local' : 'text'} step={options.date ? '1' : undefined}
      inputMode={options.date ? undefined : options.integer ? 'numeric' : 'decimal'} required value={values[key]}
      onChange={event => onChange({ ...values, [key]: event.target.value })}/></label>
  }
  return <form className="lab-form" onSubmit={event => { event.preventDefault(); onRun() }} aria-busy={busy}>
    <fieldset disabled={busy}><legend>Experiment setup</legend>
      <p className="lab-caption">Adaptive Trend <span className="lab-tag">v1.0.0 · Fixed configuration</span></p>
      <div className="lab-fields">
        <label className="lab-field" htmlFor="lab-instrument"><span>Canonical instrument</span><select id="lab-instrument" value={values.canonical_symbol} onChange={e => onChange({ ...values, canonical_symbol: e.target.value })}>{historicalSymbols.map(s => <option key={s}>{s}</option>)}</select></label>
        <label className="lab-field" htmlFor="lab-provider"><span>Historical provider</span><select id="lab-provider" value={values.provider} onChange={e => onChange({ ...values, provider: e.target.value })}><option>BINANCE</option></select></label>
        <label className="lab-field" htmlFor="lab-timeframe"><span>Timeframe</span><select id="lab-timeframe" value={values.timeframe} onChange={e => onChange({ ...values, timeframe: e.target.value as ExperimentFormValues['timeframe'] })}>{timeframes.map(t => <option key={t}>{t}</option>)}</select></label>
        {field('requested_candle_count', 'Historical candles · 53–300', { integer: true })}
      </div>
      <div className="lab-subheading"><h3>Evaluation window</h3><button type="button" className="lab-text-button" onClick={onSuggest} disabled={suggesting}>{suggesting ? 'Reading stored history…' : 'Use stored history boundaries'}</button></div>
      <p className="lab-caption" role="status">{historyMessage || 'Choose UTC boundaries matching stored candles. At least 52 requested candles initialize analytics and are excluded from evaluation.'}</p>
      <div className="lab-fields">{field('evaluation_start', 'Evaluation start · UTC', { date: true })}{field('evaluation_end', 'Evaluation end · UTC, exclusive', { date: true })}
        {field('initial_capital', 'Initial research capital · quote currency')}{field('quantity', 'Fixed quantity · base asset')}</div>
      <h3>Execution assumptions</h3><p className="lab-caption">Rates are decimal fractions: 0.001 means 0.1%. Defaults are zero-cost research assumptions, not venue fee estimates. Decisions fill no earlier than the next candle open.</p>
      <div className="lab-fields">{field('fee_rate', 'Trading fee rate')}{field('full_spread_rate', 'Full spread rate')}{field('slippage_rate', 'Slippage rate')}{field('delay_candles', 'Additional delay · whole candles', { integer: true })}</div>
      <div className="lab-run"><button className="button" type="submit" disabled={busy || suggesting}>{busy ? 'Running historical experiment…' : 'Run experiment'}</button><span className="lab-caption">Research only. No portfolio or order changes.</span></div>
    </fieldset>
  </form>
}
