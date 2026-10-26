import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

// Same built-in Node runner as the rest of the app. Real React SSR, no added DOM framework.
const frontend = fileURLToPath(new URL('..', import.meta.url))
const require = createRequire(new URL('../package.json', import.meta.url))
const cache = new Map()
function load(relative) {
  const path = resolve(frontend, relative)
  if (cache.has(path)) return cache.get(path)
  if (path.endsWith('/auth/api.ts') || path.endsWith('\\auth\\api.ts')) return { apiBase: 'http://127.0.0.1:8000' }
  const output = ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
    esModuleInterop: true,
  }}).outputText
  const module = { exports: {} }; cache.set(path, module.exports)
  const localRequire = specifier => {
    if (!specifier.startsWith('.')) return require(specifier)
    const base = resolve(dirname(path), specifier)
    const target = ['.ts', '.tsx'].map(ext => base + ext).find(existsSync)
    if (!target) throw new Error('Missing local test module: ' + specifier)
    return load(target)
  }
  new Function('require', 'module', 'exports', output)(localRequire, module, module.exports)
  cache.set(path, module.exports)
  return module.exports
}
const { defaultForm, buildRequest, storedBoundaries } = load('src/strategy-lab/model.ts')
const { parseExperiment, runExperiment, ExperimentApiError } = load('src/strategy-lab/api.ts')
const Form = load('src/strategy-lab/ExperimentForm.tsx').default
const Results = load('src/strategy-lab/ResultsSummary.tsx').default
const Details = load('src/strategy-lab/TechnicalDetails.tsx').default
const Lab = load('src/strategy-lab/StrategyLab.tsx').default
const values = { ...defaultForm, evaluation_start: '2026-10-10T04:00:00', evaluation_end: '2026-10-12T04:00:00' }
const request = () => buildRequest(values)
function fixture() {
  const req = request()
  return { experiment: { ...req, experiment_identity: 'experiment-test', dataset_identity: 'dataset-test',
    research_result_identity: 'result-test', strategy_configuration_identity: 'strategy-test',
    candle_count: 100, warmup_start: '2026-10-08T00:00:00Z', warmup_candle_count: 52,
    required_warmup_candles: 52, evaluation_candle_count: 48,
    execution: { ...req.execution, timing: 'NEXT_OPEN', cost_model_identity: 'cost-test', execution_configuration_identity: 'execution-test' } },
    summary: { initial_capital: '10000', ending_equity: '10012.1234567890123456789', total_return: '0.00121234567890123456789',
      gross_return: '0.002', net_return: '0.001', max_drawdown: '0.004', completed_trade_count: 2,
      win_rate: '0.5', profit_factor: '1.234567890123456789', expectancy: '6.06172839450617283945', exposure: '50',
      turnover: '0.25', fee_total: '1.234567890123456789', spread_cost_total: '0', slippage_cost_total: '0' },
    open_position: null, unfilled_decisions: [] }
}
const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props))
const originalFetch = globalThis.fetch
test.afterEach(() => { globalThis.fetch = originalFetch })

