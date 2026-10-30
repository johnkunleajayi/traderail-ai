import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
const root=fileURLToPath(new URL('..',import.meta.url)),require=createRequire(new URL('../package.json',import.meta.url))
function loader(overrides={}){const cache=new Map();function load(relative){const path=resolve(root,relative);if(overrides[path])return overrides[path];if(cache.has(path))return cache.get(path);if(path.replaceAll('\\','/').endsWith('auth/api.ts'))return overrides.auth??{apiBase:'http://127.0.0.1:8000'};const output=ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;const module={exports:{}};new Function('require','module','exports',output)(name=>{if(name.endsWith('.css'))return {};if(!name.startsWith('.'))return name==='react'&&overrides.react?overrides.react:require(name);const base=resolve(dirname(path),name);return load(['.ts','.tsx'].map(e=>base+e).find(existsSync))},module,module.exports);cache.set(path,module.exports);return module.exports}return load}

const load=loader(),api=load('src/mandates/activity.ts'),ui=load('src/mandates/MandateActivity.tsx')
const id='11111111-1111-4111-8111-111111111111', date='2026-10-30T10:00:00Z'
const fixture=(changes={})=>({scope:'PAPER_ACTIVITY_READ_ONLY',mandate_identity:id,environment:'PAPER',currency:'USDT',portfolio_cash:'10000.000000000000000001',portfolio_state:'AVAILABLE',runner_health:'UNVERIFIED',runner_health_reason:'HEARTBEAT_NOT_PERSISTED',observed_at:date,latest_evaluation:null,...changes})
const evaluation=(category='NO_TRADE',state='COMPLETED')=>({work_id:id,category,state,outcome_code:category,reason_code:'STRATEGY_NO_ACTION',attempts:1,recorded_at:date,last_attempt_at:date,retry_at:null,fresh_until:'2026-10-30T10:10:00Z'})
const render=value=>renderToStaticMarkup(React.createElement(ui.ActivityView,{report:api.parseActivity(value,id)}))
test('empty activity never manufactures a strategy decision or runner health',()=>{const html=render(fixture());assert.match(html,/No runner evaluation recorded/);assert.match(html,/Not verified/);assert.match(html,/not persisted/);assert.match(html,/10,000/);assert.doesNotMatch(html,/Runner online/);assert.match(html,/not a mandate wallet/)})
for(const [category,state,label] of [['NO_TRADE','COMPLETED','No trade'],['ENTRY_EXECUTED','COMPLETED','Paper entry executed'],['POSITION_HELD','COMPLETED','Holding position'],['POSITION_CLOSED','COMPLETED','Position closed'],['RISK_REJECTED','COMPLETED','Risk rejected'],['PENDING','RETRY_WAIT','Evaluation pending'],['FAILURE','RECONCILIATION_REQUIRED','Reconciliation required']])test(`persisted ${category} is explained without pretending runner is online`,()=>{const html=render(fixture({latest_evaluation:evaluation(category,state)}));assert.ok(html.includes(label));assert.match(html,/STRATEGY_NO_ACTION/);assert.match(html,/<time/);assert.match(html,/Last persisted update/);assert.match(html,/Not verified/)})
test('unavailable cash is not zero and Decimal source remains exact',()=>{const html=render(fixture());assert.match(html,/10000\.000000000000000001/);const missing=render(fixture({portfolio_state:'UNAVAILABLE',portfolio_cash:null}));assert.match(missing,/Not available/);assert.doesNotMatch(missing,/>0\.00</)})
for(const change of [{environment:'LIVE'},{mandate_identity:'wrong'},{runner_health:'ONLINE'},{portfolio_cash:10000},{latest_evaluation:evaluation('ENTRY_EXECUTED','PENDING')}])test('invalid activity context fails closed '+JSON.stringify(change),()=>assert.throws(()=>api.parseActivity(fixture(change),id)))
test('activity client is authenticated GET/no-store and rejects failures',async()=>{const original=globalThis.fetch;let called;try{globalThis.fetch=async(url,options)=>{called={url,options};return{ok:true,json:async()=>fixture()}};assert.equal((await api.readActivity(id)).portfolio_cash,'10000.000000000000000001');assert.match(called.url,/\/mandates\/.*\/activity$/);assert.equal(called.options.credentials,'include');assert.equal(called.options.cache,'no-store');assert.equal(called.options.method,undefined);globalThis.fetch=async()=>({ok:false,status:503});await assert.rejects(api.readActivity(id),/unavailable/)}finally{globalThis.fetch=original}})
test('activity integration remains read-only, responsive and explicit-refresh',()=>{const source=readFileSync(resolve(root,'src/mandates/MandateActivity.tsx'),'utf8');assert.doesNotMatch(source,/paper-run|\/api\/risk|setInterval|parseFloat|toFixed/);assert.match(source,/Loading Paper activity/);assert.match(source,/role="alert"/);assert.match(source,/Retry activity/);const parent=readFileSync(resolve(root,'src/mandates/MandatePerformance.tsx'),'utf8');assert.match(parent,/MandateActivity key=\{mandate.id\}/);assert.match(parent,/refresh=\{attempt\}/);const css=readFileSync(resolve(root,'src/workspace.css'),'utf8');assert.match(css,/ws-activity-grid/);assert.match(css,/overflow-wrap:anywhere/);assert.match(css,/max-width:650px/);assert.match(css,/data-theme=dark/)})

