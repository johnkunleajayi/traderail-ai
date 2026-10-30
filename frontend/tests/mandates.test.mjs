import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
const root=fileURLToPath(new URL('..',import.meta.url))
const require=createRequire(new URL('../package.json',import.meta.url))
function loader(overrides={}) {
  const cache=new Map()
  function load(relative){
    const path=resolve(root,relative)
    if(overrides[path])return overrides[path]
    if(cache.has(path))return cache.get(path)
    if(path.replaceAll('\\','/').endsWith('auth/api.ts'))return {apiBase:'http://127.0.0.1:8000'}
    const output=ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText
    const module={exports:{}}
    new Function('require','module','exports',output)(name=>{
      if(name.endsWith('.css'))return {}
      if(!name.startsWith('.'))return name==='react'&&overrides.react?overrides.react:require(name)
      const base=resolve(dirname(path),name)
      return load(['.ts','.tsx'].map(ext=>base+ext).find(existsSync))
    },module,module.exports)
    cache.set(path,module.exports);return module.exports
  }
  return load
}
const load=loader(), model=load('src/mandates/model.ts'), api=load('src/mandates/api.ts')
const id='11111111-1111-4111-8111-111111111111'
const owner='22222222-2222-4222-8222-222222222222'
const fixture=(status='DRAFT')=>({...model.buildIntent({...model.newForm,capital:'2000.000000000000000001'}),id,user_id:owner,status,created_at:'2026-10-24T00:00:00Z',updated_at:'2026-10-24T00:00:00Z'})
const originalFetch=globalThis.fetch
test.afterEach(()=>{globalThis.fetch=originalFetch})
const response=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body})
const settle=()=>new Promise(resolve=>setImmediate(resolve))
const render=tree=>renderToStaticMarkup(tree)
function walk(node,predicate,result=[]){
  if(Array.isArray(node)){node.forEach(child=>walk(child,predicate,result));return result}
  if(!node||typeof node!=='object')return result
  if(predicate(node))result.push(node)
  walk(node.props?.children,predicate,result);return result
}
function text(node){if(typeof node==='string'||typeof node==='number')return String(node);if(Array.isArray(node))return node.map(text).join('');return node&&typeof node==='object'?text(node.props?.children):''}
function button(tree,label){const match=walk(tree,node=>node.type==='button'&&text(node).includes(label))[0];assert.ok(match,label);return match}
function harness(path,props){
  let index=0;const slots=[],pending=[],cleanups=[]
  const hooks={...React,
    useState(initial){const slot=index++;if(!(slot in slots))slots[slot]=typeof initial==='function'?initial():initial;return [slots[slot],value=>{slots[slot]=typeof value==='function'?value(slots[slot]):value}]},
    useRef(initial){const slot=index++;return slots[slot]??(slots[slot]={current:initial})},
    useEffect(effect,deps){const slot=index++;if(!slots[slot]||deps.some((value,i)=>!Object.is(value,slots[slot][i]))){slots[slot]=deps;pending.push([slot,effect])}},
  }
  const local=loader({react:hooks,[resolve(root,'src/mandates/api.ts')]:api})
  const Component=local(path).default
  return {
    render(){index=0;return Component(props)},
    async effects(){for(const [slot,effect]of pending.splice(0)){cleanups[slot]?.();cleanups[slot]=effect()}await settle()},
    dispose(){cleanups.forEach(cleanup=>cleanup?.())},
  }
}

test('capital stays an exact decimal string and financial display preserves all digits',()=>{
  const request=model.buildIntent({...model.newForm,capital:' 999999999999999999999.1234567890123456789 '})
  assert.equal(request.capital_allocation,'999999999999999999999.1234567890123456789')
  assert.equal(model.formatAllocation(request.capital_allocation),'999,999,999,999,999,999,999.1234567890123456789 USDT')
  assert.equal(model.formatAllocation('2000'),'2,000 USDT')
  assert.equal(model.formatAllocation('.0001'),'0.0001 USDT')
})
for(const capital of ['','0','0.000','-1','NaN','Infinity','1e3','1,000','2.','+2'])test(`invalid capital ${capital}`,()=>assert.throws(()=>model.buildIntent({...model.newForm,capital})))

test('all supported risk/style preferences construct only intent, with no timeframe mapping',()=>{
  for(const risk of model.riskOptions)for(const style of model.styleOptions){
    const value=model.buildIntent({capital:'1',risk:risk.value,style:style.value})
    assert.equal(value.risk_level,risk.value);assert.equal(value.trading_style,style.value)
    assert.deepEqual(Object.keys(value).sort(),['environment','capital_allocation','allocation_currency','instrument','risk_level','trading_style','direction_capability'].sort())
  }
})

