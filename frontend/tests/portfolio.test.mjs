import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

const source = readFileSync(new URL('../src/portfolio/api.ts', import.meta.url), 'utf8')
const output = ts.transpileModule(source.replace("import { apiBase } from '../auth/api'", "const apiBase = ''"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
const { readPortfolio, formatVirtualUsdt, SessionExpired } = await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`)
const originalFetch = globalThis.fetch
test.afterEach(() => { globalThis.fetch = originalFetch })
const fixture = () => ({ environment: 'PAPER', base_currency: 'USDT', starting_balance: '10000.00', cash_balance: '10000.00', status: 'active' })

test('accepts the current USDT response with authenticated no-store fetch', async () => {
  const portfolio = fixture()
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/portfolio')
    assert.equal(options.credentials, 'include')
    assert.equal(options.cache, 'no-store')
    return { ok: true, status: 200, json: async () => portfolio }
  }
  assert.deepEqual(await readPortfolio(), portfolio)
})
test('does not treat USD or LIVE as the USDT Paper contract', async () => {
  for (const change of [{ base_currency: 'USD' }, { environment: 'LIVE' }, { cash_balance: 'NaN' }]) {
    globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ ...fixture(), ...change }) })
    await assert.rejects(readPortfolio(), /response could not be read/)
  }
})
test('preserves session-expiry handling', async () => {
  globalThis.fetch = async () => ({ ok: false, status: 401 })
  await assert.rejects(readPortfolio(), SessionExpired)
})
test('formats exact backend strings with an explicit USDT unit, without float rounding', () => {
  assert.equal(formatVirtualUsdt('10000.00'), '10,000.00 USDT')
  assert.equal(formatVirtualUsdt('0.01'), '0.01 USDT')
  assert.equal(formatVirtualUsdt('999999999999999999999999.99'), '999,999,999,999,999,999,999,999.99 USDT')
  assert.doesNotMatch(source, /\b(?:Number|parseFloat|parseInt)\s*\(/)
})
test('summary formats both balances as USDT and labels the returned currency', () => {
  const component = readFileSync(new URL('../src/components/PaperPortfolioSummary.tsx', import.meta.url), 'utf8')
  assert.match(component, /formatVirtualUsdt\(portfolio.cash_balance\)/)
  assert.match(component, /formatVirtualUsdt\(portfolio.starting_balance\)/)
  assert.match(component, /Virtual \{portfolio.base_currency\}/)
  assert.doesNotMatch(component, /Virtual USD<|formatVirtualUsd\(/)
})
