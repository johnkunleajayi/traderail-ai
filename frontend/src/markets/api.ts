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
export async function readQuidaxAvailability(signal?: AbortSignal): Promise<Set<string>> {
  const response = await fetch(`${apiBase}/api/venues/QUIDAX/instruments`, {
    method: 'GET', credentials: 'omit', cache: 'no-store', signal,
  })
  if (!response.ok) throw new Error('Venue availability is temporarily unavailable.')
  const data: unknown = await response.json()
  if (!Array.isArray(data) || !data.every(item =>
    item && item.venue?.code === 'QUIDAX'
    && typeof item.venue.status === 'string'
    && typeof item.instrument?.canonical_symbol === 'string'
    && typeof item.venue_symbol === 'string' && item.venue_symbol.length > 0
    && typeof item.status === 'string'
  )) throw new Error('Venue availability could not be read.')
  return new Set(data.filter(item => item.status === 'active' && item.venue.status === 'active')
    .map(item => item.instrument.canonical_symbol))
}
