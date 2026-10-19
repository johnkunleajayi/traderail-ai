import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync,existsSync} from 'node:fs'
import {createRequire} from 'node:module'
import {dirname,resolve} from 'node:path'
import {fileURLToPath} from 'node:url'
import ts from 'typescript'
import React from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
// Contract fixture exported from the existing guarded backend API test with synthetic
// candles + injected mock DB. Never imported into application code or used as fallback.
const fixture=JSON.parse(readFileSync(new URL('./fixtures/validation-contract.json',import.meta.url),'utf8'))
const root=fileURLToPath(new URL('..',import.meta.url)),require=createRequire(new URL('../package.json',import.meta.url)),cache=new Map()
function load(file){
  const path=resolve(root,file);if(cache.has(path))return cache.get(path)
  if(/[/\\]auth[/\\]api.ts$/.test(path))return {apiBase:'http://127.0.0.1:8000'}
  const output=ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText
  const module={exports:{}};cache.set(path,module.exports)
  new Function('require','module','exports',output)(name=>{
    if(!name.startsWith('.'))return require(name)
    const base=resolve(dirname(path),name),target=['.ts','.tsx'].map(ext=>base+ext).find(existsSync)
    if(!target)throw new Error(name);return load(target)
  },module,module.exports);cache.set(path,module.exports);return module.exports
}
const {buildValidationRequest,initialValidationForm,suggestValidationWindows}=load('src/strategy-lab/validation/model.ts')
const {parseValidation}=load('src/strategy-lab/validation/schema.ts')
const {runValidation,readValidationHistory,ValidationApiError}=load('src/strategy-lab/validation/api.ts')
const Evidence=load('src/strategy-lab/validation/ValidationEvidence.tsx').default
const Form=load('src/strategy-lab/validation/ValidationForm.tsx').default
const Workspace=load('src/strategy-lab/validation/ValidationWorkspace.tsx').default
const render=(C,props)=>renderToStaticMarkup(React.createElement(C,props))
const clone=value=>structuredClone(value),request=()=>clone(fixture.request),body=()=>clone(fixture.response)
const draft=w=>Object.fromEntries(Object.entries(w).map(([k,v])=>[k,v.slice(0,19)]))
function form(){const r=request();return {...initialValidationForm(),canonical_symbol:r.canonical_symbol,provider:r.provider,timeframe:r.timeframe,requested_candle_count:String(r.requested_candle_count),initial_capital:r.initial_capital,quantity:r.quantity,...r.execution,delay_candles:String(r.execution.delay_candles),primary:draft(r.primary),walk_forward_folds:r.walk_forward_folds.map(draft)}}
const originalFetch=globalThis.fetch
test.afterEach(()=>{globalThis.fetch=originalFetch})
test('real backend contract parses without financial conversion or evidence reordering',()=>{
  const b=body(),before=JSON.stringify(b),parsed=parseValidation(b,request())
  assert.equal(parsed,b);assert.equal(JSON.stringify(b),before)
  assert.equal(parsed.validation.verdict,'INSUFFICIENT_EVIDENCE')
  assert.equal(parsed.validation.policy_configuration.maximum_oos_drawdown,'0.20')
  assert.equal(parsed.stress.length,6);assert.equal(parsed.sensitivity.length,2)
})
test('strict request construction matches backend fixture and fixes strategy version',()=>{
  const actual=buildValidationRequest(form()),expected=request()
  for(const w of [expected.primary,...expected.walk_forward_folds])for(const key of Object.keys(w))w[key]=new Date(w[key]).toISOString()
  assert.deepEqual(actual,expected)
  assert.deepEqual(Object.keys(actual).sort(),Object.keys(expected).sort())
  assert.equal(actual.strategy_family,'ADAPTIVE_TREND');assert.equal(actual.strategy_version,'1.0.0')
})
test('exact large and small decimal inputs stay strings',()=>{
  const f=form();f.initial_capital='999999999999999999999999.123456789012345678901234';f.quantity='0.000000000000000000000001'
  const r=buildValidationRequest(f);assert.equal(r.initial_capital,f.initial_capital);assert.equal(r.quantity,f.quantity)
})
for(const [name,mutate] of [
 ['zero capital',f=>{f.initial_capital='0'}],['negative quantity',f=>{f.quantity='-1'}],['exponent fee',f=>{f.fee_rate='1e-3'}],['excess cost',f=>{f.slippage_rate='1.01'}],['fractional delay',f=>{f.delay_candles='0.5'}],
 ['count 53',f=>{f.requested_candle_count='53'}],['count 301',f=>{f.requested_candle_count='301'}],['count duration mismatch',f=>{f.requested_candle_count='59'}],
 ['reversed primary',f=>{f.primary.out_of_sample_start=f.primary.in_sample_start}],['invalid date',f=>{f.primary.end='2025-02-30T00:00:00'}],
 ['short warmup',f=>{f.primary.in_sample_start=f.primary.warmup_start}],['overlapping folds',f=>{f.walk_forward_folds.push(clone(f.walk_forward_folds[0]))}],
 ['outside primary',f=>{f.walk_forward_folds[0].warmup_start='2024-12-31T00:00:00'}],['no folds',f=>{f.walk_forward_folds=[]}],['too many folds',f=>{f.walk_forward_folds=Array.from({length:5},()=>clone(f.primary))}],
])test(`invalid validation input: ${name}`,()=>{const f=form();mutate(f);assert.throws(()=>buildValidationRequest(f))})
function history(count=180){return Array.from({length:count},(_,i)=>({canonical_symbol:'BTC/USDT',provider:'BINANCE',timeframe:'1h',open_time:new Date(Date.UTC(2025,0,1)+i*3600000).toISOString(),close_time:new Date(Date.UTC(2025,0,1)+(i+1)*3600000-1).toISOString(),retrieved_at:'2025-02-01T00:00:00Z',open:'100',high:'101',low:'99',close:'100',volume:'1'}))}
test('stored-only assistance produces explicit valid disjoint folds without metrics',()=>{
  const f=form();f.requested_candle_count='180';f.walk_forward_folds=Array.from({length:3},()=>clone(f.primary))
  const suggestion=suggestValidationWindows(history(),f),r=buildValidationRequest({...f,...suggestion})
  assert.equal(r.walk_forward_folds.length,3)
  assert.equal(r.walk_forward_folds[0].end,r.walk_forward_folds[1].warmup_start)
  assert.equal(r.walk_forward_folds[1].end,r.walk_forward_folds[2].warmup_start)
  for(const w of r.walk_forward_folds)assert.equal(Date.parse(w.in_sample_start)-Date.parse(w.warmup_start),52*3600000)
  assert.deepEqual(Object.keys(suggestion),['primary','walk_forward_folds'])
})
test('assistance rejects insufficient exact history, gaps and impossible fold coverage',()=>{
  assert.throws(()=>suggestValidationWindows([],form()),/fewer stored candles/)
  const f=form();f.walk_forward_folds=[clone(f.primary),clone(f.primary)];assert.throws(()=>suggestValidationWindows(history(60),f),/Each suggested/)
  const rows=history(60);rows[15].open_time=rows[14].open_time
  assert.throws(()=>suggestValidationWindows(rows,form()),/gap/)
})
test('authenticated validation POST uses TradeRail cookie/origin behavior with no custom credentials',async()=>{
  globalThis.fetch=async(url,opts)=>{
    assert.equal(url,'http://127.0.0.1:8000/api/strategy-lab/validations');assert.equal(opts.method,'POST');assert.equal(opts.credentials,'include');assert.equal(opts.cache,'no-store')
    assert.deepEqual(opts.headers,{'Content-Type':'application/json'});assert.deepEqual(JSON.parse(opts.body),request())
    return new Response(JSON.stringify(body()))
  }
  const result=await runValidation(request());assert.equal(result.validation.report_identity,body().validation.report_identity)
})
test('history suggestions call stored TradeRail API only',async()=>{
  globalThis.fetch=async(url,opts)=>{const u=new URL(url);assert.equal(u.origin,'http://127.0.0.1:8000');assert.equal(decodeURIComponent(u.pathname),'/api/market-data/stored-candles/BTC/USDT');assert.equal(u.searchParams.get('limit'),'1000');assert.equal(opts.credentials,'omit');assert.equal(opts.method,undefined);return new Response(JSON.stringify(history(60)))}
  assert.equal((await readValidationHistory('BTC/USDT','1h')).length,60)
})
for(const verdict of ['INSUFFICIENT_EVIDENCE','FAILED_VALIDATION','VALIDATION_CANDIDATE'])test(`backend verdict ${verdict} is rendered literally without frontend judgment`,()=>{
  const b=body();b.validation.verdict=verdict
  const html=render(Evidence,{result:parseValidation(b,request())})
  assert.ok(html.includes(verdict));assert.ok(html.includes('does not authorize paper, shadow or live trading'))
  assert.ok(html.includes('What drove this verdict?'));assert.ok(html.includes('Policy v'))
})
test('verdict reasons and explanations remain backend sourced with progressive disclosure',()=>{
  const b=body(),html=render(Evidence,{result:parseValidation(b,request())})
  for(const c of b.criteria){assert.ok(html.includes(c.code));assert.ok(html.includes(c.reason_code));assert.ok(html.includes(c.explanation.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#x27;')))}
  assert.match(html,/<details/);assert.match(html,/Passed criteria/)
})
test('primary OOS, independent fold summaries/boundaries and exact metrics render',()=>{
  const b=body();b.primary_oos.metrics.net_return='0.123456789012345678901234';b.primary_oos.metrics.exposure_percentage='50';b.primary_oos.metrics.profit_factor=null
  const html=render(Evidence,{result:parseValidation(b,request())})
  for(const text of ['Primary out-of-sample evidence','OOS:','Independent','fold','profitable folds','losing folds','breakeven folds','zero trade folds','Exact backend metric values','0.123456789012345678901234','50%','Unavailable'])assert.ok(html.toLowerCase().includes(text.toLowerCase()),text)
  assert.ok(html.includes(b.walk_forward.folds[0].fold_identity))
})
test('sensitivity shows baseline and every returned probe without ranking or parameter selection',()=>{
  const b=body(),html=render(Evidence,{result:parseValidation(b,request())})
  assert.match(html,/Baseline · unchanged Adaptive Trend configuration/);assert.match(html,/Variant minus baseline/)
  for(const probe of b.sensitivity){assert.ok(html.includes(probe.configuration_identity));for(const key of probe.changed_fields)assert.ok(html.includes(key.replaceAll('_',' ')))}
  assert.doesNotMatch(html,/Best parameter|Recommended configuration|Winner/)
})
test('CASH and BUY_AND_HOLD retain matching benchmark context and policy treatment',()=>{
  const b=body(),html=render(Evidence,{result:parseValidation(b,request())})
  for(const benchmark of b.benchmarks){assert.ok(html.includes(benchmark.kind));assert.ok(html.includes(benchmark.window_identity));assert.equal(benchmark.dataset_identity,b.validation.dataset_identity)}
  assert.ok(html.includes('INFORMATIONAL'));assert.match(html,/only affects the verdict when the backend policy makes it mandatory/)
})
test('all stress scenarios and backend differences/assumptions remain present',()=>{
  const b=body(),html=render(Evidence,{result:parseValidation(b,request())})
  for(const scenario of b.stress){assert.ok(html.includes(scenario.code));assert.ok(html.includes(scenario.scenario_identity));assert.ok(html.includes(scenario.assumptions.cost_model_identity))}
  assert.ok(html.includes('Return-sign changed:'));assert.ok(html.includes('total cost change'))
})
test('unaffordable stress remains explicit with unavailable metrics, no resizing or omission',()=>{
  const b=body(),s=b.stress.find(s=>s.code!=='BASELINE')
  Object.assign(s,{execution_failure:'UNAFFORDABLE_EXECUTION',failure_source_identity:'failure-contract-test',metrics:null,differences:null,validation_identity:null,result_identity:null})
  const html=render(Evidence,{result:parseValidation(b,request())})
  assert.ok(html.includes('UNAFFORDABLE_EXECUTION'));assert.ok(html.includes('Performance metrics: Unavailable'));assert.ok(html.includes('Quantity was not silently resized'))
})
test('policy criteria, exact observations/thresholds, identities and nulls are inspectable',()=>{
  const b=body(),before=JSON.stringify(b),html=render(Evidence,{result:parseValidation(b,request())})
  for(const text of [b.validation.validation_identity,b.validation.report_identity,b.validation.dataset_identity,b.validation.policy_identity,b.sensitivity_identity,b.stress_evidence_identity,'Technical identities','Backend policy configuration','Observed','Required','SUFFICIENT','INSUFFICIENT','Unavailable'])assert.ok(html.includes(text),text)
  assert.equal(JSON.stringify(b),before)
})
test('returns cannot override the backend verdict or trigger promotion',()=>{
  const b=body();b.primary_oos.metrics.net_return='999999999999999999.999999999999999999'
  const html=render(Evidence,{result:parseValidation(b,request())});assert.match(html,/INSUFFICIENT_EVIDENCE/)
  assert.doesNotMatch(html,/<button[^>]*>.*Promote|Approved for live|Guaranteed profitable/)
})
for(const [status,code,match] of [[401,'','session'],[403,'','origin'],[404,'','instrument'],[409,'insufficient_stored_history','exact instrument'],[409,'invalid_candle_dataset','canonical data'],[422,'','chronological'],[503,'','temporarily'],[500,'','temporarily']])test(`controlled validation error ${status}/${code}`,async()=>{
  globalThis.fetch=async()=>new Response(JSON.stringify({detail:{code,message:'PRIVATE STACK TRACE'}}),{status})
  await assert.rejects(runValidation(request()),e=>e instanceof ValidationApiError&&e.status===status&&e.message.includes(match)&&!e.message.includes('PRIVATE'))
})
test('network failure and malformed success never become fake evidence',async()=>{
  globalThis.fetch=async()=>{throw new Error('private network details')}
  await assert.rejects(runValidation(request()),e=>e.status===0&&!e.message.includes('private'))
  globalThis.fetch=async()=>new Response('{not JSON')
  await assert.rejects(runValidation(request()),e=>e.status===502)
})
for(const [name,mutate] of [
 ['float metric',b=>{b.primary_oos.metrics.net_return=0.12}],['missing metric',b=>{delete b.primary_oos.metrics.profit_factor}],['unknown verdict',b=>{b.validation.verdict='SAFE'}],
 ['wrong instrument',b=>{b.validation.canonical_symbol='ETH/USDT'}],['wrong timeframe',b=>{b.validation.timeframe='4h'}],['wrong count',b=>{b.validation.candle_count=526}],
 ['wrong boundary',b=>{b.validation.primary_boundaries.end='2025-01-03T13:00:00Z'}],['wrong execution',b=>{b.validation.baseline_execution.fee_rate='0.99'}],
 ['missing fold',b=>{b.walk_forward.folds=[]}],['wrong benchmark dataset',b=>{b.benchmarks[0].dataset_identity='other'}],['missing baseline',b=>{b.stress=b.stress.filter(s=>s.code!=='BASELINE')}],
 ['duplicate scenario',b=>{b.stress.push(clone(b.stress[0]))}],['bad criterion status',b=>{b.criteria[0].status='APPROVED'}],['wrong benchmark context',b=>{b.verdict_explanation.benchmark_context[0].result_identity='other'}],
])test(`malformed evidence ${name} is rejected without repair`,()=>{const b=body();mutate(b);assert.throws(()=>parseValidation(b,request()))})
test('empty/loading form is labelled, keyboard accessible and has no fictional result defaults',()=>{
  const html=render(Form,{values:initialValidationForm(),onChange(){},onRun(){},onSuggest(){},busy:true,suggesting:false,notice:''})
  assert.match(html,/aria-busy="true"/);assert.match(html,/<fieldset disabled/);assert.match(html,/Running formal validation/)
  for(const id of ['validation-symbol','validation-timeframe','validation-primary-warmup_start','validation-primary-out_of_sample_start','validation-fold-0-in_sample_start']){assert.ok(html.includes(`for="${id}"`));assert.ok(html.includes(`id="${id}"`))}
  const empty=render(Workspace,{onSessionExpired(){}})
  assert.match(empty,/No formal validation run yet/);assert.doesNotMatch(empty,/BACKEND RESEARCH VERDICT|VALIDATION_CANDIDATE/)
})
test('workflow navigation keeps experiments mounted and lazy-loads validation only on entry',()=>{
  const source=readFileSync(resolve(root,'src/strategy-lab/StrategyLab.tsx'),'utf8')
  assert.match(source,/Strategy Lab workflows/);assert.match(source,/>Experiment<\/button>/);assert.match(source,/>Formal validation<\/button>/)
  assert.match(source,/hidden=\{workflow !== 'experiment'\}/);assert.match(source,/validationOpened &&/)
  assert.match(source,/lazy\(\(\) => import\('\.\/validation\/ValidationWorkspace'\)\)/)
  assert.match(source,/<ExperimentVisualization/);assert.match(source,/<ExperimentForm/);assert.match(source,/<ResultsSummary/)
})
test('new UI is responsive-safe, uses tokens/focus states, and contains no financial/validation engine',()=>{
  const css=readFileSync(resolve(root,'src/styles.css'),'utf8').split('/* Strategy Lab formal validation:')[1]
  assert.match(css,/minmax\(0,1fr\)/);assert.match(css,/max-width:540px/);assert.match(css,/:focus-visible/);assert.match(css,/var\(--line\)/)
  for(const file of ['api.ts','schema.ts','model.ts','ValidationEvidence.tsx','ValidationForm.tsx','ValidationWorkspace.tsx','presentation.tsx']){
    const s=readFileSync(resolve(root,'src/strategy-lab/validation',file),'utf8')
    assert.doesNotMatch(s,/binance\.com|quidax\.io|candle-ingestions|ingestHistory|parseFloat\(|\.toFixed\(/)
    assert.doesNotMatch(s,/maximum_oos_drawdown\s*[=:]|minimum_oos_completed_trades\s*[=:]|bestVariant|rankScenarios|fakeResult|fallbackEvidence/)
  }
  const workspace=readFileSync(resolve(root,'src/strategy-lab/validation/ValidationWorkspace.tsx'),'utf8')
  assert.match(workspace,/if\(runController.current\|\|suggesting\)return/);assert.match(workspace,/if\(suggesting\).*abort/)
})