function walk(n,p,result=[]){if(Array.isArray(n)){n.forEach(x=>walk(x,p,result));return result}if(!n||typeof n!=='object')return result;if(p(n))result.push(n);walk(n.props?.children,p,result);return result}
function nodeText(n){return typeof n==='string'?n:Array.isArray(n)?n.map(nodeText).join(''):n&&typeof n==='object'?nodeText(n.props?.children):''}
function harness(path,props,overrides={}){let index=0;const slots=[],pending=[],cleanups=[];const hooks={...React,useCallback(fn,deps){const k=index++;if(!slots[k]||deps.some((v,i)=>!Object.is(v,slots[k].deps[i])))slots[k]={fn,deps};return slots[k].fn},useState(initial){const k=index++;if(!(k in slots))slots[k]=typeof initial==='function'?initial():initial;return[slots[k],v=>{slots[k]=typeof v==='function'?v(slots[k]):v}]},useRef(initial){const k=index++;return slots[k]??(slots[k]={current:initial})},useEffect(f,deps){const k=index++;if(!slots[k]||deps.some((v,i)=>!Object.is(v,slots[k][i]))){slots[k]=deps;pending.push([k,f])}}};const local=loader({...overrides,react:hooks});const Component=local(path).default;return{render(){index=0;return Component(props)},async effects(){for(const[k,f]of pending.splice(0)){cleanups[k]?.();cleanups[k]=f()}await new Promise(r=>setImmediate(r))},dispose(){cleanups.forEach(c=>c?.())}}}

test('activity loading, error and retry use real request states',async()=>{
  let fail=true,calls=0;const original=globalThis.fetch
  globalThis.fetch=async()=>{calls++;if(fail)throw Error('offline');return{ok:true,json:async()=>fixture()}}
  const mandate={id,updated_at:date,status:'ACTIVE'}
  const view=harness('src/mandates/MandateActivity.tsx',{mandate,refresh:0,onSessionExpired(){}})
  try{
    let tree=view.render();assert.match(renderToStaticMarkup(tree),/Loading persisted Paper activity/)
    assert.doesNotMatch(renderToStaticMarkup(tree),/10,000/)
    await view.effects();tree=view.render();assert.match(renderToStaticMarkup(tree),/Paper activity unavailable/)
    fail=false;walk(tree,n=>n.type==='button'&&nodeText(n).includes('Retry activity'))[0].props.onClick()
    view.render();await view.effects();tree=view.render()
    assert.match(renderToStaticMarkup(tree),/No runner evaluation recorded/);assert.equal(calls,2)
  }finally{view.dispose();globalThis.fetch=original}
})
