import { apiBase } from '../auth/api'

export interface InstrumentReference {
  canonical_symbol: string
  base_asset: { name: string }
  quote_asset: { name: string }
  market_type: string
  status: string
}
export async function readInstruments(signal?: AbortSignal): Promise<InstrumentReference[]> {
  const response = await fetch(`${apiBase}/api/instruments`, {
    method: 'GET', credentials: 'omit', cache: 'no-store', signal,
  })
  if (!response.ok) throw new Error('Markets could not be loaded. Please try again.')
  const data: unknown = await response.json()
  if (!Array.isArray(data) || !data.every(item =>
    item && typeof item.canonical_symbol === 'string'
    && typeof item.base_asset?.name === 'string'
    && typeof item.quote_asset?.name === 'string'
    && typeof item.market_type === 'string'
    && typeof item.status === 'string'
  )) throw new Error('Markets could not be read. Please try again.')
  return data
}