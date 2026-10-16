import { apiBase } from '../auth/api'
import { historyLimit, type StoredCandle, type Timeframe } from './api'
export const regimeNames = ['BULL_TREND', 'BEAR_TREND', 'RANGE', 'BREAKOUT', 'HIGH_VOLATILITY', 'UNSAFE', 'UNKNOWN'] as const
export type Regime = typeof regimeNames[number]
export const regimeEvidenceFields = ['normalized_ema_spread', 'ema20_slope', 'ema50_slope', 'close_vs_ema20', 'close_vs_ema50', 'rsi_14', 'macd', 'macd_signal', 'macd_histogram', 'normalized_macd_histogram', 'normalized_atr', 'realized_volatility', 'volatility_baseline', 'volatility_ratio', 'normalized_atr_baseline', 'atr_ratio', 'reference_low', 'reference_high', 'upside_displacement', 'downside_displacement', 'upside_atr_displacement', 'downside_atr_displacement'] as const
export interface RegimeRow {
  open_time: string; close_time: string; regime: Regime; candidate_regime: Regime
  confirmation: { count: number; required: number; previous_regime: Regime | null; retained_previous: boolean; available_candles: number; candidate_history_required: number }
  evidence: Record<typeof regimeEvidenceFields[number], string | null> & { candidate: Regime; reason_codes: string[]; confirmation_count: number; confirmation_required: number; retained_previous: boolean; previous_regime: Regime | null; available_candles: number; candidate_history_required: number; breakout_direction: 'UP' | 'DOWN' | null }
  reason_codes: string[]
}
export interface RegimeDataset {
  canonical_symbol: string; base_asset: string; quote_asset: string; provider: 'BINANCE'; timeframe: Timeframe
  candle_count: number; dataset_start: string; dataset_end: string; regime_engine_version: string; feature_engine_version: string
  regime_configuration: Record<string, string | number>; regimes: RegimeRow[]; latest_regime: RegimeRow
}
const decimal = /^-?\d+(?:\.\d+)?$/
const count = (v: unknown): v is number => Number.isSafeInteger(v) && (v as number) >= 0
const name = (v: unknown): v is Regime => regimeNames.includes(v as Regime)
const time = (v: unknown): v is string => typeof v === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(v) && !/\.\d{3}\d*[1-9]\d*(?:Z|[+-]\d{2}:\d{2})$/.test(v) && Number.isFinite(Date.parse(v))
function same(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object') return false
  const left = Object.entries(a).sort(), right = Object.entries(b).sort()
  return left.length === right.length && left.every(([key,value],i) => key === right[i][0] && same(value,right[i][1]))
}
function validRow(row: RegimeRow): boolean {
  if (!row || !time(row.open_time) || !time(row.close_time) || Date.parse(row.close_time) < Date.parse(row.open_time)
    || !name(row.regime) || !name(row.candidate_regime) || !Array.isArray(row.reason_codes) || !row.reason_codes.length
    || !row.reason_codes.every(v => typeof v === 'string' && /^[A-Z][A-Z0-9_]*$/.test(v))) return false
  const c = row.confirmation, e = row.evidence
  return !!c && !!e && [c.count,c.required,c.available_candles,c.candidate_history_required].every(count)
    && c.available_candles >= 1 && c.available_candles <= historyLimit && c.count <= c.available_candles && c.candidate_history_required >= 1
    && (!c.retained_previous || c.previous_regime !== null)
    && typeof c.retained_previous === 'boolean' && (c.previous_regime === null || name(c.previous_regime))
    && regimeEvidenceFields.every(key => e[key] === null || typeof e[key] === 'string' && decimal.test(e[key]!))
    && e.candidate === row.candidate_regime && same(e.reason_codes,row.reason_codes)
    && e.confirmation_count === c.count && e.confirmation_required === c.required && e.retained_previous === c.retained_previous
    && e.previous_regime === c.previous_regime && e.available_candles === c.available_candles && e.candidate_history_required === c.candidate_history_required
    && [null,'UP','DOWN'].includes(e.breakout_direction)
}
export async function readRegimes(symbol: string, timeframe: Timeframe, signal?: AbortSignal): Promise<RegimeDataset> {
  const pair = symbol.split('/')
  if (pair.length !== 2 || !pair.every(s => /^[A-Z][A-Z0-9]{0,15}$/.test(s))) throw new Error('Canonical regime market is unavailable.')
  const response = await fetch(`${apiBase}/api/market-regimes/${encodeURIComponent(pair[0])}/${encodeURIComponent(pair[1])}?timeframe=${timeframe}&provider=BINANCE&limit=${historyLimit}`, { credentials: 'omit', cache: 'no-store', signal })
  if (!response.ok) throw new Error(response.status === 409 ? 'Regimes unavailable: stored history is empty or failed prerequisite validation. No classifications are inferred.' : 'Market regimes could not be loaded. Candle history remains available.')
  const d: RegimeDataset = await response.json()
  const fail = () => new Error('Market regime response could not be read. No classifications are inferred.')
  if (!d || d.canonical_symbol !== symbol || d.base_asset !== pair[0] || d.quote_asset !== pair[1] || d.provider !== 'BINANCE' || d.timeframe !== timeframe
    || !count(d.candle_count) || d.candle_count < 1 || d.candle_count > historyLimit || !Array.isArray(d.regimes) || d.regimes.length !== d.candle_count
    || !time(d.dataset_start) || !time(d.dataset_end) || typeof d.regime_engine_version !== 'string' || !d.regime_engine_version
    || typeof d.feature_engine_version !== 'string' || !d.feature_engine_version || !d.regime_configuration
    || d.regime_configuration.engine_version !== d.regime_engine_version
    || !Object.values(d.regime_configuration).every(v => typeof v === 'string' || count(v))) throw fail()
  let previous = -Infinity
  for (const row of d.regimes) {
    if (!validRow(row) || Date.parse(row.open_time) <= previous) throw fail()
    previous = Date.parse(row.open_time)
  }
  if (!same(d.latest_regime,d.regimes[d.regimes.length-1]) || Date.parse(d.dataset_start) !== Date.parse(d.regimes[0].open_time)
    || Date.parse(d.dataset_end) !== Date.parse(d.regimes[d.regimes.length-1].close_time)) throw fail()
  return d
}
export function alignRegimes(candles: StoredCandle[], data: RegimeDataset): Map<number, RegimeRow> {
  const fail = () => new Error('Regimes do not align with the stored candles. Reload stored data; regime bands are hidden and candle history remains usable.')
  if (data.candle_count !== candles.length || data.regimes.length !== candles.length || !candles.length) throw fail()
  const mapped = new Map<number,RegimeRow>()
  let previous = -Infinity
  for (const row of data.regimes) {
    if (!time(row.open_time) || !time(row.close_time) || Date.parse(row.open_time) <= previous) throw fail()
    previous = Date.parse(row.open_time); mapped.set(previous,row)
  }
  const seen = new Set<number>()
  for (const c of candles) {
    const key = Date.parse(c.open_time), row = mapped.get(key)
    if (!row || seen.has(key) || c.canonical_symbol !== data.canonical_symbol || c.provider !== data.provider || c.timeframe !== data.timeframe
      || Date.parse(c.close_time) !== Date.parse(row.close_time)) throw fail()
    seen.add(key)
  }
  if (Date.parse(data.dataset_start) !== Date.parse(candles[0].open_time) || Date.parse(data.dataset_end) !== Date.parse(candles[candles.length-1].close_time)) throw fail()
  return mapped
}
