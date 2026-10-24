import { createContext, useContext, useEffect, useState, type ReactNode, type RefObject } from 'react'
import { ArrowRight, Check, LockKeyhole, LockKeyholeOpen, ShieldCheck } from 'lucide-react'

// Authored architecture illustrations. These are not signals, approvals or financial state.
export const DEMO_SCENARIOS = [
  { id: 'long', label: 'Authorized LONG', regime: 'BULL_TREND', strategy: 'ENTER_LONG', risk: 'APPROVED', execution: 'AUTHORIZED', outcome: 'POSITION OPEN', explanation: 'Valid evidence and authorization let the proposal continue.' },
  { id: 'short', label: 'Unsupported SHORT', regime: 'BEAR_TREND', strategy: 'ENTER_SHORT', risk: 'NO_TRADE', execution: 'NOT REQUESTED', outcome: 'CASH', explanation: 'SHORT is outside this Paper capability. The system stays in cash.' },
  { id: 'stale', label: 'Unsafe evidence', regime: 'STALE / UNSAFE', strategy: 'NO_TRADE', risk: 'BLOCKED', execution: 'NOT REQUESTED', outcome: 'CAPITAL PRESERVED', explanation: 'Unsafe evidence stops the proposal. Sometimes the correct action is to stay out.' },
] as const
export type DemoFrame = { scenario: number; phase: number }
export const INITIAL_DEMO_FRAME: DemoFrame = { scenario: 0, phase: 0 }
export function nextDemoFrame(frame: DemoFrame): DemoFrame {
  return frame.phase < 10 ? { ...frame, phase: frame.phase + 1 } : { scenario: (frame.scenario + 1) % DEMO_SCENARIOS.length, phase: 0 }
}
export function demoView(frame: DemoFrame) {
  const scenario = DEMO_SCENARIOS[frame.scenario]
  const resolved = frame.phase >= 4
  const approved = scenario.id === 'long'
  const active = frame.phase < 4 ? frame.phase : approved && frame.phase >= 5 && frame.phase <= 7 ? frame.phase - 1 : -1
  const heartbeat = ['MARKET OBSERVED', 'REGIME DETECTED', 'STRATEGY PROPOSED', 'RISK EVALUATING'][frame.phase]
    ?? (resolved && !approved ? scenario.outcome : frame.phase === 4 ? 'RISK APPROVED' : frame.phase === 5 ? 'AUTHORIZED' : frame.phase === 6 ? 'POSITION OPEN' : frame.phase === 7 ? 'OUTCOME RECORDED' : scenario.outcome)
  return { scenario, resolved, approved, active, heartbeat,
    statuses: ['BTC/USDT · 1H', frame.phase >= 1 ? scenario.regime : 'Waiting for observation', frame.phase >= 2 ? scenario.strategy : 'Waiting for intelligence', frame.phase < 3 ? 'Waiting for proposal' : !resolved ? 'EVALUATING' : scenario.risk,
      resolved && !approved ? 'NOT REQUESTED' : frame.phase >= 5 ? 'AUTHORIZED' : 'Awaiting authorization',
      resolved && !approved ? 'CASH · NO EXPOSURE' : frame.phase >= 6 ? 'POSITION OPEN' : 'Awaiting execution',
      resolved && !approved ? 'NO EXPOSURE RECORDED' : frame.phase >= 7 ? 'OUTCOME RECORDED' : 'Awaiting outcome'] }
}
const DemoContext = createContext({ frame: INITIAL_DEMO_FRAME, select: (_index: number) => {} })
export function LandingDemoProvider({ children, paused, reduced, surfaceRef }: { children: ReactNode; paused: boolean; reduced: boolean; surfaceRef: RefObject<HTMLDivElement | null> }) {
  const [frame, setFrame] = useState<DemoFrame>(INITIAL_DEMO_FRAME)
  useEffect(() => {
    const root = surfaceRef.current
    if (!root || paused || reduced || !('IntersectionObserver' in window)) return
    let timer: ReturnType<typeof setInterval> | undefined
    const visible = new Set<Element>()
    const stop = () => { clearInterval(timer); timer = undefined }
    const run = () => { stop(); if (visible.size && !document.hidden) timer = setInterval(() => setFrame(nextDemoFrame), 1400) }
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => { if (entry.isIntersecting) visible.add(entry.target); else visible.delete(entry.target) })
      run()
    }, { threshold: 0.1 })
    root.querySelectorAll('[data-demo-surface]').forEach(element => observer.observe(element))
    document.addEventListener('visibilitychange', run)
    return () => { stop(); observer.disconnect(); document.removeEventListener('visibilitychange', run) }
  }, [paused, reduced, surfaceRef])
  // Reduced motion presents a complete scenario. Manual selection remains available.
  const displayed = reduced ? { ...frame, phase: 10 } : frame
  return <DemoContext.Provider value={{ frame: displayed, select: index => setFrame({ scenario: index, phase: paused || reduced ? 10 : 0 }) }}>{children}</DemoContext.Provider>
}
export function DemoHeartbeat() {
  const { frame } = useContext(DemoContext)
  return <span className="lp-demo-heartbeat"><i aria-hidden="true"/>{demoView(frame).heartbeat}<small>DEMO</small></span>
}
export function DemoHero() {
  const { frame } = useContext(DemoContext)
  const view = demoView(frame)
  const gate = !view.resolved ? 'waiting' : view.approved ? 'approved' : 'held'
  return <>
    <div className="lp-context-float lp-demo-context" data-demo-gate={gate}>
      <span className="lp-interface-label">ARCHITECTURE DEMO <span className="lp-pill">PAPER</span></span><strong>BTC / USDT <small>1H</small></strong>
      <MarketTrace/>
      <dl className="lp-demo-readings"><div><dt>Intelligence</dt><dd>{view.statuses[1]}</dd></div><div><dt>Strategy</dt><dd>{view.statuses[2]}</dd></div><div><dt>Risk / capability</dt><dd>{view.statuses[3]}</dd></div></dl>
      <DemoHeartbeat/>
    </div>
    <div className="lp-authorization-float lp-demo-outcome" data-demo-gate={gate}>
      <ShieldCheck size={25}/><div><span className="lp-interface-label">{view.resolved ? 'DEMO / CONTROLLED OUTCOME' : 'DEMO / RISK AUTHORIZATION REQUIRED'}</span><strong>{view.resolved && (!view.approved || frame.phase >= 6) ? view.scenario.outcome : frame.phase === 4 ? 'Risk approved' : frame.phase >= 5 ? 'Execution authorized' : 'Control before capital.'}</strong><span className="lp-system-status">{view.resolved ? view.approved ? 'Only the authorized path proceeds' : 'No order. No new exposure.' : 'Strategy proposes. Risk decides.'}</span></div><LockKeyhole size={17}/>
    </div>
  </>
}
const stages = [ ['Market','Completed market observations.'], ['Intelligence','Turn observations into context.'], ['Strategy','Propose a trade—or stay in cash.'], ['Risk','Check exposure before permission.'], ['Execution','Act only on valid authorization.'], ['Position','Track the position through its lifecycle.'], ['Performance','Review outcomes, costs and evidence.'] ]
export function DemoEngine() {
  const { frame, select } = useContext(DemoContext)
  const view = demoView(frame)
  const gate = !view.resolved ? 'waiting' : view.approved ? 'approved' : 'held'
  return <div className="lp-engine lp-operating-engine" data-engine data-demo-surface data-demo-gate={gate} data-scenario={view.scenario.id} aria-label="Illustrative signal flow. Risk may authorize a proposal or block it. No trade is a valid outcome.">
    <div className="lp-engine-heading"><span>ILLUSTRATIVE ENGINE · NOT LIVE TRADING</span><DemoHeartbeat/></div>
    <div className="lp-demo-select" role="group" aria-label="Choose an illustrative scenario">{DEMO_SCENARIOS.map((scenario,index)=><button key={scenario.id} type="button" aria-pressed={frame.scenario===index} onClick={()=>select(index)}>{scenario.label}</button>)}</div>
    <ol className="lp-pipeline">{stages.map(([title,copy],index)=>{
      const passed = index < (frame.phase >= 4 ? frame.phase - 1 : frame.phase) && (view.approved || index < 3)
      return <li key={title} data-node={index} data-active={view.active===index} data-passed={passed} className={index===3?'lp-gate':''}>
        <span className="lp-step">0{index+1}</span><span className="lp-node-orbit" aria-hidden="true">{index===3 ? gate==='approved'?<LockKeyholeOpen size={19}/>:<LockKeyhole size={19}/> : <span/>}</span><h3>{title}</h3><p>{copy}</p><span className="lp-node-status">{view.statuses[index]}</span>{index<6&&<ArrowRight className="lp-flow-arrow" size={18} aria-hidden="true"/>}
      </li>
    })}</ol>
    <div className="lp-demo-consequence" data-resolved={view.resolved}><span className="lp-demo-result-icon" aria-hidden="true">{gate==='approved'?<Check size={20}/>:<ShieldCheck size={20}/>}</span><div><strong>{!view.resolved ? 'Every proposal waits at Risk.' : view.approved && frame.phase<6 ? 'APPROVED → AUTHORIZED' : view.scenario.outcome}</strong><p>{!view.resolved ? 'Execution cannot receive a signal before authorization.' : view.scenario.explanation}</p></div><span className="lp-demo-branch">{view.resolved ? view.approved ? 'AUTHORIZED PATH' : 'NO_TRADE / CASH' : 'GATE PENDING'}</span></div>
    <details className="lp-demo-transcript"><summary>Inspect the three demo outcomes</summary><ul>{DEMO_SCENARIOS.map(scenario=><li key={scenario.id}>BTC/USDT · {scenario.regime} → {scenario.strategy} → {scenario.risk} → {scenario.execution} → {scenario.outcome}</li>)}</ul><p>Hard-coded illustrations only. No current market information, Risk evaluation or order activity.</p></details>
  </div>
}

export function MarketTrace() {
  return <svg className="lp-market-trace" viewBox="0 0 400 100" fill="none" aria-hidden="true">
    <path className="lp-trace-grid" d="M0 25H400M0 50H400M0 75H400M50 0V100M150 0V100M250 0V100M350 0V100"/>
    <path className="lp-trace-base" d="M0 68L22 61L44 73L66 52L88 57L110 39L132 50L154 32L176 45L198 41L220 62L242 57L264 30L286 40L308 24L330 34L352 21L374 29L400 16"/>
    <path className="lp-trace-signal" d="M0 68L22 61L44 73L66 52L88 57L110 39L132 50L154 32L176 45L198 41L220 62L242 57L264 30L286 40L308 24L330 34L352 21L374 29L400 16"/>
  </svg>
}
