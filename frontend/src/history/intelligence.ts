import { apiBase } from '../auth/api'
import { historyLimit, type StoredCandle, type Timeframe } from './api'

export const measurementFields = ['ema_20', 'ema_50', 'rsi_14', 'atr_14', 'macd', 'macd_signal', 'macd_histogram', 'simple_return', 'log_return', 'realized_volatility'] as const
export type Measurement = typeof measurementFields[number]
export type FeatureRow = { open_time: string; close_time: string; close: string } & Record<Measurement, string | null>
export interface Intelligence {
  canonical_symbol: string
  provider: 'BINANCE'
  timeframe: Timeframe
  candle_count: number
  first_open_time: string
  latest_close_time: string
  feature_configuration: { engine_version: string; annualization: false; realized_volatility_window: number }
  features: FeatureRow[]
}
const decimal = /^-?\d+(?:\.\d+)?$/
const validTime = (value: unknown): value is string => typeof value === 'string' && Number.isFinite(Date.parse(value))
export async function readIntelligence(symbol: string, timeframe: Timeframe, signal?: AbortSignal): Promise<Intelligence> {
  const response = await fetch(`${apiBase}/api/market-intelligence/${encodeURIComponent(symbol)}?timeframe=${timeframe}&provider=BINANCE&limit=${historyLimit}`, { credentials: 'omit', cache: 'no-store', signal })
  if (!response.ok) {
    if (response.status === 409) throw new Error('Intelligence is unavailable for this stored dataset. It may be empty, incomplete or fail data-quality validation. No substitute measurements are shown.')
    throw new Error('Market Intelligence could not be loaded. Stored candles remain available.')
  }
  const data = await response.json()
  if (!data || data.canonical_symbol !== symbol || data.provider !== 'BINANCE' || data.timeframe !== timeframe
    || !Array.isArray(data.features) || !Number.isSafeInteger(data.candle_count) || data.candle_count < 1 || data.candle_count > historyLimit
    || data.features.length !== data.candle_count || !validTime(data.first_open_time) || !validTime(data.latest_close_time)
    || typeof data.feature_configuration?.engine_version !== 'string' || !data.feature_configuration.engine_version
    || data.feature_configuration.annualization !== false || !Number.isSafeInteger(data.feature_configuration.realized_volatility_window)
    || data.feature_configuration.realized_volatility_window < 2) throw new Error('Market Intelligence response could not be read.')
  let previous = -Infinity
  for (const row of data.features) {
    if (!row || !validTime(row.open_time) || !validTime(row.close_time) || Date.parse(row.open_time) <= previous
      || Date.parse(row.close_time) < Date.parse(row.open_time) || typeof row.close !== 'string' || !decimal.test(row.close)
      || !measurementFields.every(field => row[field] === null || typeof row[field] === 'string' && decimal.test(row[field]))) {
      throw new Error('Market Intelligence response could not be read.')
    }
    previous = Date.parse(row.open_time)
  }
  if (Date.parse(data.first_open_time) !== Date.parse(data.features[0].open_time)
    || Date.parse(data.latest_close_time) !== Date.parse(data.features.at(-1).close_time)) throw new Error('Market Intelligence response could not be read.')
  return data
}

export function alignIntelligence(candles: StoredCandle[], data: Intelligence): Map<number, FeatureRow> {
  const mismatch = () => new Error('Intelligence does not align with the stored candles. Reload stored data to retry; no indicators have been shifted or substituted.')
  if (candles.length !== data.features.length || data.candle_count !== candles.length) throw mismatch()
  const rows = new Map<number, FeatureRow>()
  for (const row of data.features) {
    const time = Date.parse(row.open_time)
    if (rows.has(time)) throw mismatch()
    rows.set(time, row)
  }
  const seen = new Set<number>()
  for (const candle of candles) {
    const time = Date.parse(candle.open_time)
    if (seen.has(time)) throw mismatch()
    seen.add(time)
    const row = rows.get(Date.parse(candle.open_time))
    if (!row || candle.canonical_symbol !== data.canonical_symbol || candle.provider !== data.provider || candle.timeframe !== data.timeframe
      || Date.parse(candle.close_time) !== Date.parse(row.close_time) || candle.close !== row.close) throw mismatch()
  }
  return rows
}

export function latestAvailable(rows: FeatureRow[], field: Measurement): string | null {
  for (let index = rows.length - 1; index >= 0; index--) if (rows[index][field] !== null) return rows[index][field]
  return null
}
