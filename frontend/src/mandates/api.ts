import { apiBase } from '../auth/api'
import { buildIntent, positiveDecimal, riskOptions, styleOptions } from './model'
import type { MandateAction, MandateIntent, TradingMandate } from './types'
export class MandateApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = 'MandateApiError' }
}
const malformed = () => new MandateApiError('The mandate response could not be confirmed. Reload saved mandates before retrying a change.', 502)
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(v)
const utc = (v: unknown): v is string => typeof v === 'string' && /^\d{4}-\d\d-\d\dT.*(?:Z|\+00:00)$/.test(v) && Number.isFinite(Date.parse(v))
export function parseMandate(value: unknown): TradingMandate {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw malformed()
  const row = value as Record<string, unknown>
  if (!uuid(row.id) || !uuid(row.user_id) || row.environment !== 'PAPER' || row.allocation_currency !== 'USDT' || row.instrument !== 'BTC/USDT' || row.direction_capability !== 'LONG_ONLY'
    || !positiveDecimal(row.capital_allocation) || !riskOptions.some(option => option.value === row.risk_level) || !styleOptions.some(option => option.value === row.trading_style)
    || !['DRAFT', 'ACTIVE', 'PAUSED', 'RETIRED'].includes(row.status as string) || !utc(row.created_at) || !utc(row.updated_at)) throw malformed()
  return row as unknown as TradingMandate
}
function idPath(id: string) { if (!uuid(id)) throw malformed(); return `/api/mandates/${encodeURIComponent(id)}` }
function intentBody(intent: MandateIntent): MandateIntent {
  // Explicit allowlist: owner, balances, quantity, prices and technical parameters never travel here.
  return buildIntent({ capital: intent.capital_allocation, risk: intent.risk_level, style: intent.trading_style })
}
async function request(path: string, body?: object, signal?: AbortSignal): Promise<unknown> {
  let response: Response
  try { response = await fetch(`${apiBase}${path}`, { method: body === undefined ? 'GET' : 'POST', credentials: 'include', cache: 'no-store', signal,
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) }) }
  catch (error) { if (signal?.aborted) throw error; throw new MandateApiError('TradeRail could not be reached. A change may not be confirmed; reload saved mandates before retrying.', 0) }
  if (!response.ok) {
    const messages: Record<number, string> = {
      401: 'Your session has expired. Sign in again to manage mandates.',
      403: 'This request origin is not authorized. Check the TradeRail frontend URL.',
      404: 'This mandate is unavailable to your account. Reload your saved mandates.',
      409: 'The server could not apply this change. The mandate state or market eligibility may have changed. Reload its saved state before trying again.',
      422: 'The mandate inputs were rejected. Check the capital amount and selected preferences.',
    }
    throw new MandateApiError(messages[response.status] ?? 'Mandates are temporarily unavailable. No successful change has been confirmed.', response.status)
  }
  try { return await response.json() } catch { throw malformed() }
}
export async function listMandates(signal?: AbortSignal): Promise<TradingMandate[]> {
  const value = await request('/api/mandates', undefined, signal)
  if (!Array.isArray(value)) throw malformed()
  const rows = value.map(parseMandate)
  if (new Set(rows.map(row => row.id)).size !== rows.length) throw malformed()
  return rows
}
function matching(value: unknown, id: string) { const row = parseMandate(value); if (row.id !== id) throw malformed(); return row }
export async function readMandate(id: string, signal?: AbortSignal) { return matching(await request(idPath(id), undefined, signal), id) }
export async function createMandate(intent: MandateIntent, signal?: AbortSignal) { return parseMandate(await request('/api/mandates', intentBody(intent), signal)) }
export async function updateMandate(id: string, intent: MandateIntent, signal?: AbortSignal) { return matching(await request(idPath(id) + '/intent', intentBody(intent), signal), id) }
export async function changeMandate(id: string, action: MandateAction, signal?: AbortSignal) {
  if (!['activate', 'pause', 'retire'].includes(action)) throw malformed()
  return matching(await request(idPath(id) + '/' + action, {}, signal), id)
}
