import type { IntentForm, MandateIntent, MandateStatus, RiskLevel, TradingStyle } from './types'
export const riskOptions: { value: RiskLevel; label: string; copy: string }[] = [
  { value: 'CONSERVATIVE', label: 'Conservative', copy: 'Prioritizes tighter exposure and capital preservation.' },
  { value: 'BALANCED', label: 'Balanced', copy: 'Balances controlled exposure with trading opportunities.' },
  { value: 'GROWTH', label: 'Growth', copy: 'Requests greater permitted exposure, always within hard Risk controls.' },
]
export const styleOptions: { value: TradingStyle; label: string; copy: string }[] = [
  { value: 'SHORT_TERM', label: 'Short-term', copy: 'A preference for shorter trading horizons.' },
  { value: 'MEDIUM_TERM', label: 'Medium-term', copy: 'A preference for a more measured holding horizon.' },
  { value: 'LONGER_TERM', label: 'Longer-term', copy: 'A preference for a longer view of market opportunities.' },
]
export const steps = ['Capital', 'Market', 'Risk', 'Trading style', 'Review', 'Activate']
export const newForm: IntentForm = { capital: '', risk: 'BALANCED', style: 'MEDIUM_TERM' }
export const positiveDecimal = (value: unknown): value is string => typeof value === 'string' && value.length <= 4096 && /^(?:\d+(?:\.\d+)?|\.\d+)$/.test(value) && /[1-9]/.test(value)
export function buildIntent(form: IntentForm): MandateIntent {
  const capital = form.capital.trim()
  if (!positiveDecimal(capital)) throw new Error('Enter a positive USDT amount using digits and an optional decimal point.')
  if (!riskOptions.some(option => option.value === form.risk) || !styleOptions.some(option => option.value === form.style)) throw new Error('Choose a supported risk preference and trading style.')
  return { environment: 'PAPER', capital_allocation: capital, allocation_currency: 'USDT', instrument: 'BTC/USDT', risk_level: form.risk, trading_style: form.style, direction_capability: 'LONG_ONLY' }
}
export function formatAllocation(value: string): string {
  // Display only: preserve every backend decimal digit without Number/parseFloat.
  const [whole, fraction] = value.split('.')
  return `${(whole || '0').replace(/\B(?=(\d{3})+(?!\d))/g, ',')}${fraction === undefined ? '' : '.' + fraction} USDT`
}
export const riskLabel = (value: RiskLevel) => riskOptions.find(option => option.value === value)?.label ?? value
export const styleLabel = (value: TradingStyle) => styleOptions.find(option => option.value === value)?.label ?? value
export const statusLabels: Record<MandateStatus, string> = { DRAFT: 'Draft', ACTIVE: 'Active', PAUSED: 'Paused', RETIRED: 'Retired' }
