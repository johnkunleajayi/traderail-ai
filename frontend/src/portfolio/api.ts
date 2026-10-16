import { apiBase } from '../auth/api'

export interface PaperPortfolio {
  environment: 'PAPER'
  base_currency: 'USD'
  starting_balance: string
  cash_balance: string
  status: 'active' | 'disabled'
}
export class SessionExpired extends Error {}
export async function readPortfolio(signal?: AbortSignal): Promise<PaperPortfolio> {
  const response = await fetch(`${apiBase}/api/portfolio`, { credentials: 'include', cache: 'no-store', signal })
  if (response.status === 401) throw new SessionExpired('Your session has expired.')
  if (!response.ok) throw new Error('Your Paper Portfolio could not be loaded. Please retry.')
  const portfolio: PaperPortfolio = await response.json()
  if (portfolio.environment !== 'PAPER' || portfolio.base_currency !== 'USD'
      || !/^\d+\.\d{2}$/.test(portfolio.cash_balance)
      || !/^\d+\.\d{2}$/.test(portfolio.starting_balance)) {
    throw new Error('The Paper Portfolio response could not be read. Please retry.')
  }
  return portfolio
}
export function formatVirtualUsd(value: string): string {
  // Format the API decimal string directly; never convert balances to float.
  const [dollars, cents] = value.split('.')
  return `$${dollars.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${cents}`
}