import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { FlaskConical } from 'lucide-react'
import { readStoredCandles } from '../history/api'
import { Card, SectionLabel } from '../components/ui'
import { ExperimentApiError, runExperiment } from './api'
import { buildRequest, defaultForm, storedBoundaries } from './model'
import type { ExperimentResult } from './types'
import ExperimentForm from './ExperimentForm'
import ResultsSummary from './ResultsSummary'
import ExperimentVisualization from './ExperimentVisualization'
import TechnicalDetails, { utcLabel } from './TechnicalDetails'

function ExperimentWorkspace({ onSessionExpired }: { onSessionExpired: () => void }) {
  const [values, setValues] = useState({ ...defaultForm })
  const [result, setResult] = useState<ExperimentResult | null>(null)
  const [busy, setBusy] = useState(false), [suggesting, setSuggesting] = useState(false)
  const [historyMessage, setHistoryMessage] = useState(''), [error, setError] = useState('')
  const [sessionExpired, setSessionExpired] = useState(false)
  const request = useRef<AbortController | null>(null), suggestions = useRef<AbortController | null>(null)
  const resultHeading = useRef<HTMLDivElement>(null)
  const suggest = useCallback(() => {
    suggestions.current?.abort()
    const controller = new AbortController(); suggestions.current = controller
    setSuggesting(true); setHistoryMessage('Reading stored history. No ingestion is performed.')
    readStoredCandles(values.canonical_symbol, values.timeframe, controller.signal).then(candles => {
      if (controller.signal.aborted) return
      const bounds = storedBoundaries(candles, values.timeframe)
      setValues(current => ({ ...current, ...bounds }))
      setHistoryMessage(`Suggested from ${candles.length} stored candles: 52 warm-up candles excluded. Review the UTC window before running.`)
    }).catch(reason => { if (!controller.signal.aborted) setHistoryMessage(reason instanceof Error ? reason.message : 'Stored boundaries are unavailable. Enter a known stored window manually.') })
      .finally(() => { if (!controller.signal.aborted) setSuggesting(false) })
    return controller
  }, [values.canonical_symbol, values.timeframe])
  useEffect(() => {
    setValues(current => ({ ...current, evaluation_start: '', evaluation_end: '' }))
    const controller = suggest()
    return () => controller.abort()
  }, [suggest])
  useEffect(() => () => { request.current?.abort(); suggestions.current?.abort() }, [])
  async function run() {
    if (request.current || suggesting) return
    let payload
    try { payload = buildRequest(values) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Review the experiment inputs.'); return }
    const controller = new AbortController(); request.current = controller
    setBusy(true); setError(''); setSessionExpired(false); setResult(null)
    try {
      const completed = await runExperiment(payload, controller.signal)
      if (!controller.signal.aborted) { setResult(completed); requestAnimationFrame(() => resultHeading.current?.focus()) }
    } catch (reason) {
      if (!controller.signal.aborted) { setError(reason instanceof Error ? reason.message : 'Experiment unavailable.'); setSessionExpired(reason instanceof ExperimentApiError && reason.status === 401) }
    } finally { if (!controller.signal.aborted) { setBusy(false); request.current = null } }
  }
  return <section className="strategy-lab" aria-labelledby="lab-title">
    <div className="lab-intro"><div><SectionLabel>STRATEGY LAB</SectionLabel><h2 id="lab-title">Evidence before exposure.</h2><p>Research and validate systematic strategies before capital is exposed.</p></div><span className="lab-tag"><FlaskConical size={14}/> Historical research</span></div>
    <div className="lab-layout"><Card className="lab-setup"><ExperimentForm values={values} onChange={setValues} onRun={run} busy={busy} suggesting={suggesting} historyMessage={historyMessage} onSuggest={suggest}/></Card>
      <div className="lab-output" ref={resultHeading} tabIndex={-1}>
        {error && <div className="lab-error" role="alert"><h3>Experiment not completed</h3><p>{error}</p>{sessionExpired && <button className="button button-secondary" onClick={onSessionExpired}>Return to sign in</button>}</div>}
        {busy ? <Card className="lab-empty"><FlaskConical size={28}/><h3>Replaying stored history</h3><p role="status">Running the deterministic research pipeline. No new market data is being fetched.</p></Card>
          : result ? <Card className="lab-result-card"><div className="lab-context"><strong>Adaptive Trend v{result.experiment.strategy_version}</strong><span>{result.experiment.canonical_symbol} · {result.experiment.provider} · {result.experiment.timeframe}</span><p>{utcLabel(result.experiment.evaluation_start)} → {utcLabel(result.experiment.evaluation_end)} (exclusive)</p></div><ResultsSummary result={result}/><TechnicalDetails result={result}/></Card>
          : !error && <Card className="lab-empty"><FlaskConical size={30}/><h3>Your research starts with a question.</h3><p>Use historical stored market data to test strategy behaviour before later controlled deployment stages. Choose an explicit evaluation window, review your assumptions, then run an experiment.</p><span className="lab-tag">No experiment run yet</span></Card>}
      </div>
    </div>
    {result && <ExperimentVisualization key={result.experiment.experiment_identity} result={result}/>}
  </section>
}


const ValidationWorkspace = lazy(() => import('./validation/ValidationWorkspace'))
export default function StrategyLab({ onSessionExpired }: { onSessionExpired: () => void }) {
  const [workflow, setWorkflow] = useState<'experiment' | 'validation'>('experiment')
  const [validationOpened, setValidationOpened] = useState(false)
  return <div className="lab-workflows">
    <nav className="workspace-nav" aria-label="Strategy Lab workflows">
      <button type="button" aria-current={workflow === 'experiment' ? 'page' : undefined} onClick={() => setWorkflow('experiment')}>Experiment</button>
      <button type="button" aria-current={workflow === 'validation' ? 'page' : undefined} onClick={() => { setValidationOpened(true); setWorkflow('validation') }}>Formal validation</button>
    </nav>
    <div hidden={workflow !== 'experiment'}><ExperimentWorkspace onSessionExpired={onSessionExpired}/></div>
    {validationOpened && <div hidden={workflow !== 'validation'}><Suspense fallback={<p role="status">Loading validation workspace…</p>}><ValidationWorkspace onSessionExpired={onSessionExpired}/></Suspense></div>}
  </div>
}
