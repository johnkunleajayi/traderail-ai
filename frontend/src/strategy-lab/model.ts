import { historicalSymbols, timeframes, type StoredCandle, type Timeframe } from '../history/api'
import type { ExperimentRequest } from './types'

export const defaultForm = {
  canonical_symbol: historicalSymbols[0] as string, provider: 'BINANCE', timeframe: '1h' as Timeframe,
  requested_candle_count: '100', evaluation_start: '', evaluation_end: '', initial_capital: '10000',
  quantity: '0.001', fee_rate: '0', full_spread_rate: '0', slippage_rate: '0', delay_candles: '0',
}
export type ExperimentFormValues = typeof defaultForm
const financial = /^(?:(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,24})?|\.[0-9]{1,24})$/
const durations = { '15m': 900000, '1h': 3600000, '4h': 14400000, '1d': 86400000 }
export function storedBoundaries(candles: StoredCandle[], timeframe: Timeframe) {
  if (candles.length < 53) throw new Error('At least 53 stored candles are needed, including 52 for warm-up. Strategy Lab does not load new history.')
  const input = (time: string) => new Date(time).toISOString().slice(0, 19)
  return { requested_candle_count: String(candles.length), evaluation_start: input(candles[52].open_time),
    evaluation_end: input(new Date(Date.parse(candles[candles.length - 1].open_time) + durations[timeframe]).toISOString()) }
}
function utc(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(value)) throw new Error('Choose an explicit UTC evaluation start and end.')
  const full = value.length === 16 ? value + ':00' : value
  const result = new Date(full + 'Z')
  if (!Number.isFinite(result.getTime()) || result.toISOString().slice(0, 19) !== full) throw new Error('Choose valid UTC evaluation timestamps.')
  return result.toISOString()
}
function integer(value: string, min: number, max: number, label: string) {
  if (!/^\d+$/.test(value)) throw new Error(`${label} must be a whole number.`)
  const result = Number(value) // Counts/delays only, never financial values.
  if (!Number.isSafeInteger(result) || result < min || result > max) throw new Error(`${label} must be between ${min} and ${max}.`)
  return result
}
export function buildRequest(form: ExperimentFormValues): ExperimentRequest {
  if (!(historicalSymbols as readonly string[]).includes(form.canonical_symbol) || form.provider !== 'BINANCE'
      || !timeframes.includes(form.timeframe)) throw new Error('Choose a supported historical instrument, provider and timeframe.')
  for (const key of ['initial_capital', 'quantity', 'fee_rate', 'full_spread_rate', 'slippage_rate'] as const) {
    if (!financial.test(form[key])) throw new Error('Financial inputs must be plain decimal values with at most 24 digits on each side of the decimal point.')
  }
  for (const key of ['initial_capital', 'quantity'] as const) {
    if (!/[1-9]/.test(form[key])) throw new Error('Initial capital and fixed quantity must be greater than zero.')
  }
  for (const key of ['fee_rate', 'full_spread_rate', 'slippage_rate'] as const) {
    const [whole, fraction = ''] = form[key].split('.')
    if (BigInt(whole || '0') > 1n || (whole === '1' && /[1-9]/.test(fraction))) throw new Error('Research cost rates must be between zero and one.')
  }
  const evaluation_start = utc(form.evaluation_start), evaluation_end = utc(form.evaluation_end)
  if (Date.parse(evaluation_start) >= Date.parse(evaluation_end)) throw new Error('Evaluation start must precede the exclusive evaluation end.')
  return { canonical_symbol: form.canonical_symbol, provider: 'BINANCE', timeframe: form.timeframe,
    requested_candle_count: integer(form.requested_candle_count, 53, 300, 'Historical candle count'),
    evaluation_start, evaluation_end, initial_capital: form.initial_capital, quantity: form.quantity,
    execution: { fee_rate: form.fee_rate, full_spread_rate: form.full_spread_rate, slippage_rate: form.slippage_rate,
      delay_candles: integer(form.delay_candles, 0, 300, 'Execution delay') },
    strategy_family: 'ADAPTIVE_TREND', strategy_version: '1.0.0' }
}
