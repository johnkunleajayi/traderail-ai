import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { Database, RefreshCw } from 'lucide-react'
import { readInstruments } from '../markets/api'
import { historicalSymbols, historyLimit, ingestHistory, readQuality, readStoredCandles, timeframes, type DatasetQuality, type StoredCandle, type Timeframe } from '../history/api'
const HistoryChart = lazy(() => import('./HistoryChart'))
import { Card, SectionLabel } from './ui'
import { alignIntelligence, readIntelligence, type Intelligence } from '../history/intelligence'
import { alignRegimes, readRegimes, type RegimeDataset } from '../history/regimes'

function HistoryDataset({ symbol, timeframe, onBusy }: { symbol: string; timeframe: Timeframe; onBusy: (value: boolean) => void }) {
  const [candles, setCandles] = useState<StoredCandle[] | null>(null)
  const [intelligence, setIntelligence] = useState<Intelligence | null>(null)
  const [intelligenceError, setIntelligenceError] = useState('')
  const [regimes, setRegimes] = useState<RegimeDataset | null>(null)
  const [regimeError, setRegimeError] = useState('')
  const [quality, setQuality] = useState<DatasetQuality | null>(null)
  const [error, setError] = useState('')
  const [qualityError, setQualityError] = useState(false)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const controller = useRef<AbortController | null>(null)
  const refresh = useCallback(async (signal: AbortSignal) => {
    setCandles(null); setRegimes(null); setRegimeError(''); setQuality(null); setIntelligence(null); setIntelligenceError(''); setError(''); setQualityError(false)
    const results = await Promise.allSettled([readStoredCandles(symbol, timeframe, signal), readQuality(symbol, timeframe, signal), readIntelligence(symbol, timeframe, signal), readRegimes(symbol, timeframe, signal)])
    if (signal.aborted) return
    if (results[0].status === 'fulfilled') setCandles(results[0].value)
    else setError(results[0].reason instanceof Error ? results[0].reason.message : 'Stored history is unavailable.')
    if (results[1].status === 'fulfilled') setQuality(results[1].value)
    else setQualityError(true)
    if (results[0].status === 'fulfilled' && results[0].value.length > 0) {
      if (results[2].status === 'fulfilled') {
        try { alignIntelligence(results[0].value, results[2].value); setIntelligence(results[2].value) }
        catch (reason) { setIntelligenceError(reason instanceof Error ? reason.message : 'Intelligence alignment failed.') }
      } else setIntelligenceError(results[2].reason instanceof Error ? results[2].reason.message : 'Market Intelligence is unavailable.')
      if (results[3].status === 'fulfilled') {
        try { alignRegimes(results[0].value, results[3].value); setRegimes(results[3].value) }
        catch (reason) { setRegimeError(reason instanceof Error ? reason.message : 'Regime alignment failed.') }
      } else setRegimeError(results[3].reason instanceof Error ? results[3].reason.message : 'Market regimes unavailable.')
    }
  }, [symbol, timeframe])
  useEffect(() => {
    const request = new AbortController(); controller.current = request
    void refresh(request.signal)
    return () => { controller.current?.abort(); onBusy(false) }
  }, [refresh, onBusy])
  async function loadHistory() {
    controller.current?.abort()
    const request = new AbortController(); controller.current = request
    setBusy(true); onBusy(true); setNotice(''); setError('')
    try {
      const result = await ingestHistory(symbol, timeframe, request.signal)
      if (!request.signal.aborted) {
        await refresh(request.signal)
        if (!request.signal.aborted) setNotice(`${result.inserted} added · ${result.unchanged} unchanged. Ingestion completed.`)
      }
    } catch (reason) {
      if (!request.signal.aborted) setError(reason instanceof Error ? reason.message : 'History could not be loaded.')
    } finally { if (!request.signal.aborted) { setBusy(false); onBusy(false) } }
  }
  function reloadStored() { controller.current?.abort(); const request = new AbortController(); controller.current = request; void refresh(request.signal) }
  return <>
    <div className="history-dataset-bar"><div className="history-quality" aria-label="Stored dataset quality">
      {quality ? <><span className={`history-freshness history-${quality.freshness}`}>{quality.freshness === 'empty' ? 'Not loaded' : quality.freshness === 'fresh' ? 'Fresh' : 'Stale'}</span><span>{quality.candle_count} stored candles</span><span>{quality.missing_interval_count === 0 ? 'No gaps' : `${quality.missing_interval_count} missing intervals`}</span>{quality.duplicate_count > 0 && <span>{quality.duplicate_count} duplicates reported</span>}</>
        : <span>{qualityError ? 'Quality unavailable' : 'Checking dataset quality…'}</span>}
    </div><button className="button button-secondary history-load" onClick={loadHistory} disabled={busy}><RefreshCw size={14} aria-hidden="true"/>{busy ? 'Loading historical data…' : candles?.length ? 'Refresh history' : 'Load historical data'}</button></div>
    {notice && <p className="history-notice" role="status">{notice}</p>}
    {error && <div className="history-error" role="status"><p>{error}</p><button className="market-refresh" disabled={busy} onClick={reloadStored}>Reload stored data</button></div>}
    {qualityError && <p className="history-caption" role="status">Quality metadata could not be loaded. Freshness and gap status are not assumed.</p>}
    {!error && candles === null ? <div className="history-state" role="status">Loading stored candles for {symbol} · {timeframe}…</div>
      : candles?.length === 0 ? <div className="history-state history-empty"><Database size={25} aria-hidden="true"/><h3>No stored history yet</h3><p>Historical candles have not been loaded for {symbol} · {timeframe}. Load up to {historyLimit} completed candles through TradeRail.</p></div>
      : candles && candles.length > 0 ? <><Suspense fallback={<div className="history-state" role="status">Preparing candlestick chart…</div>}><HistoryChart candles={candles} intelligence={intelligence} intelligenceError={intelligenceError} regimes={regimes} regimeError={regimeError}/></Suspense><p className="history-caption">Showing {candles.length} stored candles · completed intervals only{quality ? ` · Quality evaluated ${new Date(quality.evaluated_at).toLocaleString()}` : ''}</p></> : null}
  </>
}

