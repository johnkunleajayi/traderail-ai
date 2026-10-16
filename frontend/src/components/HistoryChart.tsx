import { useEffect, useRef, useState } from 'react'
import { CandlestickSeries, ColorType, createChart, type Time, type UTCTimestamp } from 'lightweight-charts'
import type { StoredCandle } from '../history/api'

function utcLabel(value: string) { return new Date(value).toISOString().replace('T', ' ').slice(0, 16) + ' UTC' }

export default function HistoryChart({ candles }: { candles: StoredCandle[] }) {
  const host = useRef<HTMLDivElement>(null)
  const reset = useRef<() => void>(() => {})
  const [inspected, setInspected] = useState(candles[candles.length - 1])
  const [error, setError] = useState(false)
  useEffect(() => {
    if (!host.current) return
    setError(false)
    const latest = candles[candles.length - 1]
    setInspected(latest)
    // Decimal -> Number conversion occurs only here, at the canvas presentation boundary.
    const data = candles.map(c => ({ time: (Date.parse(c.open_time) / 1000) as UTCTimestamp,
      open: Number(c.open), high: Number(c.high), low: Number(c.low), close: Number(c.close) }))
    if (data.some(c => ![c.open, c.high, c.low, c.close].every(value => Number.isFinite(value) && value > 0))) {
      setError(true); return
    }
    const chart = createChart(host.current, { autoSize: true, layout: { attributionLogo: true },
      timeScale: { timeVisible: true, secondsVisible: false },
      rightPriceScale: { scaleMargins: { top: .12, bottom: .12 } },
      localization: { locale: 'en-GB', timeFormatter: (time: Time) => typeof time === 'number' ? utcLabel(new Date(time * 1000).toISOString()) : String(time) },
    })
    const series = chart.addSeries(CandlestickSeries, { borderVisible: false, priceFormat: { type: 'price', precision: 2, minMove: .01 } })
    const applyTheme = () => {
      const css = getComputedStyle(host.current!)
      const token = (name: string) => css.getPropertyValue(name).trim()
      chart.applyOptions({ layout: { background: { type: ColorType.Solid, color: token('--surface') }, textColor: token('--muted'), fontFamily: 'Inter, Segoe UI, sans-serif' },
        grid: { vertLines: { visible: false }, horzLines: { color: token('--line') } },
        rightPriceScale: { borderColor: token('--line') }, timeScale: { borderColor: token('--line') },
        crosshair: { vertLine: { color: token('--brand-text'), labelBackgroundColor: token('--brand-text') }, horzLine: { color: token('--brand-text'), labelBackgroundColor: token('--brand-text') } } })
      series.applyOptions({ upColor: token('--color-success'), downColor: token('--color-risk'), wickUpColor: token('--color-success'), wickDownColor: token('--color-risk') })
    }
    applyTheme()
    const themeObserver = new MutationObserver(applyTheme)
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    series.setData(data)
    chart.timeScale().fitContent()
    reset.current = () => chart.timeScale().fitContent()
    const originals = new Map(candles.map(c => [Date.parse(c.open_time) / 1000, c]))
    chart.subscribeCrosshairMove(event => setInspected(typeof event.time === 'number' ? originals.get(event.time) ?? latest : latest))
    return () => { themeObserver.disconnect(); reset.current = () => {}; chart.remove() }
  }, [candles])
  return <div className="history-chart-block">
    <div className="history-inspector">
      <label className="history-inspect-control">Inspect candle (UTC)<select value={inspected.open_time} onChange={event => setInspected(candles.find(c => c.open_time === event.target.value)!)}>{candles.map(c => <option key={c.open_time} value={c.open_time}>{utcLabel(c.open_time)}</option>)}</select></label>
      <dl className="history-ohlc">{(['open', 'high', 'low', 'close'] as const).map(field => <div key={field}><dt>{field}</dt><dd>{inspected[field]}</dd></div>)}</dl>
    </div>
    {error ? <p className="history-state" role="status">This stored dataset could not be rendered. No substitute candles are shown.</p> : <div ref={host} className="history-canvas" role="img" aria-label={`${candles[0].canonical_symbol} stored historical candlestick chart. Use the candle selector above for exact OHLC values.`}/>}
    <div className="history-chart-help"><span>Scroll to zoom · drag to pan · crosshair to inspect · times in UTC</span><button type="button" className="market-refresh" onClick={() => reset.current()}>Reset view</button></div>
    <p className="history-attribution">TradingView Lightweight Charts™ · Copyright (с) 2025 <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView, Inc.</a></p>
  </div>
}
