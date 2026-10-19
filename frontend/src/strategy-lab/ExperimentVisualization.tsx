import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import type { StoredCandle } from '../history/api'
import { Card } from '../components/ui'
import { readExperimentCandles } from './candles'
import type { ExperimentResult } from './types'
import { timeKey } from './research'
import ResearchInspection from './ResearchInspection'
import TradeTable from './TradeTable'
import { utcLabel } from './TechnicalDetails'
const ResearchCharts=lazy(()=>import('./ResearchCharts'))
export default function ExperimentVisualization({result}:{result:ExperimentResult}){
  const data=result.research
  const [candles,setCandles]=useState<StoredCandle[]|null>(null),[loading,setLoading]=useState(false),[error,setError]=useState('')
  const [attempt,setAttempt]=useState(0),[selected,setSelected]=useState(data?.decisions.at(-1)?.open_time ?? '')
  const select=useCallback((time:string)=>setSelected(time),[])
  useEffect(()=>{
    if(!data)return
    const controller=new AbortController();setCandles(null);setLoading(true);setError('')
    readExperimentCandles(result.experiment,data,controller.signal).then(rows=>{if(!controller.signal.aborted)setCandles(rows.filter(c=>timeKey(c.open_time)>=timeKey(result.experiment.evaluation_start)))})
      .catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error ? reason.message : 'Experiment candles unavailable.')})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false)})
    return()=>controller.abort()
  },[result,data,attempt])
  if(!data)return <Card className="lab-visualization"><p className="lab-error" role="status">{result.research_error}</p></Card>
  const p=data.open_position
  return <Card className="lab-visualization"><div className="lab-subheading"><h2>Inspect the experiment</h2><span className="lab-tag">Stored history · {result.experiment.provider}</span></div>
    <p className="lab-caption">{result.experiment.warmup_candle_count} warm-up candles initialized analytics and are excluded from these evaluation charts. No warm-up returns are shown.</p>
    {p && <section className="lab-note" aria-label="Open research position"><strong>Open {p.direction} research position · marked to market, not closed</strong><p>Entry decision: {utcLabel(p.entry_decision_time)} · Fill: {utcLabel(p.entry_fill_time)}</p><p>Exact entry: {p.entry_price} · Quantity: {p.quantity}</p><p>Mark: {p.mark_price} at {utcLabel(p.mark_timestamp)} · Unrealized P&amp;L: {p.unrealized_pnl}</p></section>}
    {loading && <p role="status" className="lab-caption">Reading matching stored candles. No ingestion is performed.</p>}
    {error && <div className="lab-error" role="status"><p>{error}</p><button type="button" className="lab-text-button" onClick={()=>setAttempt(n=>n+1)}>Retry stored candle view</button></div>}
    <Suspense fallback={<p role="status">Loading research charts…</p>}><ResearchCharts candles={candles} data={data} initialCapital={result.experiment.initial_capital} selected={selected} onSelect={select}/></Suspense>
    <ResearchInspection data={data} candles={candles} selected={selected} onSelect={select}/>
    <TradeTable trades={data.trades} onInspect={time=>{
      const d=data.decisions.find(row=>timeKey(row.timestamp)===timeKey(time))
      if(d){setSelected(d.open_time);document.getElementById('lab-inspect-candle')?.focus()}
    }}/>
  </Card>
}
