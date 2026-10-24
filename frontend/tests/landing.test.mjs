import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
const root = fileURLToPath(new URL('..', import.meta.url))
const require = createRequire(new URL('../package.json', import.meta.url))
const moduleCache = new Map()
function load(path) {
  if (moduleCache.has(path)) return moduleCache.get(path)
  if (path.endsWith('AuthExperience.tsx')) return { __esModule: true, default: ({ children }) => children }
  if (path.endsWith('ThemeToggle.tsx')) return { __esModule: true, default: () => React.createElement('button', { 'aria-label': 'Toggle theme' }) }
  if (path.replaceAll('\\', '/').endsWith('auth/api.ts')) return { googleLoginUrl: '/api/auth/google' }
  const output = ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText
  const module = { exports: {} }
  new Function('require','module','exports',output)(specifier => {
    if (specifier.endsWith('.css')) return {}
    if (!specifier.startsWith('.')) return require(specifier)
    const base = resolve(dirname(path),specifier)
    return load(['.ts','.tsx'].map(ext=>base+ext).find(existsSync))
  },module,module.exports)
  moduleCache.set(path,module.exports)
  return module.exports
}
const App = load(resolve(root,'src/App.tsx')).default
const html = renderToStaticMarkup(React.createElement(App))
test('public story renders all required sections and clear Paper/Live boundaries',()=>{
  for (const text of ['Systematic trading.','Engineered','for control.','Your funds stay at your exchange.','NO_TRADE / CASH','EXAMPLE TRADING MANDATE','2,000 USDT','Explicit Live Authorization','Future stage','Paper results do not guarantee Live performance','not an available configuration form']) assert.ok(html.includes(text),text)
  const flow = ['Market','Intelligence','Strategy','Risk','Execution','Position','Performance']
  for (const stage of flow) assert.ok(html.includes(`<h3>${stage}</h3>`))
  assert.doesNotMatch(html, /<input|<form|win rate|guaranteed profit|testimonial/i)
})
test('CTAs preserve existing login destination and section links resolve',()=>{
  assert.equal((html.match(/href="\/api\/auth\/google"/g)||[]).length,4)
  for (const match of html.matchAll(/href="#([^"]+)"/g)) assert.ok(html.includes(`id="${match[1]}"`),match[1])
  assert.match(html,/\/brand\/dark.png/)
  assert.match(html,/\/brand\/light.png/)
  assert.match(html,/Illustrative workflow/)
})
test('landing styles remain scoped and adapt to mobile and reduced motion',()=>{
  const css=readFileSync(resolve(root,'src/landing.css'),'utf8')
  assert.match(css,/max-width: 700px/)
  assert.match(css,/prefers-reduced-motion: reduce/)
  assert.match(css,/var\(--bg\)/)
  assert.match(css,/minmax\(0, 1fr\)/)
  assert.doesNotMatch(css,/\.workspace|\.authenticated-shell/)
})