test('authenticated create/list/read/update use exact real endpoints and allowlisted bodies',async()=>{
  const calls=[];globalThis.fetch=async(url,options)=>{calls.push([url,options]);return response(url.endsWith('/api/mandates')&&options.method==='GET'?[fixture()]:fixture(),options.method==='POST'?201:200)}
  const intent={...fixture(),quantity:'999',balance:'100000',price:'1',approval:'APPROVED'}
  await api.createMandate(intent);await api.listMandates();await api.readMandate(id);await api.updateMandate(id,intent)
  assert.deepEqual(calls.map(([url])=>url),['http://127.0.0.1:8000/api/mandates','http://127.0.0.1:8000/api/mandates',`http://127.0.0.1:8000/api/mandates/${id}`,`http://127.0.0.1:8000/api/mandates/${id}/intent`])
  for(const [,options]of calls){assert.equal(options.credentials,'include');assert.equal(options.cache,'no-store');if(options.body){const body=JSON.parse(options.body);assert.equal(body.capital_allocation,intent.capital_allocation);assert.equal(Object.keys(body).length,7);assert.equal(options.headers['Content-Type'],'application/json');assert.ok(!('Origin'in options.headers))}}
})
for(const [action,status]of [['activate','ACTIVE'],['pause','PAUSED'],['retire','RETIRED']])test(`lifecycle ${action} reflects backend ${status}`,async()=>{
  globalThis.fetch=async(url,options)=>{assert.ok(url.endsWith(`/${id}/${action}`));assert.equal(options.body,'{}');return response(fixture(status))}
  assert.equal((await api.changeMandate(id,action)).status,status)
})

test('server status is authoritative, not inferred from requested lifecycle action',async()=>{
  globalThis.fetch=async()=>response(fixture('PAUSED'))
  assert.equal((await api.changeMandate(id,'activate')).status,'PAUSED')
})
for(const status of [401,403,404,409,422,503])test(`controlled mandate API error ${status}`,async()=>{
  globalThis.fetch=async()=>response({detail:'private internal data'},status)
  await assert.rejects(api.changeMandate(id,'activate'),error=>error instanceof api.MandateApiError&&error.status===status&&!error.message.includes('private'))
})

test('malformed responses, duplicate IDs and context mismatches never become success',async()=>{
  for(const value of [{...fixture(),environment:'LIVE'},{...fixture(),allocation_currency:'USD'},{...fixture(),capital_allocation:2000},{...fixture(),status:'APPROVED'},null])assert.throws(()=>api.parseMandate(value))
  globalThis.fetch=async()=>response([fixture(),fixture()]);await assert.rejects(api.listMandates())
  globalThis.fetch=async()=>response({...fixture(),id:owner});await assert.rejects(api.readMandate(id))
  await assert.rejects(api.updateMandate(id,fixture()))
})
test('network failure is uncertain, has no fake fallback and is never automatically retried',async()=>{
  let calls=0;globalThis.fetch=async()=>{calls++;throw new Error('offline')}
  await assert.rejects(api.createMandate(fixture()),/reload saved mandates/)
  assert.equal(calls,1)
})

test('guided flow validates capital, selects risk/style, reviews, saves draft then separately activates',async()=>{
  let saved,activated,saveCalls=0
  const flow=harness('src/mandates/MandateFlow.tsx',{mandate:null,busy:false,onCancel(){},onSave:async intent=>{saveCalls++;saved=intent;return {...fixture(),...intent}},onActivate:row=>{activated=row}})
  let tree=flow.render();assert.match(render(tree),/STEP 1 OF 6/)
  async function submit(){walk(tree,node=>node.type==='form')[0].props.onSubmit({preventDefault(){}});await settle();tree=flow.render()}
  await submit();assert.match(render(tree),/Enter a positive capital/);assert.equal(saveCalls,0)
  walk(tree,node=>node.type==='input'&&node.props.id==='mandate-capital')[0].props.onChange({target:{value:'2000.1234567890123456789'}})
  tree=flow.render();await submit();assert.match(render(tree),/Bitcoin \/ Tether/)
  await submit();assert.match(render(tree),/Risk preference/)
  walk(tree,node=>node.type==='input'&&node.props.value==='CONSERVATIVE')[0].props.onChange();tree=flow.render()
  await submit();assert.match(render(tree),/Trading style/)
  walk(tree,node=>node.type==='input'&&node.props.value==='LONGER_TERM')[0].props.onChange();tree=flow.render()
  await submit();assert.match(render(tree),/2,000.1234567890123456789 USDT/)
  assert.match(render(tree),/Activating this mandate does not place a trade/);assert.equal(saveCalls,0)
  await submit();assert.equal(saveCalls,1);assert.equal(saved.risk_level,'CONSERVATIVE');assert.equal(saved.trading_style,'LONGER_TERM')
  assert.match(render(tree),/STEP 6 OF 6/);assert.equal(activated,undefined)
  button(tree,'Activate mandate').props.onClick();assert.equal(activated.id,id)
})

