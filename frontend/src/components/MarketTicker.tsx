import { useEffect, useState } from 'react'
import { readTicker, type MarketTicker as Ticker } from '../markets/api'

// Format the decimal string directly: no binary-float conversion or lost precision.
function priceText(price: string): string {
  const [whole, fraction = ''] = price.split('.')
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  const trimmed = fraction.replace(/0+$/, '')
  return trimmed ? `${grouped}.${trimmed}` : grouped
}

export default function MarketTicker({ canonicalSymbol }: { canonicalSymbol: string }) {
  const [ticker, setTicker] = useState<Ticker | null>(null)
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setTicker(null)
    setError(false)
    readTicker(canonicalSymbol, controller.signal).then(data => {
      if (!controller.signal.aborted) setTicker(data)
    }).catch(() => {
      if (!controller.signal.aborted) setError(true)
    })
    return () => controller.abort()
  }, [canonicalSymbol, attempt])
  return <div className="market-ticker">
    <span className="market-price-label">Last reported price · Quidax</span>
    {error ? <p className="market-price-state" role="status">Price unavailable</p>
      : ticker === null ? <p className="market-price-state" role="status">Loading price…</p>
      : <>
        <div className="market-price"><strong>{priceText(ticker.last_price)}</strong><span>{ticker.quote_currency}</span></div>
        <div className="market-ticker-time">
          {ticker.source_timestamp && <span>Source snapshot <time dateTime={ticker.source_timestamp}>{new Date(ticker.source_timestamp).toLocaleString()}</time></span>}
          <span>Retrieved <time dateTime={ticker.retrieved_at}>{new Date(ticker.retrieved_at).toLocaleString()}</time></span>
        </div>
      </>}
    <button className="market-refresh" disabled={!error && ticker === null}
      aria-label={`${error ? 'Retry' : 'Refresh'} price for ${canonicalSymbol}`}
      onClick={() => setAttempt(value => value + 1)}>{error ? 'Retry price' : 'Refresh price'}</button>
  </div>
}
