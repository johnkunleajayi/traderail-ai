import { useEffect, useRef, useState } from 'react'
import { ArrowRight, FileText, FlaskConical, Plus } from 'lucide-react'
import { changeMandate, createMandate, listMandates, MandateApiError, readMandate, updateMandate } from './api'
import { formatAllocation, riskLabel, statusLabels, styleLabel } from './model'
import MandateFlow from './MandateFlow'
import MandateSummary from './MandateSummary'
import type { MandateAction, MandateIntent, TradingMandate } from './types'
import './mandates.css'
export default function MandateWorkspace({ onSessionExpired }: { onSessionExpired: () => void }) {
  const [rows, setRows] = useState<TradingMandate[]>([]), [selected, setSelected] = useState<TradingMandate | null>(null)
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [editing, setEditing] = useState(false)
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [attempt, setAttempt] = useState(0), [retiring, setRetiring] = useState(false)
  const request = useRef<AbortController | null>(null), mounted = useRef(true), result = useRef<HTMLDivElement>(null)
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;request.current?.abort()}},[])
  function fail(reason: unknown) { if (reason instanceof MandateApiError && reason.status===401) { onSessionExpired();return } setError(reason instanceof Error?reason.message:'The mandate change could not be confirmed.') }
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError('')
    listMandates(controller.signal).then(items=>{if(!controller.signal.aborted){setRows(items);setSelected(current=>items.find(item=>item.id===current?.id)??null)}})
      .catch(reason=>{if(!controller.signal.aborted)fail(reason)}).finally(()=>{if(!controller.signal.aborted)setLoading(false)})
    return()=>controller.abort()
  },[attempt,onSessionExpired])
  useEffect(()=>{if(!editing&&selected)result.current?.focus()},[editing,selected])
  function accept(row:TradingMandate){setSelected(row);setRows(current=>[row,...current.filter(item=>item.id!==row.id)]);setRetiring(false)}
  async function perform<T>(work:(signal:AbortSignal)=>Promise<T>):Promise<T|null>{
    if(request.current)return null
    const controller=new AbortController();request.current=controller;setBusy(true);setError('');setNotice('')
    try{const value=await work(controller.signal);return mounted.current&&!controller.signal.aborted?value:null}
    catch(reason){if(mounted.current&&!controller.signal.aborted)fail(reason);return null}
    finally{if(mounted.current){request.current=null;setBusy(false)}}
  }
  async function save(intent:MandateIntent,id?:string){
    const row=await perform(signal=>id?updateMandate(id,intent,signal):createMandate(intent,signal))
    if(row){accept(row);setNotice('Mandate preferences saved. No funds moved and no trade was placed.')}
    return row
  }
  async function lifecycle(row:TradingMandate,action:MandateAction){
    const updated=await perform(signal=>changeMandate(row.id,action,signal))
    if(updated){accept(updated);setEditing(false);setNotice(`Mandate ${statusLabels[updated.status].toLowerCase()}. No trade was placed.`)}
  }
  async function inspect(row:TradingMandate){
    const current=await perform(signal=>readMandate(row.id,signal))
    if(current){accept(current);setEditing(false)}
  }
  function reload(){setEditing(false);setRetiring(false);setNotice('');setAttempt(current=>current+1)}
  return <section className="mandate-workspace" aria-labelledby="mandate-title" aria-busy={loading||busy}>
    <header className="mandate-heading"><div><p className="mandate-overline">TRADING MANDATE</p><h2 id="mandate-title">Your intent. Clear boundaries.</h2><p>Define the capital, market and preferences. TradeRail keeps the technical controls.</p></div><span className="mandate-paper"><FlaskConical size={15}/> Paper environment</span></header>
    <p className="mandate-environment-note">Test the mandate using simulated capital before any future Live eligibility. Saving or activating a mandate never places a trade.</p>
    {error&&<div role="alert" className="mandate-error"><strong>Mandate action not confirmed</strong><p>{error}</p><button className="button button-secondary" disabled={busy} onClick={reload}>Reload saved mandates</button></div>}
    {notice&&<p className="mandate-notice" role="status">{notice}</p>}
    {loading?<p role="status" className="mandate-loading">Loading your saved mandates…</p>:error&&!rows.length&&!editing?null:editing?<MandateFlow mandate={selected} busy={busy} onSave={save} onActivate={row=>void lifecycle(row,'activate')} onCancel={()=>{setEditing(false);setError('')}}/>:!rows.length?<div className="mandate-empty"><FileText size={30}/><p className="mandate-overline">A PROCESS STARTS WITH INTENT</p><h3>Define your first Paper mandate.</h3><p>A guided way to set your capital allocation, market, risk preference and trading style. No technical parameters. No trading permission.</p><button className="button" onClick={()=>{setSelected(null);setEditing(true)}}>Create Paper mandate <ArrowRight size={16}/></button></div>:<>
      <div className="mandate-list-heading"><h3>Your mandates</h3><button className="button button-secondary" disabled={busy} onClick={()=>{setSelected(null);setEditing(true);setNotice('');setError('')}}><Plus size={15}/> New mandate</button></div>
      <div className="mandate-saved-list">{rows.map(row=><button key={row.id} className="mandate-saved-row" aria-pressed={selected?.id===row.id} disabled={busy} onClick={()=>void inspect(row)}><span><strong>{row.instrument}</strong><small>{riskLabel(row.risk_level)} · {styleLabel(row.trading_style)}</small></span><span className="mandate-saved-allocation">{formatAllocation(row.capital_allocation)}</span><span className={`mandate-status status-${row.status.toLowerCase()}`}>{statusLabels[row.status]}</span><ArrowRight size={15}/></button>)}</div>
      <div className="mandate-detail" ref={result} tabIndex={-1}>{selected?<>
        <div className="mandate-detail-heading"><h3>Paper mandate</h3><span className={`mandate-status status-${selected.status.toLowerCase()}`}>{statusLabels[selected.status]}</span></div><MandateSummary intent={selected}/>
        <p className="mandate-state-copy">{selected.status==='ACTIVE'?'These are your active preferences. They do not grant permission to trade or change Risk limits.':selected.status==='RETIRED'?'This mandate is retired and read-only. Create a new mandate to define different intent.':selected.status==='PAUSED'?'Your mandate is paused. You can edit its intent or reactivate it.':'Your preferences are saved as a draft. Review them before activation.'}</p>
        <div className="mandate-detail-actions">{(selected.status==='DRAFT'||selected.status==='PAUSED')&&<><button className="button button-secondary" disabled={busy} onClick={()=>{setEditing(true);setNotice('')}}>Continue editing</button><button className="button" disabled={busy} onClick={()=>void lifecycle(selected,'activate')}>{busy?'Updating…':selected.status==='PAUSED'?'Reactivate mandate':'Activate mandate'}</button></>}{selected.status==='ACTIVE'&&<button className="button button-secondary" disabled={busy} onClick={()=>void lifecycle(selected,'pause')}>{busy?'Updating…':'Pause mandate'}</button>}{selected.status!=='RETIRED'&&<button className="mandate-back" disabled={busy} onClick={()=>setRetiring(true)}>Retire mandate</button>}</div>
        {retiring&&<div className="mandate-retire-confirm"><p>Retirement is permanent for this mandate. It does not close positions or move funds.</p><button className="button button-secondary" disabled={busy} onClick={()=>void lifecycle(selected,'retire')}>Confirm retirement</button><button className="mandate-back" disabled={busy} onClick={()=>setRetiring(false)}>Keep mandate</button></div>}
      </>:<p className="mandate-note">Select a mandate to inspect its saved intent and available actions.</p>}</div>
    </>}
  </section>
}