test('photography uses existing files unchanged with useful alt text and lazy secondary images',()=>{
  for (const name of ['hero-trader-laptop.png','hero-trader-mobile.png','analysis-trader-desk.png']) {
    assert.ok(existsSync(resolve(root,'public/landing',name)))
    assert.ok(html.includes(`/landing/${name}`))
  }
  assert.match(html,/fetchPriority="high"/)
  assert.equal((html.match(/loading="lazy"/g)||[]).length,2)
  assert.match(html,/no current market readings/)
  assert.match(html,/not a live session/)
})
test('engine presents a real gate and both illustrative outcomes without claiming live signals',()=>{
  assert.match(html,/data-engine/)
  assert.equal((html.match(/data-node=/g)||[]).length,7)
  assert.match(html,/Execution cannot receive a signal before authorization./)
  assert.match(html,/NO_TRADE.*NOT REQUESTED.*CASH/)
  assert.match(html,/APPROVED.*AUTHORIZED.*POSITION OPEN/)
  assert.match(html,/Pause motion/)
  assert.match(html,/aria-pressed="false"/)
})
const { DEMO_SCENARIOS, INITIAL_DEMO_FRAME, nextDemoFrame, demoView } = load(resolve(root,'src/components/LandingDemo.tsx'))
test('one visibility-aware clock is disposable and entirely isolated from real trading',()=>{
  const demo=readFileSync(resolve(root,'src/components/LandingDemo.tsx'),'utf8')
  const motion=readFileSync(resolve(root,'src/components/LandingMotion.tsx'),'utf8')
  assert.match(motion,/prefers-reduced-motion: reduce/)
  for(const pattern of [/IntersectionObserver/,/document.hidden/,/clearInterval/,/observer.disconnect/,/removeEventListener/]) assert.match(demo,pattern)
  assert.equal((demo.match(/setInterval\(/g)||[]).length,1)
  assert.doesNotMatch(demo,/fetch\(|\/api\/|Math.random|market_data|evaluate_strategy/)
  assert.equal((html.match(/tabindex="0"/g)||[]).length,5)
})
test('all three exact scenarios are explicit demo fixtures',()=>{
  assert.deepEqual(DEMO_SCENARIOS.map(s=>[s.regime,s.strategy,s.risk,s.execution,s.outcome]),[
    ['BULL_TREND','ENTER_LONG','APPROVED','AUTHORIZED','POSITION OPEN'],
    ['BEAR_TREND','ENTER_SHORT','NO_TRADE','NOT REQUESTED','CASH'],
    ['STALE / UNSAFE','NO_TRADE','BLOCKED','NOT REQUESTED','CAPITAL PRESERVED']])
  assert.match(html,/NOT LIVE TRADING/)
  assert.match(html,/Hard-coded illustrations only/)
})
test('signal waits at Risk, then only approved LONG can reach execution',()=>{
  for(let scenario=0;scenario<3;scenario++) {
    for(let phase=0;phase<=10;phase++) {
      const view=demoView({scenario,phase})
      if(phase<4) { assert.equal(view.resolved,false); assert.equal(view.active,phase); assert.match(view.statuses[4],/Awaiting/) }
      if(scenario!==0&&phase>=4) { assert.equal(view.active,-1); assert.equal(view.statuses[4],'NOT REQUESTED'); assert.equal(view.statuses[5],'CASH · NO EXPOSURE') }
    }
  }
  assert.equal(demoView({scenario:0,phase:3}).statuses[3],'EVALUATING')
  assert.equal(demoView({scenario:0,phase:4}).statuses[3],'APPROVED')
  assert.equal(demoView({scenario:0,phase:4}).active,-1)
  assert.equal(demoView({scenario:0,phase:5}).active,4)
  assert.equal(demoView({scenario:0,phase:6}).active,5)
  assert.equal(demoView({scenario:0,phase:7}).active,6)
})
test('sequence loops deterministically and never mutates its input',()=>{
  let frame={...INITIAL_DEMO_FRAME}
  const original={...frame}
  for(let i=0;i<11;i++) frame=nextDemoFrame(frame)
  assert.deepEqual(INITIAL_DEMO_FRAME,original)
  assert.deepEqual(frame,{scenario:1,phase:0})
  for(let i=0;i<22;i++) frame=nextDemoFrame(frame)
  assert.deepEqual(frame,INITIAL_DEMO_FRAME)
  assert.equal(demoView({scenario:2,phase:4}).heartbeat,'CAPITAL PRESERVED')
})
test('scenario controls and static transcript support paused and reduced-motion inspection',()=>{
  assert.equal((html.match(/class="lp-demo-select"/g)||[]).length,1)
  for(const text of ['Authorized LONG','Unsupported SHORT','Unsafe evidence','Inspect the three demo outcomes']) assert.ok(html.includes(text))
  const source=readFileSync(resolve(root,'src/components/LandingDemo.tsx'),'utf8')
  assert.match(source,/if \(!root \|\| paused \|\| reduced/)
  assert.match(source,/reduced \? \{ \.\.\.frame, phase: 10 \}/)
})
function demoRuntime(initialFrame={scenario:0,phase:0}) {
  let frame=initialFrame, context
  const effects=[], observers=[], timers=new Map(), events=new Map()
  const surfaces=[{},{}]
  let timerId=0
  const root={querySelectorAll:()=>surfaces}
  class Observer {
    constructor(callback){this.callback=callback;this.disconnected=false;observers.push(this)}
    observe(){}
    disconnect(){this.disconnected=true}
  }
  const documentMock={hidden:false,addEventListener:(name,callback)=>events.set(name,callback),removeEventListener:name=>events.delete(name)}
  const module={exports:{}}
  const output=ts.transpileModule(readFileSync(resolve(rootPath(),'src/components/LandingDemo.tsx'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText
  new Function('require','module','exports','window','document','IntersectionObserver','setInterval','clearInterval',output)(name=>name==='react'?{
    createContext:value=>(context={value,Provider:()=>null}), useContext:ctx=>ctx.value,
    useState:()=>[frame,update=>{frame=typeof update==='function'?update(frame):update}],useEffect:effect=>effects.push(effect),
  }:require(name),module,module.exports,{IntersectionObserver:Observer},documentMock,Observer,callback=>{const id=++timerId;timers.set(id,callback);return id},id=>timers.delete(id))
  const surfaceRef={current:root}
  return { api:module.exports, timers, events, observers, surfaces, document:documentMock,
    frame:()=>frame,
    render(paused=false,reduced=false){
      const result=module.exports.LandingDemoProvider({children:null,paused,reduced,surfaceRef})
      context.value=result.props.value
      return effects.at(-1)
    },
    paint(){context.value={...context.value,frame}},
    select(index){context.value.select(index)},
  }
}
function rootPath(){return root}

test('hero and engine read the same scenario and approval phase from their provider',()=>{
  for(let scenario=0;scenario<3;scenario++) {
    const runtime=demoRuntime({scenario,phase:10})
    runtime.render(true)
    const hero=renderToStaticMarkup(React.createElement(runtime.api.DemoHero))
    const engine=renderToStaticMarkup(React.createElement(runtime.api.DemoEngine))
    for(const text of [DEMO_SCENARIOS[scenario].regime,DEMO_SCENARIOS[scenario].strategy,DEMO_SCENARIOS[scenario].risk,DEMO_SCENARIOS[scenario].outcome]) {
      assert.ok(hero.includes(text),text)
      assert.ok(engine.includes(text),text)
    }
    assert.ok(engine.includes(`data-scenario="${DEMO_SCENARIOS[scenario].id}"`))
    if(scenario>0) assert.doesNotMatch(engine, /data-node="[456]" data-active="true"/)
  }
})
test('single demo timer stops when paused, hidden, offscreen or unmounted; resume preserves phase',()=>{
  const runtime=demoRuntime()
  let cleanup=runtime.render()()
  const visible=()=>runtime.observers.at(-1).callback(runtime.surfaces.map(target=>({target,isIntersecting:true})))
  visible()
  assert.equal(runtime.timers.size,1)
  visible()
  assert.equal(runtime.timers.size,1)
  runtime.timers.values().next().value()
  assert.deepEqual(runtime.frame(),{scenario:0,phase:1})
  cleanup()
  assert.equal(runtime.timers.size,0)
  assert.equal(runtime.events.size,0)
  assert.equal(runtime.render(true)(),undefined)
  assert.equal(runtime.timers.size,0)
  cleanup=runtime.render()()
  visible()
  assert.deepEqual(runtime.frame(),{scenario:0,phase:1})
  runtime.document.hidden=true;runtime.events.get('visibilitychange')()
  assert.equal(runtime.timers.size,0)
  runtime.document.hidden=false;runtime.events.get('visibilitychange')()
  assert.equal(runtime.timers.size,1)
  runtime.observers.at(-1).callback(runtime.surfaces.map(target=>({target,isIntersecting:false})))
  assert.equal(runtime.timers.size,0)
  cleanup()
  assert.ok(runtime.observers.every(observer=>observer.disconnected))
})
test('reduced motion renders a complete static outcome with manual scenario inspection and no timer',()=>{
  const runtime=demoRuntime()
  assert.equal(runtime.render(false,true)(),undefined)
  assert.equal(runtime.timers.size,0)
  let engine=renderToStaticMarkup(React.createElement(runtime.api.DemoEngine))
  assert.match(engine,/data-demo-gate="approved"/)
  assert.match(engine,/POSITION OPEN/)
  runtime.select(2)
  runtime.render(false,true)
  engine=renderToStaticMarkup(React.createElement(runtime.api.DemoEngine))
  assert.match(engine,/data-scenario="stale"/)
  assert.match(engine,/data-demo-gate="held"/)
  assert.match(engine,/CAPITAL PRESERVED/)
  assert.equal(runtime.timers.size,0)
})