export default function MarketHistory() {
  const [symbols, setSymbols] = useState<string[] | null>(null)
  const [symbol, setSymbol] = useState<string>('BTC/USDT')
  const [timeframe, setTimeframe] = useState<Timeframe>('1h')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const request = new AbortController(); setError(false)
    readInstruments(request.signal).then(data => {
      if (request.signal.aborted) return
      const supported = data.filter(item => historicalSymbols.some(supported => supported === item.canonical_symbol)).map(item => item.canonical_symbol)
      setSymbols(supported)
      if (supported.length) setSymbol(current => supported.includes(current) ? current : supported[0])
    }).catch(() => { if (!request.signal.aborted) setError(true) })
    return () => request.abort()
  }, [attempt])
  return <Card className="market-history" aria-labelledby="history-heading">
    <div className="history-heading"><div><SectionLabel>STORED MARKET DATA</SectionLabel><h2 id="history-heading">Market History</h2><p>Historical source: <strong>Binance</strong><span className="history-source-separator">·</span>Current market price: <strong>Quidax</strong></p></div><span className="history-research-label">Historical research</span></div>
    {error ? <div className="history-error" role="status">Historical markets could not be loaded.<button className="market-refresh" onClick={() => setAttempt(value => value + 1)}>Retry</button></div>
      : symbols === null ? <p className="history-state" role="status">Loading historical markets…</p>
      : symbols.length === 0 ? <p className="history-state">No supported historical instruments are available.</p>
      : <><div className="history-controls"><label>Canonical instrument<select value={symbol} disabled={busy} onChange={event => setSymbol(event.target.value)}>{symbols.map(value => <option key={value}>{value}</option>)}</select></label><fieldset disabled={busy}><legend>Timeframe</legend><div className="history-timeframes">{timeframes.map(value => <button type="button" key={value} aria-pressed={timeframe === value} onClick={() => setTimeframe(value)}>{value}</button>)}</div></fieldset><p className="history-selection">{symbol} <span> / </span> {timeframe} <span> · BINANCE</span></p></div><HistoryDataset key={`${symbol}:${timeframe}`} symbol={symbol} timeframe={timeframe} onBusy={setBusy}/></>}
  </Card>
}