test('form defaults are fixed research assumptions, never claimed venue fees', () => {
  assert.equal(defaultForm.provider, 'BINANCE'); assert.equal(defaultForm.requested_candle_count, '100')
  assert.equal(defaultForm.initial_capital, '10000'); assert.equal(defaultForm.quantity, '0.001')
  for (const key of ['fee_rate', 'full_spread_rate', 'slippage_rate', 'delay_candles']) assert.equal(defaultForm[key], '0')
  assert.equal(defaultForm.evaluation_start, ''); assert.equal(defaultForm.evaluation_end, '')
  const html = render(Form, { values, onChange() {}, onRun() {}, busy: false, suggesting: false, historyMessage: '', onSuggest() {} })
  assert.match(html, /Fixed configuration/); assert.match(html, /not venue fee estimates/)
  for (const label of ['Canonical instrument','Historical provider','Timeframe','Initial research capital','Fixed quantity','Trading fee rate','Full spread rate','Slippage rate','Additional delay','Evaluation start','Evaluation end']) assert.ok(html.includes(label))
  for (const id of ['lab-instrument','lab-provider','lab-timeframe','lab-evaluation_start','lab-evaluation_end']) {
    assert.ok(html.includes(`for="${id}"`)); assert.ok(html.includes(`id="${id}"`))
  }
})
test('request preserves financial strings, explicit UTC and fixed strategy', () => {
  const form = { ...values, quantity: '0.000000000000000000000001', initial_capital: '999999999999999999999999.123456789012345678901234' }
  const r = buildRequest(form)
  assert.equal(r.quantity, form.quantity); assert.equal(r.initial_capital, form.initial_capital)
  assert.equal(r.evaluation_start, values.evaluation_start + '.000Z')
  assert.equal(r.strategy_family, 'ADAPTIVE_TREND'); assert.equal(r.strategy_version, '1.0.0')
  assert.deepEqual(r.execution, { fee_rate:'0', full_spread_rate:'0', slippage_rate:'0', delay_candles:0 })
})
for (const [key, value] of [['quantity','0'], ['quantity','-1'], ['initial_capital','1e5'], ['fee_rate','1.0001'], ['slippage_rate','NaN'], ['full_spread_rate','-0.001'], ['requested_candle_count','52'], ['requested_candle_count','301'], ['delay_candles','0.5'], ['delay_candles','-1'], ['evaluation_start','2026-02-30T00:00'], ['evaluation_start','2026-10-13T00:00'], ['evaluation_end',''], ['provider','QUIDAX']]) {
  test(`invalid form ${key}=${value} is rejected before request`, () => assert.throws(() => buildRequest({ ...values, [key]:value })))
}
test('real stored timestamp suggestions explicitly exclude 52 warm-up candles', () => {
  const candles = Array.from({ length:100 }, (_,i) => ({ open_time:new Date(Date.UTC(2026,9,1) + i*3600000).toISOString() }))
  const bounds = storedBoundaries(candles,'1h')
  assert.equal(bounds.requested_candle_count,'100')
  assert.equal(bounds.evaluation_start,'2026-10-03T04:00:00')
  assert.equal(bounds.evaluation_end,'2026-10-05T04:00:00')
  assert.throws(() => storedBoundaries([], '1h'), /53 stored candles/)
  assert.throws(() => storedBoundaries(candles.slice(0,52), '1h'))
})
test('successful request uses TradeRail only and authenticated cookie POST', async () => {
  let calls = 0
  globalThis.fetch = async (url, options) => {
    calls++; assert.equal(url,'http://127.0.0.1:8000/api/strategy-lab/experiments')
    assert.equal(options.method,'POST'); assert.equal(options.credentials,'include'); assert.equal(options.cache,'no-store')
    assert.deepEqual(JSON.parse(options.body),request())
    return new Response(JSON.stringify(fixture()))
  }
  const result = await runExperiment(request())
  assert.equal(calls,1); assert.equal(result.summary.ending_equity,'10012.1234567890123456789')
  assert.equal(result.experiment.dataset_identity,'dataset-test')
})
test('rendered summary respects fractional returns versus already-percentage exposure', () => {
  const result = parseExperiment(fixture(),request()), html = render(Results,{ result })
  assert.match(html,/10,012.12/); assert.match(html,/0.1212%/); assert.match(html,/50%/)
  assert.doesNotMatch(html,/5,000%/)
  assert.match(html,/title="10012.1234567890123456789"/)
  const exact = render(Details,{ result })
  assert.match(exact,/10012.1234567890123456789/); assert.match(exact,/6.06172839450617283945/)
  assert.match(exact,/<details/); assert.match(exact,/dataset-test/)
})
test('undefined metrics remain unavailable and actual zero remains zero', () => {
  const f = fixture(); f.summary.win_rate = null; f.summary.profit_factor = null; f.summary.net_return = null
  const html = render(Results,{ result:parseExperiment(f,request()) })
  assert.equal((html.match(/Unavailable/g) || []).length,3)
  assert.ok(html.includes('Spread cost')); assert.ok(html.includes('>0</dd>'))
})
test('open position and unfilled decisions are explicit without invented exit/trade table', () => {
  const f = fixture(); f.open_position = { direction:'LONG', quantity:'0.001' }; f.unfilled_decisions = [{ decision:'ENTER_LONG' }]
  const html = render(Results,{ result:parseExperiment(f,request()) })
  assert.match(html,/open LONG research position/); assert.match(html,/No exit was fabricated/); assert.match(html,/1 decision\(s\) remain unfilled/)
  assert.doesNotMatch(html,/<table|<canvas/)
})
test('empty state is real and has no fake result metrics', () => {
  const html = render(Lab,{ onSessionExpired() {} })
  assert.match(html,/No experiment run yet/); assert.match(html,/historical stored market data/)
  assert.doesNotMatch(html,/Ending equity|10,000|Profit factor|<canvas|<table/)
})
test('loading form is disabled and announces busy state', () => {
  const html = render(Form,{ values,onChange(){},onRun(){},busy:true,suggesting:false,historyMessage:'',onSuggest(){} })
  assert.match(html,/aria-busy="true"/); assert.match(html,/<fieldset disabled/); assert.match(html,/Running historical experiment/)
  const source=readFileSync(resolve(frontend,'src/strategy-lab/StrategyLab.tsx'),'utf8')
  assert.match(source,/role="status">Running the deterministic research pipeline/)
  assert.match(source,/if \(request.current \|\| suggesting\) return/)
})
for (const [status,code,message] of [[401,'','session has expired'],[403,'','origin'],[404,'','instrument'],[422,'','UTC boundaries'],[409,'insufficient_stored_history','exact requested'],[409,'insufficient_warmup','52 requested'],[409,'research_execution_failed','accounting'],[409,'invalid_candle_dataset','dataset quality'],[500,'','temporarily unavailable']]) {
  test(`API error ${status}/${code} stays controlled without fallback`,async () => {
    globalThis.fetch=async()=>new Response(JSON.stringify({detail:{code,message:'PRIVATE STACK TRACE'}}),{status})
    await assert.rejects(runExperiment(request()), error => error instanceof ExperimentApiError && error.status===status && error.message.includes(message) && !error.message.includes('PRIVATE'))
  })
}
test('network failure never returns fake success',async()=>{
  globalThis.fetch=async()=>{throw new Error('private connection details')}
  await assert.rejects(runExperiment(request()),error=>error.status===0 && !error.message.includes('private'))
})
for (const mutate of [f=>{f.summary.total_return=0.1},f=>{f.experiment.canonical_symbol='ETH/USDT'},f=>{f.experiment.provider='QUIDAX'},f=>{f.experiment.execution.fee_rate='0.9'},f=>{f.experiment.quantity='2'},f=>{f.experiment.evaluation_end='2026-10-13T00:00:00Z'},f=>{delete f.summary.expectancy},f=>{f.experiment.warmup_candle_count=1}]) {
  test('malformed or mismatched success cannot become research results',()=>{
    const f=fixture();mutate(f);assert.throws(()=>parseExperiment(f,request()),/could not be read/)
  })
}
test('invalid JSON success is not substituted',async()=>{
  globalThis.fetch=async()=>new Response('not JSON')
  await assert.rejects(runExperiment(request()),/could not be read/)
})
test('navigation is authenticated, lazy, preserves workspace and resets research on logout',()=>{
  const source=readFileSync(resolve(frontend,'src/components/AuthExperience.tsx'),'utf8')
  assert.match(source,/Workspace navigation/); assert.match(source,/<span>Strategy Lab<\/span>/)
  assert.ok(source.indexOf('if (!user) return') < source.indexOf('Workspace navigation'))
  assert.match(source,/lazy\(\(\) => import\('\.\.\/strategy-lab\/StrategyLab'\)\)/)
  assert.match(source,/hidden=\{view !== 'workspace'\}/); assert.match(source,/labOpened &&/)
  assert.match(source,/setLabOpened\(false\)/); assert.match(source,/await logout\(\); clearUser\(\)/)
})
test('responsive layout stays shrinkable and uses existing tokens in both themes',()=>{
  const css=readFileSync(resolve(frontend,'src/styles.css'),'utf8').split('/* Strategy Lab:')[1]
  assert.match(css,/minmax\(0,1fr\)/);assert.match(css,/@media\(max-width:540px\)/)
  assert.match(css,/min-width:0/);assert.match(css,/overflow-wrap:anywhere/)
  assert.match(css,/\.lab-setup,\.lab-result-card \{ display:block/);assert.match(css,/\.strategy-lab \.card:hover \{ transform:none/)
  assert.match(css,/var\(--surface\)|var\(--bg\)/);assert.match(css,/:focus-visible/)
})
test('feature contains no provider calls, ingestion, financial calculations or fallback fixtures',()=>{
  for(const file of ['StrategyLab.tsx','api.ts','model.ts','ResultsSummary.tsx','TechnicalDetails.tsx']) {
    const source=readFileSync(resolve(frontend,'src/strategy-lab',file),'utf8')
    assert.doesNotMatch(source,/binance\.com|quidax\.io|ingestHistory|\/candle-ingestions|parseFloat\(|\.toFixed\(/)
    assert.doesNotMatch(source,/demoData|mockResult|fallbackResult/)
  }
})
