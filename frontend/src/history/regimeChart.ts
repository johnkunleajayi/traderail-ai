import { HistogramSeries, type IChartApi, type UTCTimestamp } from 'lightweight-charts'
import type { Regime, RegimeRow } from './regimes'
export const regimeColorTokens: Record<Regime,string> = { BULL_TREND:'--brand-text', BEAR_TREND:'--headline-end', RANGE:'--muted', BREAKOUT:'--color-success', HIGH_VOLATILITY:'--color-warning', UNSAFE:'--color-risk', UNKNOWN:'--line' }
export function addRegimeStrip(chart: IChartApi, rows: RegimeRow[], token: (name: string) => string) {
  const index = chart.panes().length
  const strip = chart.addSeries(HistogramSeries, { priceLineVisible:false, lastValueVisible:false, priceScaleId:'regime-strip',
    priceFormat:{type:'custom',formatter:()=>''}, autoscaleInfoProvider:()=>({priceRange:{minValue:0,maxValue:1}}) }, index)
  strip.priceScale().applyOptions({visible:false,scaleMargins:{top:0,bottom:0}})
  // Constant categorical geometry only: bar height is not a financial value or score.
  const theme = () => strip.setData(rows.map(row=>({time:(Date.parse(row.open_time)/1000) as UTCTimestamp,value:1,color:token(regimeColorTokens[row.regime])})))
  chart.panes()[index].setStretchFactor(.35)
  theme()
  return theme
}
