import type { ExperimentMetadata } from './types'

export const actions = ['ENTER_LONG', 'EXIT_LONG', 'ENTER_SHORT', 'EXIT_SHORT', 'NO_ACTION'] as const
export type Action = typeof actions[number]
type State = 'CASH' | 'LONG' | 'SHORT'
const regimes = ['BULL_TREND','BEAR_TREND','RANGE','BREAKOUT','HIGH_VOLATILITY','UNSAFE','UNKNOWN'] as const
export interface DecisionRow { open_time: string; timestamp: string; decision_identity: string; decision: Action; reason_codes: string[]; position_before: State; position_after: State }
export interface RegimeRow { open_time: string; timestamp: string; regime: string; candidate_regime: string; confirmation_count: number; confirmation_required: number; retained_previous: boolean; previous_regime: string | null; reason_codes: string[] }
export interface EquityRow { timestamp: string; cash: string; realized_capital: string; realized_pnl: string; unrealized_pnl: string; total_equity: string; drawdown: string | null; position_state: State }
export interface TradeRow { trade_identity: string; direction: 'LONG' | 'SHORT'; entry_decision_time: string; entry_fill_time: string; entry_reference_price: string; entry_fill_price: string; exit_decision_time: string; exit_fill_time: string; exit_reference_price: string; exit_fill_price: string; quantity: string; gross_pnl: string; fees: string; spread_cost: string; slippage_cost: string; net_pnl: string; entry_reason_codes: string[]; exit_reason_codes: string[] }
export interface OpenResearchPosition { direction: 'LONG' | 'SHORT'; quantity: string; entry_decision_time: string; entry_fill_time: string; entry_reference_price: string; entry_price: string; entry_fee_recognized: string; mark_price: string; mark_timestamp: string; unrealized_pnl: string; reserved_capital: string; restricted_short_proceeds: string; entry_reason_codes: string[] }
export interface UnfilledRow { decision_identity: string; decision: Action; decision_time: string; eligible_fill_time: string; filled_state: State; reason_code: string }
export interface ResearchData { trades: TradeRow[]; decisions: DecisionRow[]; regimes: RegimeRow[]; equity_curve: EquityRow[]; open_position: OpenResearchPosition | null; unfilled_decisions: UnfilledRow[] }
export const unavailableResearch = 'Experiment detail data could not be aligned or read. Summary values remain available; no replacement observations are shown.'
export function fail(): never { throw new Error(unavailableResearch) }
export function record(v: unknown): Record<string, unknown> { if (!v || typeof v !== 'object' || Array.isArray(v)) fail(); return v as Record<string, unknown> }
export function decimal(v: unknown): v is string { return typeof v === 'string' && v.length <= 4096 && /^-?\d+(?:\.\d+)?$/.test(v) }
export function timeKey(v: unknown): string {
  if (typeof v !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(v)) fail()
  const date = new Date(v)
  if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0,19) !== v.slice(0,19)) fail()
  return v.slice(0,19) + '.' + (v.slice(19).match(/^\.(\d+)/)?.[1] ?? '').padEnd(6,'0') + 'Z'
}
const text = (v: unknown) => typeof v === 'string' && v.length > 0
const state = (v: unknown) => ['CASH','LONG','SHORT'].includes(v as string)
const reasons = (v: unknown) => Array.isArray(v) && v.every(text)
function fields(row: Record<string, unknown>, dates: string[], money: string[], codes: string[] = []) {
  dates.forEach(k => timeKey(row[k])); if (!money.every(k => decimal(row[k])) || !codes.every(k => reasons(row[k]))) fail()
}
function rows(data: Record<string, unknown>, key: string) {
  const value = data[key]; if (!Array.isArray(value) || value.length > 300) fail()
  return value.map(record)
}
export function parseResearch(value: unknown, meta: ExperimentMetadata, tradeCount: number): ResearchData {
  const data = record(value), decisions = rows(data,'decisions'), equity = rows(data,'equity_curve'), regime = rows(data,'regimes'), trades = rows(data,'trades'), unfilled = rows(data,'unfilled_decisions')
  const start = timeKey(meta.evaluation_start), end = timeKey(meta.evaluation_end)
  if (decisions.length !== meta.evaluation_candle_count || equity.length !== decisions.length || regime.length !== decisions.length || trades.length !== tradeCount || !decisions.length) fail()
  const ids = new Set(), tradeIds = new Set()
  decisions.forEach((r,i) => {
    fields(r,['open_time','timestamp'],[],['reason_codes'])
    if (!actions.includes(r.decision as Action) || !text(r.decision_identity) || ids.has(r.decision_identity) || !state(r.position_before) || !state(r.position_after)
      || timeKey(r.open_time) < start || timeKey(r.timestamp) >= end || timeKey(r.open_time) >= timeKey(r.timestamp)
      || (i > 0 && timeKey(r.open_time) <= timeKey(decisions[i-1].open_time))) fail()
    ids.add(r.decision_identity)
    const e = equity[i], g = regime[i]
    fields(e,['timestamp'],['cash','realized_capital','realized_pnl','unrealized_pnl','total_equity'])
    fields(g,['open_time','timestamp'],[],['reason_codes'])
    if (timeKey(e.timestamp) !== timeKey(r.timestamp) || timeKey(g.open_time) !== timeKey(r.open_time) || timeKey(g.timestamp) !== timeKey(r.timestamp)
      || !state(e.position_state) || !(e.drawdown === null || decimal(e.drawdown) && !e.drawdown.startsWith('-'))
      || !regimes.includes(g.regime as typeof regimes[number]) || !regimes.includes(g.candidate_regime as typeof regimes[number])
      || !(g.previous_regime === null || regimes.includes(g.previous_regime as typeof regimes[number]))
      || !Number.isSafeInteger(g.confirmation_count) || (g.confirmation_count as number) < 0 || !Number.isSafeInteger(g.confirmation_required)
      || (g.confirmation_required as number) < 0 || (g.confirmation_count as number) > meta.candle_count || typeof g.retained_previous !== 'boolean') fail()
  })
  trades.forEach(r => {
    fields(r,['entry_decision_time','entry_fill_time','exit_decision_time','exit_fill_time'],['entry_reference_price','entry_fill_price','exit_reference_price','exit_fill_price','quantity','gross_pnl','fees','spread_cost','slippage_cost','net_pnl'],['entry_reason_codes','exit_reason_codes'])
    if (!['LONG','SHORT'].includes(r.direction as string) || !text(r.trade_identity) || tradeIds.has(r.trade_identity) || timeKey(r.entry_fill_time) >= timeKey(r.exit_fill_time)) fail()
    tradeIds.add(r.trade_identity)
  })
  let open: Record<string, unknown> | null = null
  if (data.open_position !== null) {
    open = record(data.open_position)
    fields(open,['entry_decision_time','entry_fill_time','mark_timestamp'],['quantity','entry_reference_price','entry_price','entry_fee_recognized','mark_price','unrealized_pnl','reserved_capital','restricted_short_proceeds'],['entry_reason_codes'])
    if (!['LONG','SHORT'].includes(open.direction as string) || timeKey(open.mark_timestamp) !== timeKey(equity[equity.length-1].timestamp)) fail()
  }
  unfilled.forEach(r => {
    fields(r,['decision_time','eligible_fill_time'],[])
    if (!ids.has(r.decision_identity) || !actions.includes(r.decision as Action) || r.decision === 'NO_ACTION' || !state(r.filled_state) || !text(r.reason_code)
      || timeKey(r.eligible_fill_time) < end || timeKey(r.eligible_fill_time) <= timeKey(r.decision_time)) fail()
  })
  const parsed = { trades, decisions, regimes:regime, equity_curve:equity, open_position:open, unfilled_decisions:unfilled } as unknown as ResearchData
  researchEvents(parsed) // Validate every action against supplied fill/unfilled provenance, never infer execution.
  return parsed
}
export interface ResearchEvent { id: string; kind: 'decision' | 'fill'; action: Exclude<Action,'NO_ACTION'>; candle_open_time: string; timestamp: string; price: string | null; status: 'Executed' | 'Unfilled' | 'Open position'; reason_codes: string[] }
export function researchEvents(data: ResearchData): ResearchEvent[] {
  const fills = data.trades.flatMap(t => [
    { action:`ENTER_${t.direction}`, decision:t.entry_decision_time, time:t.entry_fill_time, price:t.entry_fill_price, open:false },
    { action:`EXIT_${t.direction}`, decision:t.exit_decision_time, time:t.exit_fill_time, price:t.exit_fill_price, open:false }])
  if (data.open_position) { const p = data.open_position; fills.push({action:`ENTER_${p.direction}`,decision:p.entry_decision_time,time:p.entry_fill_time,price:p.entry_price,open:true}) }
  const consumed = new Set(), usedUnfilled = new Set(), result: ResearchEvent[] = []
  for (const d of data.decisions) {
    if (d.decision === 'NO_ACTION') continue
    const matching = fills.filter(f => f.action === d.decision && timeKey(f.decision) === timeKey(d.timestamp))
    const missing = data.unfilled_decisions.filter(u => u.decision_identity === d.decision_identity && u.decision === d.decision && timeKey(u.decision_time) === timeKey(d.timestamp))
    if (matching.length + missing.length !== 1) fail()
    const fill = matching[0]
    const status = !fill ? 'Unfilled' : fill.open ? 'Open position' : 'Executed'
    result.push({ id:d.decision_identity,kind:'decision',action:d.decision,candle_open_time:d.open_time,timestamp:d.timestamp,price:null,status,reason_codes:d.reason_codes })
    if (fill) {
      if (consumed.has(fill) || timeKey(fill.time) <= timeKey(d.timestamp)) fail()
      const candle = data.decisions.find(row => timeKey(row.open_time) === timeKey(fill.time))
      if (!candle) fail()
      consumed.add(fill)
      result.push({id:d.decision_identity+':fill',kind:'fill',action:d.decision,candle_open_time:candle.open_time,timestamp:fill.time,price:fill.price,status,reason_codes:d.reason_codes})
    } else usedUnfilled.add(missing[0])
  }
  if (consumed.size !== fills.length || usedUnfilled.size !== data.unfilled_decisions.length) fail()
  return result
}
