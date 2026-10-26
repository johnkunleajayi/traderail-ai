import { apiBase } from '../auth/api'
import { MandateApiError } from './api'

export type Financial = string | null
export interface PerformanceLeg {
  intent_identity: string; decision_identity: string; strategy_decision_identity: string; strategy_input_identity: string
  strategy_reason_codes: string[] | null; risk_outcome: 'APPROVED'; registry_identity: string; authorization_identity: string
  request_identity: string; client_order_identity: string; order_identity: string; fill_identity: string; accounting_identity: string
  market_evidence_identity: string; executed_at: string; quantity: string; reference_price: string; execution_price: string
  reference_notional: string; gross_notional: string; fee: string
}
export interface PerformanceTrade {
  position_id: string; position_identity: string; instrument: string; instrument_id: string; strategy_family: string
  strategy_version: string; strategy_configuration_identity: string; direction: 'LONG'; status: 'OPEN' | 'CLOSED'
  quantity: string; allocation_at_entry: string; eligibility_identity: string; strategy_observation_identity: string
  entry: PerformanceLeg; exit: PerformanceLeg | null; gross_realized_pnl: Financial; net_realized_pnl: Financial
}
export interface MandatePerformance {
  scope: 'MANDATE_PERFORMANCE_READ_ONLY'; projection_version: 'mandate-performance-v1'; projection_identity: string
  mandate_identity: string; environment: 'PAPER'; currency: 'USDT'; mandate_status: 'DRAFT' | 'ACTIVE' | 'PAUSED' | 'RETIRED'
  status: 'DRAFT' | 'PAUSED' | 'RETIRED' | 'NO_TRADES_YET' | 'POSITION_OPEN' | 'CLOSED' | 'UNAVAILABLE'
  financial_state: 'AVAILABLE' | 'UNAVAILABLE'; allocation: string; performance_allocation: Financial; allocation_changed: boolean
  capital_semantics: 'ALLOCATION_CONTEXT_NOT_WALLET'; capital_exposed: Financial; capital_committed: Financial
  available_unallocated: Financial; allocation_shortfall: Financial; realized_pnl: Financial; gross_realized_pnl: Financial
  open_entry_fees: Financial; marked_exposure: Financial; price_unrealized_pnl: Financial; unrealized_pnl: Financial
  current_mandate_value: Financial; return_percentage: Financial
  valuation: { state: 'FRESH' | 'STALE' | 'UNAVAILABLE' | 'NOT_REQUIRED'; price: Financial; priced_at: string | null
    valid_until: string | null; source: string | null; evidence_identity: string | null; reason_codes: string[] }
  drawdown_state: 'UNAVAILABLE'; drawdown: null; drawdown_reason: string
  trade_count: number | null; completed_trade_count: number | null; trades: PerformanceTrade[]; reason_codes: string[]; evaluated_at: string
}
const bad = () => new MandateApiError('Performance could not be verified. Refresh the report to try again.', 502)
const object = (v: unknown): Record<string, unknown> => { if (!v || typeof v !== 'object' || Array.isArray(v)) throw bad(); return v as Record<string, unknown> }
export const decimalString = (v: unknown): v is string => typeof v === 'string' && v.length <= 4096 && /^-?\d+(?:\.\d+)?$/.test(v)
const financial = (v: unknown) => v === null || decimalString(v)
const text = (v: unknown): v is string => typeof v === 'string' && v.length > 0
const strings = (v: unknown) => Array.isArray(v) && v.every(text)
const date = (v: unknown) => typeof v === 'string' && /(?:Z|\+00:00)$/.test(v) && Number.isFinite(Date.parse(v))
const identity = (v: unknown) => typeof v === 'string' && /^sha256:[a-f0-9]{64}$/.test(v)
const uuid = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(v)
const inSet = (v: unknown, values: string[]) => typeof v === 'string' && values.includes(v)
function leg(value: unknown) {
  const v = object(value)
  for (const k of ['intent_identity','decision_identity','strategy_decision_identity','strategy_input_identity','registry_identity','authorization_identity','request_identity','client_order_identity','order_identity','fill_identity','accounting_identity','market_evidence_identity']) if (!identity(v[k])) throw bad()
  for (const k of ['quantity','reference_price','execution_price','reference_notional','gross_notional','fee']) if (!decimalString(v[k])) throw bad()
  if (!date(v.executed_at) || v.risk_outcome !== 'APPROVED' || !(v.strategy_reason_codes === null || strings(v.strategy_reason_codes))) throw bad()
}
export function parsePerformance(value: unknown, mandateId: string): MandatePerformance {
  const v = object(value)
  if (!uuid(mandateId) || v.mandate_identity !== mandateId || !identity(v.projection_identity) || v.scope !== 'MANDATE_PERFORMANCE_READ_ONLY'
    || v.projection_version !== 'mandate-performance-v1' || v.environment !== 'PAPER' || v.currency !== 'USDT'
    || v.capital_semantics !== 'ALLOCATION_CONTEXT_NOT_WALLET' || !inSet(v.financial_state,['AVAILABLE','UNAVAILABLE'])
    || !inSet(v.mandate_status,['DRAFT','ACTIVE','PAUSED','RETIRED']) || !inSet(v.status,['DRAFT','PAUSED','RETIRED','NO_TRADES_YET','POSITION_OPEN','CLOSED','UNAVAILABLE'])
    || !decimalString(v.allocation) || tone(v.allocation) !== 'positive' || typeof v.allocation_changed !== 'boolean'
    || !date(v.evaluated_at) || !strings(v.reason_codes) || v.drawdown_state !== 'UNAVAILABLE' || v.drawdown !== null || !text(v.drawdown_reason)) throw bad()
  const metrics = ['performance_allocation','capital_exposed','capital_committed','available_unallocated','allocation_shortfall','realized_pnl','gross_realized_pnl','open_entry_fees','marked_exposure','price_unrealized_pnl','unrealized_pnl','current_mandate_value','return_percentage']
  if (!metrics.every(k => financial(v[k]))) throw bad()
  for (const k of ['trade_count','completed_trade_count']) if (!(v[k] === null || typeof v[k] === 'number' && Number.isSafeInteger(v[k]) && (v[k] as number) >= 0)) throw bad()
  const mark = object(v.valuation)
  if (!inSet(mark.state,['FRESH','STALE','UNAVAILABLE','NOT_REQUIRED']) || !financial(mark.price) || !strings(mark.reason_codes)
    || ![mark.priced_at,mark.valid_until].every(x => x === null || date(x)) || ![mark.source,mark.evidence_identity].every(x => x === null || text(x))) throw bad()
  if (mark.state === 'FRESH' && (!decimalString(mark.price) || tone(mark.price) !== 'positive' || !date(mark.priced_at) || !date(mark.valid_until) || !identity(mark.evidence_identity) || !text(mark.source))) throw bad()
  if (mark.state !== 'FRESH' && mark.price !== null) throw bad()
  if (!Array.isArray(v.trades)) throw bad()
  const ids = new Set()
  for (const item of v.trades) {
    const t = object(item)
    if (!uuid(t.position_id) || ids.has(t.position_id) || !identity(t.position_identity) || !uuid(t.instrument_id) || t.instrument !== 'BTC/USDT' || t.direction !== 'LONG'
      || !inSet(t.status,['OPEN','CLOSED']) || !text(t.strategy_family) || !text(t.strategy_version) || !identity(t.strategy_configuration_identity)
      || !identity(t.eligibility_identity) || !identity(t.strategy_observation_identity) || !decimalString(t.quantity) || !decimalString(t.allocation_at_entry)
      || !financial(t.net_realized_pnl) || !financial(t.gross_realized_pnl)) throw bad()
    if (tone(t.allocation_at_entry as string) !== 'positive' || (t.status === 'OPEN' ? tone(t.quantity as string) !== 'positive' : tone(t.quantity as string) !== 'neutral')) throw bad()
    ids.add(t.position_id); leg(t.entry)
    if (t.status === 'OPEN' ? t.exit !== null || t.net_realized_pnl !== null || t.gross_realized_pnl !== null : t.exit === null || t.net_realized_pnl === null || t.gross_realized_pnl === null) throw bad()
    if (t.exit !== null) leg(t.exit)
  }
  if (v.financial_state === 'AVAILABLE' && (v.trade_count !== v.trades.length || v.completed_trade_count !== v.trades.filter(item => object(item).status === 'CLOSED').length || mark.state === 'NOT_REQUIRED' && v.trades.some(item => object(item).status === 'OPEN'))) throw bad()
  if (v.financial_state === 'UNAVAILABLE' && (metrics.some(k => v[k] !== null) || v.trades.length || v.trade_count !== null || v.completed_trade_count !== null)) throw bad()
  return v as unknown as MandatePerformance
}
export async function readPerformance(id: string, signal?: AbortSignal): Promise<MandatePerformance> {
  if (!uuid(id)) throw bad()
  let response: Response
  try { response = await fetch(`${apiBase}/api/mandates/${encodeURIComponent(id)}/performance`, { credentials: 'include', cache: 'no-store', signal }) }
  catch (error) { if (signal?.aborted) throw error; throw new MandateApiError('Performance is temporarily unreachable. Refresh when your connection is restored.', 0) }
  if (!response.ok) throw new MandateApiError(response.status === 401 ? 'Your session has expired. Sign in again.' : 'Performance is temporarily unavailable. Your trading state has not been changed.', response.status)
  let value: unknown; try { value = await response.json() } catch { throw bad() }
  return parsePerformance(value,id)
}
export function tone(value: Financial): 'positive' | 'negative' | 'neutral' | 'unavailable' {
  if (value === null || !decimalString(value)) return 'unavailable'
  if (!/[1-9]/.test(value)) return 'neutral'
  return value.startsWith('-') ? 'negative' : 'positive'
}
export function formatFinancial(value: Financial, digits = 2, signed = false): string {
  if (value === null || !decimalString(value)) return 'Not available'
  const negative = tone(value) === 'negative', nonzero = tone(value) !== 'neutral'
  const [integer,fraction = ''] = value.replace(/^-/, '').split('.')
  const approx = /[1-9]/.test(fraction.slice(digits))
  const whole = integer.replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${approx ? '≈ ' : ''}${negative ? '−' : signed && nonzero ? '+' : ''}${whole}${digits ? '.' + fraction.slice(0,digits).padEnd(digits,'0') : ''}`
}
// Diagram width only, never a financial metric. Integer string arithmetic avoids
// lossy Number conversion; backend amounts remain the sole monetary truth.
export function utilizationWidth(committed: Financial, allocation: Financial): string {
  if (committed === null || allocation === null || !decimalString(committed) || !decimalString(allocation) || tone(committed) === 'negative' || tone(allocation) !== 'positive') return '0'
  const [a,af=''] = allocation.split('.'), [c,cf=''] = committed.split('.'), scale = Math.max(af.length,cf.length)
  const denominator = BigInt(a + af.padEnd(scale,'0')), numerator = BigInt(c + cf.padEnd(scale,'0'))
  const width = numerator * 10000n / denominator, bounded = width > 10000n ? 10000n : width
  return `${bounded / 100n}.${(bounded % 100n).toString().padStart(2,'0')}`
}
export function valuationUsable(p: MandatePerformance, now = Date.now()): boolean {
  return p.financial_state === 'AVAILABLE' && (p.valuation.state === 'NOT_REQUIRED' || p.valuation.state === 'FRESH' && p.valuation.valid_until !== null && Date.parse(p.valuation.valid_until) > now)
}
export function operatingStatus(p: MandatePerformance): { title: string; copy: string } {
  const state = p.mandate_status === 'PAUSED' || p.mandate_status === 'RETIRED' || p.mandate_status === 'DRAFT' ? p.mandate_status : p.status
  const copy: Record<string,{title:string;copy:string}> = {
    DRAFT:{title:'Ready for your review',copy:'These preferences are saved as a draft. Activation does not place a trade.'},
    PAUSED:{title:'Mandate paused',copy:'Your mandate is paused. Existing position records, if any, remain visible below.'},
    RETIRED:{title:'Mandate retired',copy:'This mandate is read-only. Its recorded trading history remains available.'},
    NO_TRADES_YET:{title:'No trades recorded',copy:'No executed position lifecycle is attributed to this mandate. This is not a live strategy signal.'},
    POSITION_OPEN:{title:'Position open',copy:'A canonical Paper long position is attributed to this mandate. Reporting does not initiate an entry or exit.'},
    CLOSED:{title:'Lifecycle complete',copy:'The position has closed and its realized result has been recorded. Viewing this report does not re-enter.'},
    UNAVAILABLE:{title:'Reporting needs attention',copy:'The economic history could not be fully reconciled. Financial values are withheld.'},
  }
  return copy[state] ?? copy.UNAVAILABLE
}
