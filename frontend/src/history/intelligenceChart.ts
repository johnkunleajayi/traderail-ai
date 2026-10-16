import { HistogramSeries, LineSeries, LineStyle, type IChartApi, type UTCTimestamp } from 'lightweight-charts'
import type { FeatureRow, Measurement } from './intelligence'

export const indicatorControls = [{ key: 'ema20', label: 'EMA20' }, { key: 'ema50', label: 'EMA50' }, { key: 'rsi', label: 'RSI14' }, { key: 'macd', label: 'MACD' }] as const
export type Indicators = Record<typeof indicatorControls[number]['key'], boolean>
export const defaultIndicators: Indicators = { ema20: true, ema50: true, rsi: true, macd: false }
export const rsiReferences = [30, 70] as const

// This is the canvas presentation boundary only. No indicators are computed here.
export function measurementPoints(rows: FeatureRow[], field: Measurement) {
  return rows.map(row => {
    const time = (Date.parse(row.open_time) / 1000) as UTCTimestamp
    if (row[field] === null) return { time } // Whitespace preserves unavailable warm-up intervals.
    const value = Number(row[field])
    if (!Number.isFinite(value) || value === 0 && /[1-9]/.test(row[field]!)) throw new Error('A backend measurement cannot be rendered. No replacement values are shown.')
    return { time, value }
  })
}

export function addIntelligence(chart: IChartApi, rows: FeatureRow[], visible: Indicators, token: (name: string) => string) {
  const referenceThemes: (() => void)[] = []
  const themed: { applyOptions: (options: { color: string }) => void; token: string }[] = []
  const addLine = (field: Measurement, title: string, color: string, pane: number) => {
    const series = chart.addSeries(LineSeries, { title, lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
      color: token(color), priceFormat: { type: 'price', precision: 4, minMove: .0001 } }, pane)
    series.setData(measurementPoints(rows, field))
    themed.push({ applyOptions: options => series.applyOptions(options), token: color })
    return series
  }
  if (visible.ema20) addLine('ema_20', 'EMA20', '--brand-text', 0)
  if (visible.ema50) addLine('ema_50', 'EMA50', '--intelligence-secondary', 0)
  let pane = 1
  if (visible.rsi) {
    const rsi = addLine('rsi_14', 'RSI14', '--brand-text', pane++)
    rsi.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) })
    for (const price of rsiReferences) {
      const reference = rsi.createPriceLine({ price, title: `${price} reference`, lineStyle: LineStyle.Dashed, color: token('--muted'), lineWidth: 1, axisLabelVisible: true })
      referenceThemes.push(() => reference.applyOptions({ color: token('--muted') }))
    }
  }
  if (visible.macd) {
    const index = pane++
    const histogram = chart.addSeries(HistogramSeries, { title: 'MACD histogram', color: token('--intelligence-histogram'),
      priceLineVisible: false, lastValueVisible: false, priceFormat: { type: 'price', precision: 4, minMove: .0001 } }, index)
    histogram.setData(measurementPoints(rows, 'macd_histogram'))
    themed.push({ applyOptions: options => histogram.applyOptions(options), token: '--intelligence-histogram' })
    addLine('macd', 'MACD', '--brand-text', index)
    addLine('macd_signal', 'MACD signal', '--intelligence-secondary', index)
  }
  chart.panes().forEach((item, index) => item.setStretchFactor(index === 0 ? 3 : 1))
  return () => { themed.forEach(item => item.applyOptions({ color: token(item.token) })); referenceThemes.forEach(apply => apply()) }
}
