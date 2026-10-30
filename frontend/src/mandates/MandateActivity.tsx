import { useEffect, useState } from 'react'
import { MandateApiError } from './api'
import { readActivity, type PaperActivity, type ActivityCategory } from './activity'
import { formatFinancial } from './performance'
import type { TradingMandate } from './types'
const labels: Record<ActivityCategory,string> = { PENDING:'Evaluation pending', NO_TRADE:'No trade', ENTRY_EXECUTED:'Paper entry executed', POSITION_HELD:'Holding position', POSITION_CLOSED:'Position closed', RISK_REJECTED:'Risk rejected', SKIPPED:'Evaluation skipped', FAILURE:'Reconciliation required', COMPLETED:'Evaluation completed' }
const utc = (value: string) => new Date(value).toLocaleString(undefined,{ timeZone:'UTC',timeZoneName:'short' })
export function ActivityView({ report }: { report: PaperActivity }) {
  const e = report.latest_evaluation
  return <section className="ws-activity" aria-label="Paper trading activity">
    <div className="ws-section-title"><div><p className="ws-eyebrow">Paper trading activity</p><h3>Latest recorded evaluation</h3></div><span className="ws-subtle">Persisted evidence</span></div>
    <div className="ws-activity-grid"><div className={`ws-evaluation ${e?.category === 'FAILURE' ? 'is-failure' : ''}`}>
      <h4>{e ? labels[e.category] : 'No runner evaluation recorded'}</h4>
      {e ? <><p className="ws-subtle">Outcome: {e.outcome_code ?? 'Not recorded yet'}</p><p>Recorded reason: {e.reason_code ?? 'No reason recorded yet'}</p>
        <p><time dateTime={e.recorded_at}>{utc(e.recorded_at)}</time> <span className="ws-subtle">· Last persisted update</span></p>
        {e.category === 'PENDING' && <p className="ws-inline-note">Pending is a stored work state, not proof that a worker is currently running.{e.retry_at && <> Retry eligible from <time dateTime={e.retry_at}>{utc(e.retry_at)}</time>.</>}</p>}
        {e.category === 'NO_TRADE' && <p className="ws-subtle">This evaluation recorded no trade. No execution is implied.</p>}
        {e.category === 'FAILURE' && <p className="ws-inline-note">Automatic processing requires reconciliation. Refreshing this report does not retry execution.</p>}
      </> : <p className="ws-subtle">There is no persisted autonomous evaluation for this mandate. Manual decisions without runner work records are not shown here.</p>}
    </div><div className="ws-activity-context"><dl><div><dt>Portfolio cash · USDT</dt><dd title={report.portfolio_cash ?? 'Not available'}>{formatFinancial(report.portfolio_cash)}</dd></div><div><dt>Runner health</dt><dd>Not verified</dd></div></dl><p className="ws-subtle">Portfolio cash is shared portfolio context, not a mandate wallet. Runner heartbeats are not persisted; an evaluation result does not establish that the runner is online.</p></div></div>
    <p className="ws-subtle">Activity snapshot <time dateTime={report.observed_at}>{utc(report.observed_at)}</time>. Refresh to update.</p>
  </section>
}
export default function MandateActivity({ mandate, refresh, onSessionExpired }: { mandate:TradingMandate; refresh:number; onSessionExpired:()=>void }) {
  const [report,setReport] = useState<PaperActivity|null>(null), [error,setError] = useState(''), [loading,setLoading] = useState(true), [retry,setRetry] = useState(0)
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setReport(null);setError('')
    readActivity(mandate.id,controller.signal).then(value=>{if(!controller.signal.aborted)setReport(value)})
      .catch(reason=>{if(controller.signal.aborted)return;if(reason instanceof MandateApiError && reason.status===401){onSessionExpired();return}setError(reason instanceof Error?reason.message:'Paper activity is unavailable.')})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false)})
    return()=>controller.abort()
  },[mandate.id,mandate.updated_at,mandate.status,refresh,retry,onSessionExpired])
  if (loading) return <section className="ws-activity" role="status" aria-label="Loading Paper activity"><div className="ws-skeleton ws-skeleton-title"/><p>Loading persisted Paper activity…</p></section>
  if (error || report?.mandate_identity !== mandate.id) return <section className="ws-activity" role="alert"><h3>Paper activity unavailable</h3><p>{error || 'The activity snapshot does not match this mandate.'}</p><button className="button button-secondary" onClick={()=>setRetry(v=>v+1)}>Retry activity</button></section>
  return <ActivityView report={report}/>
}
