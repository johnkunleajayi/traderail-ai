import { apiBase } from '../auth/api'
import type { StoredCandle } from '../history/api'
import type { ExperimentMetadata } from './types'
import { decimal, record, timeKey, type ResearchData } from './research'
const duration = { '15m':900000, '1h':3600000, '4h':14400000, '1d':86400000 }
const unavailable = () => new Error('Stored candles do not match this experiment window. Candlestick markers are hidden; backend performance and trades remain available.')
const normalized = (value: string) => { const [whole, tail = ''] = value.split('.'); return whole.replace(/^0+(?=\d)/,'') + '.' + tail.replace(/0+$/,'') }
export function alignExperimentCandles(value: unknown, meta: ExperimentMetadata, research: ResearchData): StoredCandle[] {
  if (!Array.isArray(value) || value.length > 1000) throw unavailable()
  let previous = ''
  const all = value.map(item => {
    const row = record(item), key = timeKey(row.open_time)
    if (key <= previous || row.canonical_symbol !== meta.canonical_symbol || row.provider !== meta.provider || row.timeframe !== meta.timeframe
      || !['open','high','low','close','volume'].every(k => decimal(row[k]))) throw unavailable()
    previous = key; timeKey(row.close_time); timeKey(row.retrieved_at)
    return row as unknown as StoredCandle
  })
  const candles = all.filter(c => timeKey(c.open_time) >= timeKey(meta.warmup_start) && timeKey(c.open_time) < timeKey(meta.evaluation_end))
  if (candles.length !== meta.candle_count || !candles.length || timeKey(candles[0].open_time) !== timeKey(meta.warmup_start)
    || Date.parse(candles[candles.length-1].open_time) + duration[meta.timeframe] !== Date.parse(meta.evaluation_end)) throw unavailable()
  const evaluation = candles.filter(c => timeKey(c.open_time) >= timeKey(meta.evaluation_start))
  if (evaluation.length !== research.decisions.length || candles.length - evaluation.length !== meta.warmup_candle_count) throw unavailable()
  candles.forEach((c,i) => {
    if (i > 0 && Date.parse(c.open_time) - Date.parse(candles[i-1].open_time) !== duration[meta.timeframe]) throw unavailable()
  })
  evaluation.forEach((c,i) => {
    const d = research.decisions[i]
    if (timeKey(c.open_time) !== timeKey(d.open_time) || timeKey(c.close_time) !== timeKey(d.timestamp)) throw unavailable()
  })
  const at = new Map(candles.map(c => [timeKey(c.open_time),c]))
  for (const trade of research.trades) {
    if (normalized(at.get(timeKey(trade.entry_fill_time))?.open ?? '') !== normalized(trade.entry_reference_price)
      || normalized(at.get(timeKey(trade.exit_fill_time))?.open ?? '') !== normalized(trade.exit_reference_price)) throw unavailable()
  }
  const p = research.open_position
  if (p && (normalized(at.get(timeKey(p.entry_fill_time))?.open ?? '') !== normalized(p.entry_reference_price)
    || normalized(candles[candles.length-1].close) !== normalized(p.mark_price))) throw unavailable()
  return candles
}
export async function readExperimentCandles(meta: ExperimentMetadata, research: ResearchData, signal?: AbortSignal) {
  const response = await fetch(`${apiBase}/api/market-data/stored-candles/${encodeURIComponent(meta.canonical_symbol)}?timeframe=${meta.timeframe}&provider=${meta.provider}&limit=1000`,
    { credentials:'omit',cache:'no-store',signal })
  if (!response.ok) throw new Error('Stored experiment candles are unavailable. No replacement data is shown.')
  try { return alignExperimentCandles(await response.json(),meta,research) }
  catch { throw unavailable() }
}
