import { useEffect, useState } from 'react'
import { readInstruments, type InstrumentReference } from '../markets/api'
import { Card, SectionLabel } from './ui'

export default function MarketsSection() {
  const [instruments, setInstruments] = useState<InstrumentReference[] | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setError('')
    setInstruments(null)
    readInstruments(controller.signal).then(data => {
      if (!controller.signal.aborted) setInstruments(data)
    }).catch(reason => {
      if (!controller.signal.aborted) setError(
        reason instanceof Error ? reason.message : 'Markets are temporarily unavailable.'
      )
    })
    return () => controller.abort()
  }, [attempt])
  return <Card className="markets-section" aria-labelledby="markets-heading">
    <div className="markets-heading"><div><SectionLabel>CANONICAL INSTRUMENTS</SectionLabel><h2 id="markets-heading">Markets</h2></div><span className="markets-reference-label">Reference only</span></div>
    {error ? <div className="markets-message" role="status"><p>{error}</p><button className="button button-secondary" onClick={() => setAttempt(value => value + 1)}>Retry</button></div>
      : instruments === null ? <p className="markets-message" role="status">Loading instruments…</p>
      : instruments.length === 0 ? <p className="markets-message" role="status">No instruments are available yet.</p>
      : <ul className="markets-list">{instruments.map(instrument =>
        <li className="market-reference" key={instrument.canonical_symbol}>
          <div className="market-reference-title"><h3>{instrument.canonical_symbol}</h3><span className="market-type">{instrument.market_type}</span></div>
          <p>{instrument.base_asset.name} · {instrument.quote_asset.name}</p>
          <span className="market-status">{instrument.status}</span>
        </li>
      )}</ul>}
    <p className="markets-coming-next">Live market data is coming in the next stage.</p>
  </Card>
}