test('loading and empty state are intentional, existing mandates avoid forced creation',async()=>{
  globalThis.fetch=async()=>response([])
  const view=harness('src/mandates/MandateWorkspace.tsx',{onSessionExpired(){}})
  assert.match(render(view.render()),/Loading your saved mandates/)
  await view.effects();let tree=view.render();assert.match(render(tree),/Define your first Paper mandate/)
  button(tree,'Create Paper mandate').props.onClick();tree=view.render()
  assert.ok(walk(tree,node=>node.type?.name==='MandateFlow').length)
  view.dispose()
})

for(const [status,allowed,absent]of [['DRAFT','Activate mandate','Pause mandate'],['ACTIVE','Pause mandate','Continue editing'],['PAUSED','Reactivate mandate','Pause mandate'],['RETIRED','read-only','Activate mandate']])test(`saved ${status} renders appropriate server-backed actions`,async()=>{
  globalThis.fetch=async url=>response(url.endsWith('/api/mandates')?[fixture(status)]:fixture(status))
  const view=harness('src/mandates/MandateWorkspace.tsx',{onSessionExpired(){}})
  view.render();await view.effects();let tree=view.render()
  assert.doesNotMatch(render(tree),/Define your first Paper mandate/)
  await walk(tree,node=>node.type==='button'&&node.props.className==='mandate-saved-row')[0].props.onClick();await settle();tree=view.render()
  assert.ok(render(tree).includes(allowed));assert.ok(!render(tree).includes(absent))
  if(status==='RETIRED')assert.doesNotMatch(render(tree),/Retire mandate|Continue editing|Reactivate/)
  view.dispose()
})

test('activation, pause and retirement use returned state without optimistic mutation',async()=>{
  let row=fixture('DRAFT');const writes=[]
  globalThis.fetch=async(url,options)=>{
    if(options.method==='POST'){writes.push(url);row=fixture(url.endsWith('/activate')?'ACTIVE':url.endsWith('/pause')?'PAUSED':'RETIRED')}
    return response(url.endsWith('/api/mandates')?[row]:row)
  }
  const view=harness('src/mandates/MandateWorkspace.tsx',{onSessionExpired(){}})
  view.render();await view.effects();let tree=view.render()
  await walk(tree,node=>node.type==='button'&&node.props.className==='mandate-saved-row')[0].props.onClick();await settle();tree=view.render()
  button(tree,'Activate mandate').props.onClick();await settle();tree=view.render();assert.match(render(tree),/active preferences/)
  button(tree,'Pause mandate').props.onClick();await settle();tree=view.render();assert.match(render(tree),/Your mandate is paused/)
  button(tree,'Reactivate mandate').props.onClick();await settle();tree=view.render();assert.match(render(tree),/active preferences/)
  button(tree,'Retire mandate').props.onClick();tree=view.render();assert.match(render(tree),/Retirement is permanent/)
  button(tree,'Confirm retirement').props.onClick();await settle();tree=view.render();assert.match(render(tree),/retired and read-only/)
  assert.equal(writes.length,4);assert.ok(writes.every(url=>url.includes('/api/mandates/')))
  view.dispose()
})

test('409 lifecycle rejection leaves existing state visible and offers authoritative reload',async()=>{
  globalThis.fetch=async(url,options)=>response(options.method==='POST'?{}:url.endsWith('/api/mandates')?[fixture()]:fixture(),options.method==='POST'?409:200)
  const view=harness('src/mandates/MandateWorkspace.tsx',{onSessionExpired(){}})
  view.render();await view.effects();let tree=view.render()
  await walk(tree,node=>node.type==='button'&&node.props.className==='mandate-saved-row')[0].props.onClick();await settle();tree=view.render()
  button(tree,'Activate mandate').props.onClick();await settle();tree=view.render()
  assert.match(render(tree),/Mandate action not confirmed/);assert.match(render(tree),/Reload saved mandates/);assert.doesNotMatch(render(tree),/active preferences/)
  view.dispose()
})
test('expired session returns to existing authentication and does not fabricate mandates',async()=>{
  let expired=0;globalThis.fetch=async()=>response({},401)
  const view=harness('src/mandates/MandateWorkspace.tsx',{onSessionExpired(){expired++}})
  view.render();await view.effects();assert.equal(expired,1);view.dispose()
})

