import { useEffect, useRef, useState } from 'react'
import { CandlestickSeries, ColorType, createChart, type LogicalRange, type Time, type UTCTimestamp } from 'lightweight-charts'
import { formatIndicator, formatMarketValue, formatVolatility } from '../markets/format'
import type { StoredCandle } from '../history/api'
import { alignIntelligence, latestAvailable, type Intelligence } from '../history/intelligence'
import { addIntelligence, defaultIndicators, indicatorControls } from '../history/intelligenceChart'

function utcLabel(value: string) { return new Date(value).toISOString().replace('T', ' ').slice(0, 16) + ' UTC' }
const inspectFields = [{ key: 'ema_20', label: 'EMA20' }, { key: 'ema_50', label: 'EMA50' }, { key: 'rsi_14', label: 'RSI14' },
  { key: 'atr_14', label: 'ATR14' }, { key: 'macd', label: 'MACD' }, { key: 'macd_signal', label: 'MACD signal' }, { key: 'macd_histogram', label: 'MACD histogram' }] as const

export default function HistoryChart({ candles, intelligence, intelligenceError }: { candles: StoredCandle[]; intelligence: Intelligence | null; intelligenceError: string }) {
  const host = useRef<HTMLDivElement>(null)
  const reset = useRef<() => void>(() => {})
  const range = useRef<LogicalRange | null>(null)
  const priorCandles = useRef(candles)
  const [selected, setSelected] = useState(candles[candles.length - 1].open_time)
  const inspected = candles.find(c => c.open_time === selected) ?? candles[candles.length - 1]
  const [visible, setVisible] = useState(defaultIndicators)
  const [error, setError] = useState('')
  let aligned: ReturnType<typeof alignIntelligence> | null = null
  let alignmentError = ''
  try { if (intelligence) aligned = alignIntelligence(candles, intelligence) }
  catch (reason) { alignmentError = reason instanceof Error ? reason.message : 'Intelligence alignment failed.' }
  const feature = aligned?.get(Date.parse(inspected.open_time))
  const intelligenceReady = intelligence && aligned && !alignmentError && !intelligenceError
  useEffect(() => {
    if (!host.current) return
    setError('')
    if (priorCandles.current !== candles) { range.current = null; priorCandles.current = candles }
    const latest = candles[candles.length - 1]
    const data = candles.map(c => ({ time: (Date.parse(c.open_time) / 1000) as UTCTimestamp,
      open: Number(c.open), high: Number(c.high), low: Number(c.low), close: Number(c.close) }))
    if (data.some(c => ![c.open, c.high, c.low, c.close].every(value => Number.isFinite(value) && value > 0))) {
      setError('This stored dataset could not be rendered. No substitute candles are shown.'); return
    }
    const chart = createChart(host.current, { autoSize: true, layout: { attributionLogo: true },
      timeScale: { timeVisible: true, secondsVisible: false },
      rightPriceScale: { scaleMargins: { top: .12, bottom: .12 } },
      localization: { locale: 'en-GB', timeFormatter: (time: Time) => typeof time === 'number' ? utcLabel(new Date(time * 1000).toISOString()) : String(time) },
    })
    const series = chart.addSeries(CandlestickSeries, { borderVisible: false, priceFormat: { type: 'price', precision: 2, minMove: .01 } })
    const token = (name: string) => getComputedStyle(host.current!).getPropertyValue(name).trim()
    let themeIntelligence = () => {}
    try {
      if (intelligence && !intelligenceError && !alignmentError) {
        alignIntelligence(candles, intelligence)
        themeIntelligence = addIntelligence(chart, intelligence.features, visible, token)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Measurements could not be rendered.')
      chart.remove(); return
    }
    const applyTheme = () => {
      chart.applyOptions({ layout: { background: { type: ColorType.Solid, color: token('--surface') }, textColor: token('--muted'), fontFamily: 'Inter, Segoe UI, sans-serif' },
        grid: { vertLines: { visible: false }, horzLines: { color: token('--line') } },
        rightPriceScale: { borderColor: token('--line') }, timeScale: { borderColor: token('--line') },
        crosshair: { vertLine: { color: token('--brand-text'), labelBackgroundColor: token('--brand-text') }, horzLine: { color: token('--brand-text'), labelBackgroundColor: token('--brand-text') } } })
      series.applyOptions({ upColor: token('--color-success'), downColor: token('--color-risk'), wickUpColor: token('--color-success'), wickDownColor: token('--color-risk') })
      themeIntelligence()
    }
    applyTheme()
    const themeObserver = new MutationObserver(applyTheme)
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    series.setData(data)
    if (range.current) chart.timeScale().setVisibleLogicalRange(range.current)
    else chart.timeScale().fitContent()
    reset.current = () => chart.timeScale().fitContent()
    const originals = new Map(candles.map(c => [Date.parse(c.open_time) / 1000, c]))
    chart.subscribeCrosshairMove(event => setSelected(typeof event.time === 'number' ? (originals.get(event.time) ?? latest).open_time : latest.open_time))
    return () => { range.current = chart.timeScale().getVisibleLogicalRange(); themeObserver.disconnect(); reset.current = () => {}; chart.remove() }
  }, [candles, intelligence, intelligenceError, alignmentError, visible])
  const atr = intelligenceReady ? latestAvailable(intelligence.features, 'atr_14') : null
  const volatility = intelligenceReady ? latestAvailable(intelligence.features, 'realized_volatility') : null
  const panelCount = intelligenceReady ? Number(visible.rsi) + Number(visible.macd) : 0
  return <div className="history-chart-block">
    <div className="intelligence-heading"><span>Market Intelligence{intelligenceReady ? ` · Engine v${intelligence.feature_configuration.engine_version}` : ''}</span><span>Measurements only</span></div>
    {(intelligenceError || alignmentError) && <p className="history-error" role="status">{intelligenceError || alignmentError}</p>}
    <div className="intelligence-controls" role="group" aria-label="Show market measurements">{indicatorControls.map(control => <button type="button" key={control.key} disabled={!intelligenceReady} aria-pressed={visible[control.key]} className={`intelligence-toggle intelligence-${control.key}`} onClick={() => setVisible(current => ({ ...current, [control.key]: !current[control.key] }))}>{control.label}</button>)}</div>
    {intelligenceReady && <>
      <div className="intelligence-measures"><div><span>Latest available ATR14</span><strong>{atr === null ? 'Unavailable · insufficient history' : formatMarketValue(atr)}</strong></div><div><span>Realized volatility · Unannualized · {intelligence.feature_configuration.realized_volatility_window} log returns</span><strong>{volatility === null ? 'Unavailable · insufficient history' : formatVolatility(volatility)}</strong></div></div>
      <p className="history-caption">{visible.ema20 && 'EMA20 · '}{visible.ema50 && 'EMA50 · '}{visible.rsi && 'RSI14 · 30 / 70 reference levels only · '}{visible.macd && 'MACD line / signal / histogram · '}Shared UTC time axis. Missing warm-up values remain unavailable.</p>
      {inspectFields.filter(field => (field.key === 'ema_20' && visible.ema20 || field.key === 'ema_50' && visible.ema50 || field.key === 'rsi_14' && visible.rsi || field.key === 'macd_signal' && visible.macd) && !intelligence.features.some(row => row[field.key] !== null)).map(field => <p className="history-caption" key={field.key}>{field.label}: insufficient stored history for this calculation window.</p>)}
    </>}
    <div className="history-inspector">
      <label className="history-inspect-control">Inspect candle (UTC)<select value={inspected.open_time} onChange={event => setSelected(event.target.value)}>{candles.map(c => <option key={c.open_time} value={c.open_time}>{utcLabel(c.open_time)}</option>)}</select></label>
      <dl className="history-ohlc">{(['open', 'high', 'low', 'close'] as const).map(field => <div key={field}><dt>{field}</dt><dd title={inspected[field]}>{formatMarketValue(inspected[field])}</dd></div>)}</dl>
    </div>
    {feature && <details className="intelligence-inspector"><summary>Selected candle measurements · exact backend values</summary><dl>{(['open', 'high', 'low', 'close'] as const).map(field => <div key={field}><dt>{field} · exact</dt><dd>{inspected[field]}</dd></div>)}{inspectFields.map(field => { const raw = feature[field.key]; return <div key={field.key}><dt>{field.label}</dt><dd>{raw === null ? 'Unavailable · warm-up' : <>{formatIndicator(raw, field.key === 'rsi_14' ? 'rsi' : 'market')}<br/><span className="history-caption">Exact: {raw}</span></>}</dd></div> })}</dl></details>}
    {error && <p className="history-error" role="status">{error}</p>}
    <div ref={host} className="history-canvas intelligence-canvas" style={{ '--intelligence-panels': panelCount } as React.CSSProperties} role="img" aria-label={`${candles[0].canonical_symbol} stored historical candlesticks${intelligenceReady && visible.rsi ? ', RSI14 panel with 30 and 70 reference levels' : ''}${intelligenceReady && visible.macd ? ', MACD panel with line, signal and histogram' : ''}. Use the candle selector for exact values.`}/>
    <div className="history-chart-help"><span>Scroll to zoom · drag to pan · crosshair to inspect · times in UTC</span><button type="button" className="market-refresh" onClick={() => reset.current()}>Reset view</button></div>
    <p className="history-attribution">TradingView Lightweight Charts™ · Copyright (с) 2025 <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView, Inc.</a></p>
  </div>
}
