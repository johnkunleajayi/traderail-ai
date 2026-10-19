import { formatMarketValue } from '../markets/format'
import type { TradeRow } from './research'
import { utcLabel } from './TechnicalDetails'
const value=(raw:string)=><span title={raw}>{formatMarketValue(raw)}</span>
export default function TradeTable({trades,onInspect}:{trades:TradeRow[];onInspect:(decisionTime:string)=>void}){
  return <section className="lab-trades" aria-labelledby="lab-trades-title"><h3 id="lab-trades-title">Completed research trades</h3>
    {!trades.length ? <p className="lab-note">No completed trades in this evaluation period. Open positions and unfilled decisions are not counted as completed trades.</p>
      : <div className="lab-table-scroll" tabIndex={0} role="region" aria-label="Completed trades; scroll horizontally to inspect all columns"><table><caption>Backend simulated historical fills and exact-cost accounting. Values are in quote currency except base-asset quantity. Expand reasons for exact decimals.</caption>
        <thead><tr>{['Direction','Entry decision · UTC','Entry fill · UTC','Entry fill price','Exit decision · UTC','Exit fill · UTC','Exit fill price','Quantity','Gross P&L','Fees','Spread cost','Slippage cost','Net P&L','Inspection'].map(label=><th scope="col" key={label}>{label}</th>)}</tr></thead>
        <tbody>{trades.map(t=><tr key={t.trade_identity}><th scope="row">{t.direction}</th><td>{utcLabel(t.entry_decision_time)}</td><td>{utcLabel(t.entry_fill_time)}</td><td>{value(t.entry_fill_price)}</td><td>{utcLabel(t.exit_decision_time)}</td><td>{utcLabel(t.exit_fill_time)}</td><td>{value(t.exit_fill_price)}</td><td>{t.quantity}</td><td>{value(t.gross_pnl)}</td><td>{value(t.fees)}</td><td>{value(t.spread_cost)}</td><td>{value(t.slippage_cost)}</td><td>{value(t.net_pnl)}</td>
          <td><button className="lab-text-button" type="button" onClick={()=>onInspect(t.entry_decision_time)}>Inspect entry</button><button className="lab-text-button" type="button" onClick={()=>onInspect(t.exit_decision_time)}>Inspect exit</button>
            <details><summary>Reasons &amp; exact values</summary><p>Entry: {t.entry_reason_codes.join(' · ') || 'Unavailable'}</p><p>Exit: {t.exit_reason_codes.join(' · ') || 'Unavailable'}</p><dl>{Object.entries(t).filter(([key])=>!key.endsWith('reason_codes')).map(([key,raw])=><div key={key}><dt>{key.replaceAll('_',' ')}</dt><dd>{String(raw)}</dd></div>)}</dl></details></td>
        </tr>)}</tbody></table></div>}
  </section>
}