test('navigation remains authenticated, lazy and resets mandate state at sign out',()=>{
  const source=readFileSync(resolve(root,'src/components/AuthExperience.tsx'),'utf8')
  assert.match(source,/lazy\(\(\) => import\('..\/mandates\/MandateWorkspace'\)\)/)
  assert.match(source,/<span>Trading Mandates<\/span>/)
  assert.match(source,/setMandatesOpened\(false\)/)
  assert.match(source,/hidden=\{view !== 'mandates'\}/)
})
test('scoped responsive styles, keyboard controls and no Risk/execution connection',()=>{
  const css=readFileSync(resolve(root,'src/mandates/mandates.css'),'utf8')
  assert.match(css,/max-width:400px/);assert.match(css,/prefers-reduced-motion/);assert.match(css,/focus-visible/);assert.match(css,/minmax\(0,1fr\)/)
  assert.match(readFileSync(resolve(root,'src/styles.css'),'utf8'),/workspace-feature-nav \{ flex-wrap:wrap/)
  for(const file of readdirSync(resolve(root,'src/mandates')).filter(name=>/\.tsx?$/.test(name))){
    const source=readFileSync(resolve(root,'src/mandates',file),'utf8')
    assert.doesNotMatch(source,/\/api\/(risk|paper|market-data|strategy-lab)|parseFloat\(|toFixed\(/)
    if(!['api.ts','performance.ts','activity.ts'].includes(file))assert.doesNotMatch(source,/fetch\(/)
  }
})
test('paused edits retain exact capital and address the existing mandate rather than creating another',async()=>{
  let savedId, savedIntent
  const flow=harness('src/mandates/MandateFlow.tsx',{mandate:fixture('PAUSED'),busy:false,onCancel(){},onSave:async(intent,id)=>{savedId=id;savedIntent=intent;return {...fixture('PAUSED'),...intent}},onActivate(){}})
  let tree=flow.render()
  assert.ok(render(tree).includes(fixture().capital_allocation))
  walk(tree,node=>node.type==='input'&&node.props.id==='mandate-capital')[0].props.onChange({target:{value:'1000.000000000000000001'}})
  for(let step=0;step<5;step++){tree=flow.render();walk(tree,node=>node.type==='form')[0].props.onSubmit({preventDefault(){}});await settle()}
  tree=flow.render();assert.equal(savedId,id);assert.equal(savedIntent.capital_allocation,'1000.000000000000000001')
  assert.match(render(tree),/Status: Paused/)
})
test('successful draft save keeps the flow mounted for the separate activation step',async()=>{
  let writes=0
  globalThis.fetch=async(url,options)=>{if(options.method==='POST'){writes++;return response(fixture(),201)}return response([])}
  const view=harness('src/mandates/MandateWorkspace.tsx',{onSessionExpired(){}})
  view.render();await view.effects();let tree=view.render()
  button(tree,'Create Paper mandate').props.onClick();tree=view.render()
  let flow=walk(tree,node=>node.type?.name==='MandateFlow')[0]
  assert.equal(flow.key,null)
  const saved=await flow.props.onSave(model.buildIntent({...model.newForm,capital:'2000'}));assert.equal(saved.id,id)
  tree=view.render();flow=walk(tree,node=>node.type?.name==='MandateFlow')[0]
  assert.equal(flow.key,null);assert.equal(flow.props.mandate.id,id)
  assert.equal(writes,1);assert.match(render(tree),/Mandate preferences saved/)
  view.dispose()
})
test('double lifecycle clicks cannot enqueue concurrent duplicate writes',async()=>{
  let finish, writes=0
  globalThis.fetch=async(url,options)=>{
    if(options.method==='POST'){writes++;return new Promise(resolve=>{finish=()=>resolve(response(fixture('ACTIVE')))})}
    return response(url.endsWith('/api/mandates')?[fixture()]:fixture())
  }
  const view=harness('src/mandates/MandateWorkspace.tsx',{onSessionExpired(){}})
  view.render();await view.effects();let tree=view.render()
  await walk(tree,node=>node.type==='button'&&node.props.className==='mandate-saved-row')[0].props.onClick();await settle();tree=view.render()
  const activate=button(tree,'Activate mandate')
  activate.props.onClick();activate.props.onClick();assert.equal(writes,1)
  tree=view.render();assert.equal(button(tree,'Updating').props.disabled,true)
  finish();await settle();tree=view.render();assert.match(render(tree),/active preferences/)
  view.dispose()
})
test('failed initial loading is a visible failure, never an invented empty-success state',async()=>{
  globalThis.fetch=async()=>response({},503)
  const view=harness('src/mandates/MandateWorkspace.tsx',{onSessionExpired(){}})
  view.render();await view.effects();const html=render(view.render())
  assert.match(html,/Mandate action not confirmed/);assert.doesNotMatch(html,/Define your first Paper mandate/)
  view.dispose()
})
