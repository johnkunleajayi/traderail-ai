import { formatIndicator, formatVolatility } from '../markets/format'
import type { RegimeDataset, RegimeRow } from '../history/regimes'
function RowState({row}: {row:RegimeRow}) {
  return <><span>Candidate: <strong>{row.candidate_regime}</strong></span><span>Confirmation: {row.confirmation.required === 0 ? 'Unavailable' : `${row.confirmation.count} / ${row.confirmation.required}`}</span>{row.confirmation.retained_previous && <span>Retained previous regime: {row.confirmation.previous_regime}</span>}</>
}
export default function RegimeSummary({data,error,loading=false}: {data:RegimeDataset|null;error:string;loading?:boolean}) {
  if (error) return <div className="history-error" role="status">{error}</div>
  if (!data) return <p className="history-caption" role="status">{loading ? 'Loading market regimes…' : 'Market regimes unavailable.'}</p>
  const row=data.latest_regime
  return <section className={`regime-summary regime-summary-${row.regime}`} aria-label="Latest market regime"><div className="regime-heading"><span>Market Regime</span><span>Engine v{data.regime_engine_version}</span></div><strong className={`regime-label regime-${row.regime}`}>{row.regime}</strong><div className="regime-state"><RowState row={row}/></div><p className="regime-reasons">{row.reason_codes.join(' · ')}</p><p className="history-caption">Describes stored market conditions only. Colors identify categories, not profitability.</p></section>
}
const evidenceLabels = { normalized_ema_spread:'EMA spread / close',ema20_slope:'EMA20 slope',ema50_slope:'EMA50 slope',rsi_14:'RSI14',macd_histogram:'MACD histogram',normalized_atr:'ATR / close',realized_volatility:'Realized volatility · unannualized',volatility_ratio:'Relative volatility',atr_ratio:'Relative ATR',reference_low:'Prior closing-range low',reference_high:'Prior closing-range high' } as const
export function RegimeInspection({row}: {row:RegimeRow}) {
  return <section className="regime-inspection" aria-label="Selected candle regime"><div className="regime-heading"><span>Selected candle regime</span><strong className={`regime-label regime-${row.regime}`}>{row.regime}</strong></div><div className="regime-state"><RowState row={row}/></div><p className="regime-reasons">{row.reason_codes.join(' · ')}</p><details className="intelligence-inspector"><summary>Regime evidence · backend measurements</summary><dl>{(Object.keys(evidenceLabels) as (keyof typeof evidenceLabels)[]).map(key=>{const raw=row.evidence[key];return <div key={key}><dt>{evidenceLabels[key]}</dt><dd>{raw===null?'Unavailable':<>{key==='realized_volatility'?formatVolatility(raw):formatIndicator(raw,key==='rsi_14'?'rsi':'market')}<br/><span className="history-caption">Exact: {raw}</span></>}</dd></div>})}</dl></details></section>
}
