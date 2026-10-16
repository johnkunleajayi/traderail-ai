import { useEffect, useState } from 'react'
import { readInstruments, readQuidaxAvailability, type InstrumentReference } from '../markets/api'
import { Card, SectionLabel } from './ui'
import MarketTicker from './MarketTicker'

export default function MarketsSection() {
  const [instruments, setInstruments] = useState<InstrumentReference[] | null>(null)
  const [error, setError] = useState('')
  const [availability, setAvailability] = useState<Set<string> | null>(null)
  const [availabilityError, setAvailabilityError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setError('')
    setInstruments(null)
    setAvailability(null)
    setAvailabilityError('')
    readInstruments(controller.signal).then(data => {
      if (!controller.signal.aborted) setInstruments(data)
    }).catch(reason => {
      if (!controller.signal.aborted) setError(
        reason instanceof Error ? reason.message : 'Markets are temporarily unavailable.'
      )
    })
    readQuidaxAvailability(controller.signal).then(data => {
      if (!controller.signal.aborted) setAvailability(data)
    }).catch(() => {
      if (!controller.signal.aborted) setAvailabilityError('Venue availability is temporarily unavailable.')
    })
    return () => controller.abort()
  }, [attempt])
  return <Card className="markets-section" aria-labelledby="markets-heading">
    <div className="markets-heading"><div><SectionLabel>CANONICAL INSTRUMENTS</SectionLabel><h2 id="markets-heading">Markets</h2></div><span className="markets-reference-label">Public market data</span></div>
    {availabilityError && !error && <div className="markets-message" role="status"><p>{availabilityError}</p><button className="button button-secondary" onClick={() => setAttempt(value => value + 1)}>Retry</button></div>}
    {error ? <div className="markets-message" role="status"><p>{error}</p><button className="button button-secondary" onClick={() => setAttempt(value => value + 1)}>Retry</button></div>
      : instruments === null ? <p className="markets-message" role="status">Loading instruments…</p>
      : instruments.length === 0 ? <p className="markets-message" role="status">No instruments are available yet.</p>
      : <ul className="markets-list">{instruments.map(instrument =>
        <li className="market-reference" key={instrument.canonical_symbol}>
          <div className="market-reference-title"><h3>{instrument.canonical_symbol}</h3><span className="market-type">{instrument.market_type}</span></div>
          <p>{instrument.base_asset.name} · {instrument.quote_asset.name}</p>
          {availability?.has(instrument.canonical_symbol) && <MarketTicker canonicalSymbol={instrument.canonical_symbol}/>}
          <div className="market-reference-footer">
            <span className="market-status">{instrument.status}</span>
            <span className={`market-venue${availability?.has(instrument.canonical_symbol) ? ' market-venue-available' : ''}`}>
              {availabilityError ? 'Availability unavailable'
                : availability === null ? 'Checking venue…'
                : availability.has(instrument.canonical_symbol) ? 'Quidax available' : 'No venue mapping'}
            </span>
          </div>
        </li>
      )}</ul>}
    <p className="markets-coming-next">Public ticker snapshots from Quidax. Refresh to request an updated price.</p>
  </Card>
}