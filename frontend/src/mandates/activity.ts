import { apiBase } from '../auth/api'
import { MandateApiError } from './api'
import { decimalString, tone } from './performance'
export type ActivityCategory = 'PENDING' | 'NO_TRADE' | 'ENTRY_EXECUTED' | 'POSITION_HELD' | 'POSITION_CLOSED' | 'RISK_REJECTED' | 'SKIPPED' | 'FAILURE' | 'COMPLETED'
export interface PaperActivity {
  scope: 'PAPER_ACTIVITY_READ_ONLY'; mandate_identity: string; environment: 'PAPER'; currency: 'USDT'
  portfolio_cash: string | null; portfolio_state: 'AVAILABLE' | 'UNAVAILABLE'
  runner_health: 'UNVERIFIED'; runner_health_reason: 'HEARTBEAT_NOT_PERSISTED'; observed_at: string
  latest_evaluation: null | { work_id: string; state: string; category: ActivityCategory; outcome_code: string | null
    reason_code: string | null; attempts: number; recorded_at: string; last_attempt_at: string | null; retry_at: string | null; fresh_until: string }
}
const bad = () => new MandateApiError('Paper activity could not be verified. Try refreshing.', 502)
const uuid = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(v)
const date = (v: unknown) => typeof v === 'string' && /(?:Z|\+00:00)$/.test(v) && Number.isFinite(Date.parse(v))
const code = (v: unknown) => v === null || typeof v === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(v)
export function parseActivity(value: unknown, id: string): PaperActivity {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw bad()
  const v = value as PaperActivity
  if (!uuid(id) || v.mandate_identity !== id || v.scope !== 'PAPER_ACTIVITY_READ_ONLY' || v.environment !== 'PAPER'
    || v.currency !== 'USDT' || !date(v.observed_at) || v.runner_health !== 'UNVERIFIED' || v.runner_health_reason !== 'HEARTBEAT_NOT_PERSISTED'
    || !['AVAILABLE','UNAVAILABLE'].includes(v.portfolio_state)) throw bad()
  if (v.portfolio_state === 'AVAILABLE' ? !decimalString(v.portfolio_cash) || tone(v.portfolio_cash) === 'negative' : v.portfolio_cash !== null) throw bad()
  const e = v.latest_evaluation
  if (e !== null) {
    if (!e || !uuid(e.work_id) || !['PENDING','RETRY_WAIT','COMPLETED','SKIPPED','RECONCILIATION_REQUIRED'].includes(e.state)
      || !['PENDING','NO_TRADE','ENTRY_EXECUTED','POSITION_HELD','POSITION_CLOSED','RISK_REJECTED','SKIPPED','FAILURE','COMPLETED'].includes(e.category)
      || !code(e.outcome_code) || !code(e.reason_code) || !Number.isSafeInteger(e.attempts) || e.attempts < 0
      || !date(e.recorded_at) || !date(e.fresh_until) || ![e.last_attempt_at,e.retry_at].every(v => v === null || date(v))) throw bad()
    if (['PENDING','RETRY_WAIT'].includes(e.state) ? e.category !== 'PENDING' : e.state === 'RECONCILIATION_REQUIRED' ? e.category !== 'FAILURE' : e.state === 'SKIPPED' ? e.category !== 'SKIPPED' : ['PENDING','FAILURE','SKIPPED'].includes(e.category)) throw bad()
  }
  return v
}
export async function readActivity(id: string, signal?: AbortSignal): Promise<PaperActivity> {
  if (!uuid(id)) throw bad()
  let response: Response
  try { response = await fetch(`${apiBase}/api/mandates/${encodeURIComponent(id)}/activity`, { credentials:'include', cache:'no-store', signal }) }
  catch (error) { if (signal?.aborted) throw error; throw new MandateApiError('Paper activity is temporarily unreachable.', 0) }
  if (!response.ok) throw new MandateApiError(response.status === 401 ? 'Your session has expired.' : 'Paper activity is unavailable. No trading state has changed.', response.status)
  let value: unknown; try { value = await response.json() } catch { throw bad() }
  return parseActivity(value,id)
}
