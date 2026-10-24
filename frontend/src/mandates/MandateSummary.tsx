import { ShieldCheck } from 'lucide-react'
import { formatAllocation, riskLabel, styleLabel } from './model'
import type { MandateIntent } from './types'
export default function MandateSummary({ intent }: { intent: MandateIntent }) {
  return <div className="mandate-summary"><p className="mandate-overline">YOUR TRADING MANDATE</p><h3>Allocate <span>{formatAllocation(intent.capital_allocation)}</span></h3><dl>
    {[['Market', intent.instrument], ['Risk preference', riskLabel(intent.risk_level)], ['Trading style', styleLabel(intent.trading_style)], ['Capability', 'Long / Cash'], ['Environment', 'Paper']].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl><div className="mandate-boundary"><ShieldCheck size={18}/><p>Strategy proposes. Risk authorizes exposure. Execution only acts on authorized decisions. <strong>Activating this mandate does not place a trade.</strong></p></div></div>
}
