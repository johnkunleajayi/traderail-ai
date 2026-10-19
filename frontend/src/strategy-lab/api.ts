import { parseResearch, unavailableResearch } from './research'
import { apiBase } from '../auth/api'
import { metricKeys, type ExperimentRequest, type ExperimentResult } from './types'

export class ExperimentApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = 'ExperimentApiError' }
}
const malformed = () => new ExperimentApiError('The experiment response could not be read. No replacement results were created.', 502)
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw malformed()
  return value as Record<string, unknown>
}
const decimal = (v: unknown): v is string => typeof v === 'string' && v.length <= 4096 && /^-?\d+(?:\.\d+)?$/.test(v)
const timestamp = (v: unknown): v is string => typeof v === 'string' && /^\d{4}-\d\d-\d\dT.*(?:Z|\+00:00)$/.test(v) && Number.isFinite(Date.parse(v))
const count = (v: unknown) => Number.isSafeInteger(v) && (v as number) >= 0
function equivalent(left: unknown, right: string) {
  if (!decimal(left) || !/^-?(?:\d+(?:\.\d+)?|\.\d+)$/.test(right)) return false
  const normalize = (value: string) => {
    const [whole, tail = ''] = value.split('.')
    return (whole || '0').replace(/^0+(?=\d)/, '') + '.' + tail.replace(/0+$/, '')
  }
  return normalize(left) === normalize(right)
}
export function parseExperiment(value: unknown, request: ExperimentRequest): ExperimentResult {
  const data = object(value), e = object(data.experiment), s = object(data.summary), cost = object(e.execution)
  if (e.canonical_symbol !== request.canonical_symbol || e.provider !== request.provider || e.timeframe !== request.timeframe
    || e.strategy_family !== 'ADAPTIVE_TREND' || e.strategy_version !== '1.0.0'
    || e.requested_candle_count !== request.requested_candle_count
    || !['experiment_identity', 'dataset_identity', 'research_result_identity', 'strategy_configuration_identity'].every(k => typeof e[k] === 'string' && (e[k] as string).length > 0)
    || !['warmup_start', 'evaluation_start', 'evaluation_end'].every(k => timestamp(e[k]))
    || Date.parse(e.evaluation_start as string) !== Date.parse(request.evaluation_start)
    || Date.parse(e.evaluation_end as string) !== Date.parse(request.evaluation_end)
    || !['candle_count', 'warmup_candle_count', 'required_warmup_candles', 'evaluation_candle_count'].every(k => count(e[k]))
    || e.candle_count !== request.requested_candle_count || e.required_warmup_candles !== 52
    || (e.warmup_candle_count as number) < 52 || e.evaluation_candle_count !== (e.candle_count as number) - (e.warmup_candle_count as number)
    || !equivalent(e.initial_capital, request.initial_capital) || !equivalent(e.quantity, request.quantity) || !equivalent(s.initial_capital, request.initial_capital)
    || !count(s.completed_trade_count) || !metricKeys.every(k => s[k] === null || decimal(s[k]))
    || !(s.gross_return === null || decimal(s.gross_return))
    || !(['fee_rate', 'full_spread_rate', 'slippage_rate'] as const).every(k => equivalent(cost[k], request.execution[k])) || cost.delay_candles !== request.execution.delay_candles
    || !['timing', 'cost_model_identity', 'execution_configuration_identity'].every(k => typeof cost[k] === 'string')
    || !Array.isArray(data.unfilled_decisions)) throw malformed()
  let position: ExperimentResult['open_position'] = null
  if (data.open_position !== null) {
    const p = object(data.open_position)
    if (!['LONG', 'SHORT'].includes(p.direction as string) || !decimal(p.quantity)) throw malformed()
    position = { direction: p.direction as 'LONG' | 'SHORT', quantity: p.quantity }
  }
  let research: ExperimentResult['research'] = null, research_error = ''
  try { research = parseResearch(data, e as unknown as ExperimentResult['experiment'], s.completed_trade_count as number) }
  catch { research_error = unavailableResearch }
  return { research, research_error, experiment: e as unknown as ExperimentResult['experiment'], summary: s as unknown as ExperimentResult['summary'],
    open_position: position, unfilled_decision_count: data.unfilled_decisions.length }
}
export async function runExperiment(request: ExperimentRequest, signal?: AbortSignal): Promise<ExperimentResult> {
  let response: Response
  try {
    response = await fetch(`${apiBase}/api/strategy-lab/experiments`, { method: 'POST', credentials: 'include', cache: 'no-store',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request), signal })
  } catch (error) {
    if (signal?.aborted) throw error
    throw new ExperimentApiError('TradeRail could not be reached. Check your connection and retry; no results were substituted.', 0)
  }
  if (!response.ok) {
    if (response.status === 401) throw new ExperimentApiError('Your session has expired. Sign in again before running research.', 401)
    if (response.status === 403) throw new ExperimentApiError('This frontend origin is not allowed to run research. Check the configured TradeRail frontend URL.', 403)
    if (response.status === 404) throw new ExperimentApiError('This canonical instrument is unavailable.', 404)
    if (response.status === 422) throw new ExperimentApiError('The research inputs or UTC boundaries were rejected. Check candle alignment, capital, quantity and cost rates.', 422)
    if (response.status === 409) {
      let code = ''
      try { code = (await response.json()).detail?.code ?? '' } catch { /* Fixed, safe fallback below. */ }
      const message = code === 'insufficient_stored_history' ? 'The exact requested instrument/provider/timeframe history is unavailable. No candles were ingested.'
        : code === 'insufficient_warmup' ? 'At least 52 requested candles must precede evaluation. No hidden history is added.'
        : code === 'invalid_candle_dataset' ? 'Stored candles failed research validation. Review dataset quality in Market History.'
        : 'Research execution or accounting could not complete with these assumptions. Review quantity, capital and dataset boundaries.'
      throw new ExperimentApiError(message, 409)
    }
    throw new ExperimentApiError('Research is temporarily unavailable. No fallback results were created. Please retry.', response.status)
  }
  try { return parseExperiment(await response.json(), request) }
  catch (error) { if (signal?.aborted) throw error; throw malformed() }
}
