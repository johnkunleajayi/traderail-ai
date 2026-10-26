import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Check, ShieldCheck } from 'lucide-react'
import { buildIntent, newForm, positiveDecimal, riskOptions, steps, styleOptions } from './model'
import MandateSummary from './MandateSummary'
import type { IntentForm, MandateIntent, TradingMandate } from './types'
interface Props { mandate: TradingMandate | null; busy: boolean; onSave: (intent: MandateIntent, id?: string) => Promise<TradingMandate | null>; onActivate: (mandate: TradingMandate) => void; onCancel: () => void }
export default function MandateFlow({ mandate, busy, onSave, onActivate, onCancel }: Props) {
  const [form, setForm] = useState<IntentForm>(mandate ? { capital: mandate.capital_allocation, risk: mandate.risk_level, style: mandate.trading_style } : { ...newForm })
  const [step, setStep] = useState(0), [saved, setSaved] = useState<TradingMandate | null>(null), [error, setError] = useState('')
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus() }, [step])
  const intent = positiveDecimal(form.capital.trim()) ? buildIntent(form) : null
  async function next() {
    setError('')
    if (step === 0 && !positiveDecimal(form.capital.trim())) { setError('Enter a positive capital allocation in USDT.'); return }
    if (step === 4) {
      const result = await onSave(buildIntent(form), mandate?.id)
      if (result) { setSaved(result); setStep(5) }
    } else setStep(current => current + 1)
  }
  return <div className="mandate-flow" aria-busy={busy}>
    <aside className="mandate-progress"><p className="mandate-overline">DEFINE YOUR INTENT</p><ol aria-label="Mandate setup progress">{steps.map((label,index)=><li key={label} aria-current={step===index?'step':undefined} data-complete={index<step}><span>{index<step?<Check size={14}/>:index+1}</span>{label}</li>)}</ol><div className="mandate-progress-note"><ShieldCheck size={21}/><p>Your preferences guide the mandate. Hard Risk controls stay in place.</p></div></aside>
    <div className="mandate-step"><p className="mandate-overline">STEP {step+1} OF 6 · PAPER</p>
      <h3 ref={heading} tabIndex={-1}>{['How much capital should TradeRail manage under this mandate?', 'Choose your market.', 'What approach to risk feels right?', 'Choose your trading style.', 'Your intent, clearly defined.', 'Ready to activate your mandate?'][step]}</h3>
      {error&&<p role="alert" className="mandate-error">{error}</p>}
      <form onSubmit={event=>{event.preventDefault();if(!busy&&step<5)void next()}}>
      <fieldset disabled={busy} className="mandate-step-fields"><legend className="sr-only">{steps[step]}</legend>
      {step===0&&<><p>This defines the capital allocation for the mandate. It does not transfer, reserve or move funds.</p><div className="mandate-capital"><label htmlFor="mandate-capital">Capital allocation</label><div><input id="mandate-capital" type="text" inputMode="decimal" autoComplete="off" value={form.capital} onChange={event=>{setForm({...form,capital:event.target.value});setError('')}} aria-describedby="mandate-capital-help" aria-invalid={!!error}/><span>USDT</span></div></div><p id="mandate-capital-help" className="mandate-note">Use a positive amount, such as 2,000 entered as 2000. Your actual Paper cash balance is unchanged.</p></>}
      {step===1&&<><p>A focused starting point. BTC/USDT is the supported market for this Paper mandate.</p><label className="mandate-market-choice"><input type="radio" name="market" value="BTC/USDT" checked onChange={()=>{}}/><span className="mandate-market-icon" aria-hidden="true">BTC</span><span><strong>Bitcoin / Tether</strong><small>BTC/USDT · Paper</small></span><Check size={18}/></label></>}
      {step===2&&<><p>These are user preferences. TradeRail’s hard Risk controls cannot be disabled, and this selection does not change current Risk settings.</p><div className="mandate-options" role="radiogroup" aria-label="Risk preference">{riskOptions.map(option=><label key={option.value}><input type="radio" name="risk" value={option.value} checked={form.risk===option.value} onChange={()=>setForm({...form,risk:option.value})}/><span><strong>{option.label}</strong><small>{option.copy}</small></span></label>)}</div></>}
      {step===3&&<><p>Choose your preferred horizon. TradeRail handles eligible technical configurations internally; your preference does not choose a raw timeframe or determine how long a position must stay open.</p><div className="mandate-options" role="radiogroup" aria-label="Trading style">{styleOptions.map(option=><label key={option.value}><input type="radio" name="style" value={option.value} checked={form.style===option.value} onChange={()=>setForm({...form,style:option.value})}/><span><strong>{option.label}</strong><small>{option.copy}</small></span></label>)}</div></>}
      {step===4&&intent&&<><MandateSummary intent={intent}/><p className="mandate-note">Save these preferences as {mandate?.status==='PAUSED'?'a paused mandate':'a draft'}, then choose whether to activate. Eligibility, strategy decisions and Risk authorization remain separate controls. Saving or activating does not place a trade.</p></>}
      {step===5&&saved&&<><MandateSummary intent={saved}/><p className="mandate-confirmed" role="status"><Check size={16}/> Preferences saved. Status: {saved.status==='PAUSED'?'Paused':'Draft'}.</p><p>Activating means TradeRail may use these preferences as your active Paper mandate. It does not start trading.</p></>}
      </fieldset>
      <div className="mandate-flow-actions">{step<5?<><button type="button" className="mandate-back" disabled={busy} onClick={()=>step===0?onCancel():setStep(current=>current-1)}><ArrowLeft size={15}/>{step===0?'Cancel':'Back'}</button><button className="button" type="submit" disabled={busy}>{busy?'Saving preferences…':step===4?'Save & continue':'Continue'}<ArrowRight size={15}/></button></>:<><button type="button" className="mandate-back" disabled={busy} onClick={onCancel}>Keep {saved?.status==='PAUSED'?'paused':'as draft'}</button><button type="button" className="button" disabled={busy||!saved} onClick={()=>saved&&onActivate(saved)}>{busy?'Activating…':'Activate mandate'}<ArrowRight size={15}/></button></>}</div>
      </form>
      <div className="mandate-capability"><ShieldCheck size={16}/><p><strong>Current trading capability · Long / Cash</strong><span>TradeRail can enter long positions or remain in cash when conditions are unsuitable. A mandate alone cannot authorize either action.</span></p></div>
    </div>
  </div>
}
