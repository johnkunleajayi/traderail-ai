import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

// Node's built-in runner; no DOM framework or extra test dependency.
const source = await readFile(new URL('../src/history/api.ts', import.meta.url), 'utf8')
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText
const isolated = output.replace(/import \{ apiBase \} from ['"]..\/auth\/api['"];?/, "const apiBase = 'http://127.0.0.1:8000';")
const api = await import(`data:text/javascript;base64,${Buffer.from(isolated).toString('base64')}`)
const originalFetch = globalThis.fetch
const candle = (symbol = 'BTC/USDT', timeframe = '1h') => ({
  canonical_symbol: symbol, provider: 'BINANCE', timeframe,
  open_time: '2026-10-15T00:00:00Z', close_time: '2026-10-15T00:59:59.999Z',
  open: '10.1234567890123456789', high: '12', low: '9', close: '11', volume: '1',
  retrieved_at: '2026-10-16T00:00:00Z',
})
const response = (data, status = 200) => new Response(JSON.stringify(data), { status })
test.afterEach(() => { globalThis.fetch = originalFetch })

for (const symbol of ['BTC/USDT', 'ETH/USDT']) {
  for (const timeframe of ['15m', '1h', '4h', '1d']) {
    test(`stored request ${symbol} ${timeframe} preserves Decimal text and provenance`, async () => {
      globalThis.fetch = async (url, options) => {
        const parsed = new URL(url)
        assert.equal(parsed.origin, 'http://127.0.0.1:8000')
        assert.equal(decodeURIComponent(parsed.pathname), `/api/market-data/stored-candles/${symbol}`)
        assert.equal(parsed.searchParams.get('timeframe'), timeframe)
        assert.equal(parsed.searchParams.get('provider'), 'BINANCE')
        assert.equal(parsed.searchParams.get('limit'), '100')
        assert.equal(options.credentials, 'omit')
        return response([candle(symbol, timeframe)])
      }
      const result = await api.readStoredCandles(symbol, timeframe)
      assert.equal(result[0].open, '10.1234567890123456789')
      assert.equal(result[0].provider, 'BINANCE')
    })
  }
}
test('empty stored response stays empty, with no demo fallback', async () => {
  globalThis.fetch = async () => response([])
  assert.deepEqual(await api.readStoredCandles('ETH/USDT', '1h'), [])
})
test('ingestion uses authenticated TradeRail POST and canonical identifier', async () => {
  globalThis.fetch = async (url, options) => {
    assert.match(url, /127\.0\.0\.1:8000\/api\/market-data\/candle-ingestions\/ETH%2FUSDT/)
    assert.equal(options.method, 'POST'); assert.equal(options.credentials, 'include')
    return response({canonical_symbol:'ETH/USDT', provider:'BINANCE', timeframe:'1h', fetched:100, inserted:0, unchanged:100})
  }
  assert.equal((await api.ingestHistory('ETH/USDT', '1h')).unchanged, 100)
})
test('conflict does not return success or replacement data', async () => {
  globalThis.fetch = async () => response({detail:{code:'candle_integrity_conflict'}}, 409)
  await assert.rejects(api.ingestHistory('BTC/USDT', '1h'), /not been overwritten/)
})
for (const status of [401, 403, 503]) test(`ingestion ${status} is a controlled error`, async () => {
  globalThis.fetch = async () => response({}, status)
  await assert.rejects(api.ingestHistory('BTC/USDT', '1h'))
})
for (const data of [[{...candle(),provider:'QUIDAX'}], [{...candle(),open:10.1}], [candle(),candle()], [{...candle(),canonical_symbol:'ETH/USDT'}]]) {
  test('malformed, duplicate or wrong-source candles are rejected', async () => {
    globalThis.fetch = async () => response(data)
    await assert.rejects(api.readStoredCandles('BTC/USDT', '1h'))
  })
}
test('quality reports API-provided status without inferring freshness', async () => {
  const data = {canonical_symbol:'BTC/USDT',provider:'BINANCE',timeframe:'1h',candle_count:97,
    duplicate_count:0,missing_interval_count:2,freshness:'stale',evaluated_at:'2026-10-16T00:00:00Z'}
  globalThis.fetch = async () => response(data)
  assert.deepEqual(await api.readQuality('BTC/USDT', '1h'), data)
})
test('failed quality request is not converted to Fresh or No gaps', async () => {
  globalThis.fetch = async () => response({}, 503)
  await assert.rejects(api.readQuality('BTC/USDT', '1h'))
})
