import { apiBase } from '../auth/api'

export const timeframes = ['15m', '1h', '4h', '1d'] as const
export type Timeframe = typeof timeframes[number]
// Canonical selector scope for the currently supported historical research provider.
// These are not venue symbols or embedded candle/mapping data.
export const historicalSymbols = ['BTC/USDT', 'ETH/USDT'] as const
export const historyLimit = 100
export interface StoredCandle {
  canonical_symbol: string
  provider: 'BINANCE'
  timeframe: Timeframe
  open_time: string
  close_time: string
  open: string
  high: string
  low: string
  close: string
  volume: string
  retrieved_at: string
}
export interface DatasetQuality {
  canonical_symbol: string
  provider: 'BINANCE'
  timeframe: Timeframe
  candle_count: number
  duplicate_count: number
  missing_interval_count: number
  freshness: 'fresh' | 'stale' | 'empty'
  evaluated_at: string
}
export interface IngestionResult { fetched: number; inserted: number; unchanged: number }
const decimal = /^\d+(?:\.\d+)?$/
const validTime = (value: unknown): value is string => typeof value === 'string' && Number.isFinite(Date.parse(value))
const count = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0
function selection(item: Record<string, unknown>, symbol: string, timeframe: Timeframe) {
  return item.canonical_symbol === symbol && item.provider === 'BINANCE' && item.timeframe === timeframe
}
export async function readStoredCandles(symbol: string, timeframe: Timeframe, signal?: AbortSignal): Promise<StoredCandle[]> {
  const response = await fetch(`${apiBase}/api/market-data/stored-candles/${encodeURIComponent(symbol)}?timeframe=${timeframe}&provider=BINANCE&limit=${historyLimit}`, { credentials: 'omit', cache: 'no-store', signal })
  if (!response.ok) throw new Error('Stored history could not be loaded. Please retry.')
  const data: unknown = await response.json()
  if (!Array.isArray(data) || data.length > historyLimit) throw new Error('Stored history could not be read.')
  let previous = -Infinity
  for (const item of data) {
    if (!item || !selection(item, symbol, timeframe) || !validTime(item.open_time) || !validTime(item.close_time)
      || !validTime(item.retrieved_at) || Date.parse(item.open_time) <= previous
      || !['open', 'high', 'low', 'close', 'volume'].every(field => typeof item[field] === 'string' && decimal.test(item[field]))) {
      throw new Error('Stored history could not be read.')
    }
    previous = Date.parse(item.open_time)
  }
  return data
}
export async function readQuality(symbol: string, timeframe: Timeframe, signal?: AbortSignal): Promise<DatasetQuality> {
  const response = await fetch(`${apiBase}/api/market-data/candle-quality/${encodeURIComponent(symbol)}?timeframe=${timeframe}&provider=BINANCE`, { credentials: 'omit', cache: 'no-store', signal })
  if (!response.ok) throw new Error('Dataset quality is temporarily unavailable.')
  const data = await response.json()
  if (!data || !selection(data, symbol, timeframe) || !count(data.candle_count) || !count(data.duplicate_count)
    || !count(data.missing_interval_count) || !['fresh', 'stale', 'empty'].includes(data.freshness)
    || !validTime(data.evaluated_at)) throw new Error('Dataset quality could not be read.')
  return data
}
export async function ingestHistory(symbol: string, timeframe: Timeframe, signal?: AbortSignal): Promise<IngestionResult> {
  const response = await fetch(`${apiBase}/api/market-data/candle-ingestions/${encodeURIComponent(symbol)}?timeframe=${timeframe}&limit=${historyLimit}`, { method: 'POST', credentials: 'include', cache: 'no-store', signal })
  if (response.status === 401) throw new Error('Your session has expired. Sign in again to load history.')
  if (response.status === 403) throw new Error('History could not be loaded from this origin. Check your local frontend URL.')
  if (response.status === 409) throw new Error('History could not be updated. The backend reported a data-integrity conflict or unsupported dataset; stored candles have not been overwritten.')
  if (!response.ok) throw new Error('Historical data is temporarily unavailable. No replacement candles were created.')
  const data = await response.json()
  if (!data || !selection(data, symbol, timeframe) || !count(data.fetched) || !count(data.inserted) || !count(data.unchanged)) throw new Error('The ingestion result could not be read. Reload stored data to check its status.')
  return data
}
