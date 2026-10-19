import type { StoredCandle } from '../history/api'
import { formatVolatility } from '../markets/format'
import { researchEvents, timeKey, type ResearchData } from './research'
import { utcLabel } from './TechnicalDetails'
export default function ResearchInspection({data,candles,selected,onSelect}:{data:ResearchData;candles:StoredCandle[]|null;selected:string;onSelect:(time:string)=>void}){
  const i=Math.max(0,data.decisions.findIndex(d=>timeKey(d.open_time)===timeKey(selected))),d=data.decisions[i],g=data.regimes[i],e=data.equity_curve[i]
  const c=candles?.find(row=>timeKey(row.open_time)===timeKey(d.open_time))
  const events=researchEvents(data).filter(event=>timeKey(event.candle_open_time)===timeKey(d.open_time))
  return <section className="lab-research-inspector" aria-labelledby="lab-inspect-title"><h3 id="lab-inspect-title">Candle &amp; decision inspection</h3>
    <label className="lab-field" htmlFor="lab-inspect-candle"><span>Inspect evaluation candle · UTC</span><select id="lab-inspect-candle" value={d.open_time} onChange={event=>onSelect(event.target.value)}>{data.decisions.map(row=><option key={row.open_time} value={row.open_time}>{utcLabel(row.open_time)} · {row.decision}</option>)}</select></label>
    <dl className="lab-inspection-grid"><div><dt>Decision known at close</dt><dd>{utcLabel(d.timestamp)}</dd></div><div><dt>Strategy decision</dt><dd>{d.decision}</dd></div><div><dt>Replay thesis state · not filled state</dt><dd>{d.position_before} → {d.position_after}</dd></div><div><dt>Accounting position at close</dt><dd>{e.position_state}</dd></div>
      <div><dt>Confirmed regime</dt><dd>{g.regime}</dd></div><div><dt>Candidate regime</dt><dd>{g.candidate_regime}</dd></div><div><dt>Confirmation count / required</dt><dd>{g.confirmation_count} / {g.confirmation_required}</dd></div><div><dt>Regime retention</dt><dd>{g.retained_previous ? 'Retained previous regime' : 'Not retained'} · Previous: {g.previous_regime ?? 'Unavailable'}</dd></div>
      <div><dt>Exact total equity</dt><dd>{e.total_equity}</dd></div><div><dt>Drawdown magnitude</dt><dd>{e.drawdown === null ? 'Unavailable' : formatVolatility(e.drawdown)}</dd></div></dl>
    <p className="lab-caption">Decision reasons: {d.reason_codes.join(' · ') || 'Unavailable'}</p><p className="lab-caption">Regime reasons: {g.reason_codes.join(' · ') || 'Unavailable'}</p>
    <div className="lab-event-list">{events.map(event=><div className="lab-note" key={event.id}><strong>{event.kind === 'fill' ? 'Simulated execution fill' : 'Strategy decision'} · {event.action} · {event.status}</strong><p>{utcLabel(event.timestamp)}{event.price !== null ? ` · Exact fill price: ${event.price}` : ''}</p></div>)}</div>
    <details className="lab-technical"><summary>Exact selected-candle values</summary><dl>{c ? (['open','high','low','close'] as const).map(key=><div key={key}><dt>{key}</dt><dd>{c[key]}</dd></div>) : <div><dt>OHLC</dt><dd>Unavailable · stored candle view not aligned</dd></div>}
      {(['cash','realized_capital','realized_pnl','unrealized_pnl','total_equity','drawdown'] as const).map(key=><div key={key}><dt>{key.replaceAll('_',' ')}</dt><dd>{e[key] ?? 'Unavailable'}</dd></div>)}<div><dt>Observation timestamp</dt><dd>{utcLabel(e.timestamp)}</dd></div></dl></details>
  </section>
}
