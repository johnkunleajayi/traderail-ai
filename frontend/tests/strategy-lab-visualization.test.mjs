import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync,existsSync} from 'node:fs'
import {createRequire} from 'node:module'
import {dirname,resolve} from 'node:path'
import {fileURLToPath} from 'node:url'
import ts from 'typescript'
import React from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
const root=fileURLToPath(new URL('..',import.meta.url)),require=createRequire(new URL('../package.json',import.meta.url)),cache=new Map()
function load(file){
  const path=resolve(root,file)
  if(cache.has(path))return cache.get(path)
  if(/[/\\]auth[/\\]api.ts$/.test(path))return {apiBase:'http://127.0.0.1:8000'}
  const output=ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText
  const module={exports:{}};cache.set(path,module.exports)
  new Function('require','module','exports',output)(name=>{
    if(!name.startsWith('.'))return require(name)
    const base=resolve(dirname(path),name),target=['.ts','.tsx'].map(ext=>base+ext).find(existsSync)
    if(!target)throw new Error(name);return load(target)
  },module,module.exports)
  cache.set(path,module.exports);return module.exports
}
const {parseResearch,researchEvents,timeKey}=load('src/strategy-lab/research.ts')
const {alignExperimentCandles,readExperimentCandles}=load('src/strategy-lab/candles.ts')
const {candleCoordinates,performanceCoordinates,markerCoordinates,drawdownAxisLabel,chartNumber}=load('src/strategy-lab/chartBoundary.ts')
const Inspection=load('src/strategy-lab/ResearchInspection.tsx').default,Table=load('src/strategy-lab/TradeTable.tsx').default
const {parseExperiment}=load('src/strategy-lab/api.ts')
const render=(C,props)=>renderToStaticMarkup(React.createElement(C,props))
const at=h=>new Date(Date.UTC(2026,9,1)+h*3600000).toISOString()
const close=h=>new Date(Date.UTC(2026,9,1)+(h+1)*3600000-1).toISOString()
function fixture(){
  const candles=Array.from({length:58},(_,i)=>({canonical_symbol:'BTC/USDT',provider:'BINANCE',timeframe:'1h',open_time:at(i),close_time:close(i),retrieved_at:at(60),open:String(100+i),high:String(101+i),low:String(99+i),close:`${100+i}.5`,volume:'1.000000000000000001'}))
  const meta={canonical_symbol:'BTC/USDT',provider:'BINANCE',timeframe:'1h',requested_candle_count:58,candle_count:58,warmup_start:at(0),warmup_candle_count:52,required_warmup_candles:52,evaluation_start:at(52),evaluation_end:at(58),evaluation_candle_count:6,
    strategy_family:'ADAPTIVE_TREND',strategy_version:'1.0.0',experiment_identity:'experiment',dataset_identity:'dataset',research_result_identity:'research',strategy_configuration_identity:'strategy',initial_capital:'10000',quantity:'0.001',execution:{fee_rate:'0',full_spread_rate:'0',slippage_rate:'0',delay_candles:0,timing:'NEXT_OPEN',cost_model_identity:'cost',execution_configuration_identity:'execution'}}
  const action=['ENTER_LONG','NO_ACTION','EXIT_LONG','NO_ACTION','ENTER_SHORT','EXIT_SHORT']
  const before=['CASH','LONG','LONG','CASH','CASH','SHORT'],after=['LONG','LONG','CASH','CASH','SHORT','CASH']
  const decisions=action.map((decision,i)=>({open_time:at(52+i),timestamp:close(52+i),decision_identity:`d${i}`,decision,reason_codes:['REASON_'+i],position_before:before[i],position_after:after[i]}))
  const regimes=decisions.map((d,i)=>({open_time:d.open_time,timestamp:d.timestamp,regime:i<2?'UNKNOWN':'BULL_TREND',candidate_regime:i<2?'RANGE':'BULL_TREND',confirmation_count:i<2?2:55,confirmation_required:3,retained_previous:i===3,previous_regime:i===3?'RANGE':null,reason_codes:['REGIME_REASON']}))
  const equity_curve=decisions.map((d,i)=>({timestamp:d.timestamp,cash:'10000.000000000000000001',realized_capital:'10000',realized_pnl:'0',unrealized_pnl:'0.000000000000000123',total_equity:'10000.1234567890123456789',drawdown:i===0?null:i===1?'0':'0.00259252',position_state:['CASH','LONG','LONG','CASH','CASH','SHORT'][i]}))
  const trades=[{trade_identity:'t1',direction:'LONG',entry_decision_time:close(52),entry_fill_time:at(53),entry_reference_price:'153',entry_fill_price:'153.1',exit_decision_time:close(54),exit_fill_time:at(55),exit_reference_price:'155',exit_fill_price:'154.9',quantity:'0.001',gross_pnl:'0.002',fees:'0.0001',spread_cost:'0.0001',slippage_cost:'0.0001',net_pnl:'0.0017',entry_reason_codes:['LONG_TREND_CONFIRMED'],exit_reason_codes:['LONG_TREND_INVALIDATED']}]
  const open_position={direction:'SHORT',quantity:'0.001',entry_decision_time:close(56),entry_fill_time:at(57),entry_reference_price:'157',entry_price:'156.9',entry_fee_recognized:'0.0001',mark_price:'157.5',mark_timestamp:close(57),unrealized_pnl:'-0.0006',reserved_capital:'0.157',restricted_short_proceeds:'0.1569',entry_reason_codes:['SHORT_TREND_CONFIRMED']}
  const unfilled_decisions=[{decision_identity:'d5',decision:'EXIT_SHORT',decision_time:close(57),eligible_fill_time:at(58),filled_state:'SHORT',reason_code:'END_OF_DATASET'}]
  const raw={decisions,regimes,equity_curve,trades,open_position,unfilled_decisions}
  return {meta,candles,raw,data:parseResearch(raw,meta,1)}
}
const originalFetch=globalThis.fetch
test.afterEach(()=>{globalThis.fetch=originalFetch})
test('full backend rows retain exact financial strings and chronological context',()=>{
  const {data,raw}=fixture()
  assert.equal(data.equity_curve[0].total_equity,raw.equity_curve[0].total_equity)
  assert.equal(data.trades[0].net_pnl,'0.0017');assert.equal(data.open_position.direction,'SHORT')
  assert.equal(data.regimes[3].confirmation_count,55) // Streak may exceed its requirement.
})
test('UNKNOWN with no confirmation requirement remains valid and explicit',()=>{
  const f=fixture();Object.assign(f.raw.regimes[0],{regime:'UNKNOWN',candidate_regime:'UNKNOWN',confirmation_count:0,confirmation_required:0})
  assert.equal(parseResearch(f.raw,f.meta,1).regimes[0].regime,'UNKNOWN')
})
test('real stored window is read through TradeRail only, without ingestion',async()=>{
  const f=fixture();globalThis.fetch=async(url,opts)=>{
    const u=new URL(url);assert.equal(u.origin,'http://127.0.0.1:8000')
    assert.equal(decodeURIComponent(u.pathname),'/api/market-data/stored-candles/BTC/USDT')
    assert.equal(u.searchParams.get('limit'),'1000');assert.equal(u.searchParams.get('provider'),'BINANCE')
    assert.equal(u.searchParams.get('timeframe'),'1h');assert.equal(opts.credentials,'omit');assert.equal(opts.method,undefined)
    return new Response(JSON.stringify(f.candles))
  }
  assert.deepEqual(await readExperimentCandles(f.meta,f.data),f.candles)
})
test('candlestick coordinates use real selected OHLC without mutating decimals',()=>{
  const f=fixture(),evaluation=alignExperimentCandles(f.candles,f.meta,f.data).slice(52)
  const copy=JSON.stringify(evaluation),points=candleCoordinates(evaluation)
  assert.equal(points.length,6);assert.equal(points[0].open,152);assert.equal(points[0].close,152.5)
  assert.equal(points[0].time,Date.parse(at(52))/1000);assert.equal(JSON.stringify(evaluation),copy)
})
for(const [name,mutate] of [
  ['wrong symbol',f=>{f.candles[0].canonical_symbol='ETH/USDT'}],
  ['wrong provider',f=>{f.candles[0].provider='QUIDAX'}],
  ['wrong timeframe',f=>{f.candles[0].timeframe='4h'}],
  ['missing candle',f=>{f.candles.splice(20,1)}],
  ['duplicate candle',f=>{f.candles[20]=f.candles[19]}],
  ['unordered candle',f=>{f.candles.reverse()}],
  ['close timestamp drift',f=>{f.candles[52].close_time=f.candles[52].close_time.replace('.999Z','.999001Z')}],
  ['changed reference price',f=>{f.candles[53].open='999'}],
  ['changed final mark',f=>{f.candles[57].close='99'}],
])test(`stored ${name} hides candle/marker view rather than shifting or replacing`,()=>{const f=fixture();mutate(f);assert.throws(()=>alignExperimentCandles(f.candles,f.meta,f.data))})
test('historical window evicted from latest stored read is explicitly unavailable',()=>{
  const f=fixture();assert.throws(()=>alignExperimentCandles(f.candles.slice(10),f.meta,f.data),/do not match/)
})
test('stored read failure never invents candles',async()=>{
  const f=fixture();globalThis.fetch=async()=>new Response('unavailable',{status:503})
  await assert.rejects(readExperimentCandles(f.meta,f.data),/No replacement/)
})
test('all four decisions are mapped, NO_ACTION has no marker, and fills are separate',()=>{
  const f=fixture(),events=researchEvents(f.data)
  assert.deepEqual(events.filter(e=>e.kind==='decision').map(e=>e.action),['ENTER_LONG','EXIT_LONG','ENTER_SHORT','EXIT_SHORT'])
  assert.equal(events.filter(e=>e.kind==='fill').length,3)
  assert.equal(events.find(e=>e.id==='d0').candle_open_time,at(52))
  assert.equal(events.find(e=>e.id==='d0').timestamp,close(52))
  assert.equal(events.find(e=>e.id==='d0:fill').timestamp,at(53))
  assert.equal(events.find(e=>e.id==='d0:fill').price,'153.1')
})
test('fill marker uses supplied delayed timestamp, not decision-close execution',()=>{
  const f=fixture();f.data.trades[0].entry_fill_time=at(54)
  const events=researchEvents(f.data),fill=events.find(e=>e.id==='d0:fill')
  assert.equal(fill.candle_open_time,at(54));assert.notEqual(fill.candle_open_time,at(52))
})
test('arrows versus fill circles and unfilled squares carry non-color status text',()=>{
  const markers=markerCoordinates(researchEvents(fixture().data),name=>name)
  assert.equal(markers.find(m=>m.id==='d0').shape,'arrowUp')
  assert.equal(markers.find(m=>m.id==='d0:fill').shape,'circle')
  assert.equal(markers.find(m=>m.id==='d0:fill').position,'atPriceMiddle')
  assert.equal(markers.find(m=>m.id==='d0:fill').price,153.1)
  assert.equal(markers.find(m=>m.id==='d5').shape,'square');assert.match(markers.find(m=>m.id==='d5').text,/unfilled/)
  assert.equal(markers.some(m=>m.id==='d5:fill'),false)
})
test('open short has its entry fill and mark but no fabricated exit',()=>{
  const f=fixture(),events=researchEvents(f.data)
  assert.equal(events.find(e=>e.id==='d4:fill').status,'Open position')
  assert.equal(events.filter(e=>e.kind==='fill'&&e.action==='EXIT_SHORT').length,0)
  const html=render(Inspection,{data:f.data,candles:f.candles.slice(52),selected:at(57),onSelect(){}})
  assert.match(html,/EXIT_SHORT/);assert.match(html,/Unfilled/);assert.match(html,/SHORT/)
})
test('backend equity and drawdown observations are used without deriving metrics',()=>{
  const f=fixture(),before=JSON.stringify(f.data),points=performanceCoordinates(f.data)
  assert.equal(points.equity[0].time,Date.parse(close(52))/1000)
  assert.equal(points.equity[0].value,Number(f.data.equity_curve[0].total_equity))
  assert.deepEqual(points.drawdown[0],{time:Date.parse(close(52))/1000})
  assert.equal(points.drawdown[1].value,0);assert.equal(points.drawdown[2].value,-0.00259252)
  assert.equal(JSON.stringify(f.data),before)
  assert.equal(drawdownAxisLabel(0),'0.00%');assert.equal(drawdownAxisLabel(-0.01),'-1.00%')
})
test('extreme coordinates fail safely while exact strings remain intact',()=>{
  for(const value of ['1'+'0'.repeat(350),'0.'+'0'.repeat(350)+'1'])assert.throws(()=>chartNumber(value),/rendering limits/)
})
test('completed-trade table has semantic columns and accessible exact cost/reason inspection',()=>{
  const f=fixture(),html=render(Table,{trades:f.data.trades,onInspect(){}})
  for(const text of ['Entry decision','Entry fill','Exit decision','Exit fill','Quantity','Gross P&amp;L','Fees','Spread cost','Slippage cost','Net P&amp;L','LONG_TREND_CONFIRMED','LONG_TREND_INVALIDATED'])assert.ok(html.includes(text),text)
  assert.match(html,/<table>/);assert.match(html,/<caption>/);assert.match(html,/scope="col"/);assert.match(html,/scope="row"/)
  assert.match(html,/tabindex="0"/);assert.match(html,/>0.0017<\/dd>/);assert.match(html,/>153.1<\/dd>/)
})
test('zero completed trades has an intentional empty state and no fake table rows',()=>{
  const html=render(Table,{trades:[],onInspect(){}})
  assert.match(html,/No completed trades/);assert.doesNotMatch(html,/<tbody>|No profit/)
})
test('inspection preserves exact OHLC, cash/equity, null drawdown, reasons, and UNKNOWN',()=>{
  const f=fixture(),html=render(Inspection,{data:f.data,candles:f.candles.slice(52),selected:at(52),onSelect(){}})
  for(const text of ['UNKNOWN','RANGE','2 / 3','REASON_0','REGIME_REASON','10000.1234567890123456789','10000.000000000000000001','Unavailable','Strategy decision','Executed'])assert.ok(html.includes(text),text)
  assert.match(html,/for="lab-inspect-candle"/);assert.match(html,/<select/);assert.match(html,/Replay thesis state/);assert.match(html,/Accounting position/)
})
test('retained regime and differing replay/accounting state remain explicit',()=>{
  const f=fixture(),html=render(Inspection,{data:f.data,candles:null,selected:at(55),onSelect(){}})
  assert.match(html,/Retained previous regime/);assert.match(html,/Previous: RANGE/);assert.match(html,/stored candle view not aligned/)
})
for(const [name,mutate] of [
 ['numeric money',f=>{f.raw.equity_curve[0].total_equity=10000}],
 ['missing row',f=>{f.raw.equity_curve.pop()}],
 ['misaligned regime',f=>{f.raw.regimes[0].open_time=at(53)}],
 ['timestamp drift',f=>{f.raw.decisions[0].timestamp=f.raw.decisions[0].timestamp.replace('.999Z','.999001Z')}],
 ['unmatched fill',f=>{f.raw.trades[0].entry_decision_time=close(53)}],
 ['same-close fill',f=>{f.raw.trades[0].entry_fill_time=close(52)}],
 ['hidden unfilled',f=>{f.raw.unfilled_decisions=[]}],
 ['duplicate identity',f=>{f.raw.decisions[1].decision_identity='d0'}],
 ['invented regime',f=>{f.raw.regimes[0].regime='HOLD'}],
 ['out-of-window',f=>{f.raw.decisions[0].open_time=at(51)}],
])test(`malformed research ${name} cannot become visual evidence`,()=>{const f=fixture();mutate(f);assert.throws(()=>parseResearch(f.raw,f.meta,1))})
test('malformed detail data preserves successful Part 1 summary but disables visualization',()=>{
  const f=fixture(),raw={...f.raw,experiment:f.meta,summary:{initial_capital:'10000',ending_equity:'10000',total_return:'0',gross_return:'0',net_return:'0',max_drawdown:'0',completed_trade_count:1,win_rate:null,profit_factor:null,expectancy:null,exposure:'0',turnover:'0',fee_total:'0',spread_cost_total:'0',slippage_cost_total:'0'}}
  const valid=parseExperiment(raw,f.meta);assert.equal(valid.research.trades.length,1)
  delete raw.equity_curve
  const result=parseExperiment(raw,f.meta);assert.equal(result.summary.ending_equity,'10000');assert.equal(result.research,null);assert.match(result.research_error,/no replacement/)
})
test('UTC identity retains microseconds, never shifts classifications to nearby candles',()=>{
  assert.notEqual(timeKey('2026-10-01T00:00:00.999001Z'),timeKey('2026-10-01T00:00:00.999Z'))
  assert.equal(timeKey('2026-10-01T00:00:00Z'),timeKey('2026-10-01T00:00:00.000000+00:00'))
})
test('responsive table is a bounded scroll region and keyboard/chart fallbacks remain present',()=>{
  const css=readFileSync(resolve(root,'src/styles.css'),'utf8').split('/* Strategy Lab experiment inspection.')[1]
  assert.match(css,/\.lab-table-scroll \{[^}]*max-width:100%;[^}]*overflow-x:auto/)
  assert.match(css,/max-width:540px/);assert.match(css,/:focus-visible/)
  const source=readFileSync(resolve(root,'src/strategy-lab/ResearchCharts.tsx'),'utf8')
  assert.match(source,/role="img"/);assert.match(source,/MutationObserver/);assert.match(source,/autoSize:true/)
  assert.match(source,/subscribeCrosshairMove/);assert.match(source,/subscribeVisibleLogicalRangeChange/)
  assert.match(source,/getVisibleLogicalRange/);assert.match(source,/observer.disconnect/);assert.match(source,/chart.remove/)
})
test('new views have no provider calls, no ingestion, no financial formulas outside chart coordinates',()=>{
  for(const file of ['research.ts','candles.ts','ResearchCharts.tsx','ResearchInspection.tsx','TradeTable.tsx','ExperimentVisualization.tsx']){
    const source=readFileSync(resolve(root,'src/strategy-lab',file),'utf8')
    assert.doesNotMatch(source,/binance\.com|quidax\.io|candle-ingestions|ingestHistory|parseFloat\(|\.toFixed\(|Number\((?:r\.|c\.|e\.|p\.|raw)/)
    assert.doesNotMatch(source,/maximum_drawdown|realized_pnl_aggregation|demoCandles|fakePrices/)
  }
})


test('completed short has EXIT_SHORT execution while final open long has no fabricated exit',()=>{
  const f=fixture(),swap=s=>s.replace('LONG','TEMP').replace('SHORT','LONG').replace('TEMP','SHORT')
  for(const d of f.raw.decisions){d.decision=swap(d.decision);d.position_before=swap(d.position_before);d.position_after=swap(d.position_after)}
  for(const e of f.raw.equity_curve)e.position_state=swap(e.position_state)
  Object.assign(f.raw.trades[0],{direction:'SHORT',entry_fill_price:'152.9',exit_fill_price:'155.1'})
  Object.assign(f.raw.open_position,{direction:'LONG',entry_price:'157.1'})
  Object.assign(f.raw.unfilled_decisions[0],{decision:'EXIT_LONG',filled_state:'LONG'})
  const data=parseResearch(f.raw,f.meta,1),events=researchEvents(data),markers=markerCoordinates(events,n=>n)
  assert.equal(events.find(e=>e.id==='d2:fill').action,'EXIT_SHORT')
  assert.equal(markers.find(e=>e.id==='d2:fill').price,155.1)
  assert.equal(data.open_position.direction,'LONG')
  assert.equal(events.some(e=>e.kind==='fill'&&e.action==='EXIT_LONG'),false)
})

test('chart wiring creates three real series, initial-capital line, synchronized inspection and disposes resources',()=>{
  const f=fixture(),effects=[],created=[],observers=[],selected=[]
  let color='theme-one',errors=[]
  const oldStyle=globalThis.getComputedStyle,oldObserver=globalThis.MutationObserver,oldDocument=globalThis.document
  const mockReact={useRef:initial=>({current:initial===null?{}:initial}),useState:initial=>[initial,value=>errors.push(value)],useEffect:effect=>effects.push(effect)}
  const makeChart=()=>{
    const chart={options:[],series:[],range:null,removed:false,crosshair:null,rangeCallback:null,move:null,
      applyOptions(o){this.options.push(o)},remove(){this.removed=true},
      addSeries(kind,options){const s={kind,options,rows:null,lines:[],applyOptions(o){this.options={...this.options,...o}},setData(rows){this.rows=rows},createPriceLine(line){const item={...line,applyOptions(o){Object.assign(this,o)}};this.lines.push(item);return item}};this.series.push(s);return s},
      timeScale(){return {getVisibleLogicalRange:()=>this.range,fitContent:()=>{this.range={from:0,to:5}},subscribeVisibleLogicalRangeChange:cb=>{this.rangeCallback=cb},setVisibleLogicalRange:range=>{this.range=range;this.rangeCallback?.(range)}}},
      subscribeCrosshairMove(cb){this.move=cb},setCrosshairPosition(value,time,series){this.crosshair={value,time,series}},clearCrosshairPosition(){this.crosshair=null}}
    created.push(chart);return chart
  }
  const lwc={createChart:makeChart,ColorType:{Solid:'solid'},CandlestickSeries:'candles',LineSeries:'line',AreaSeries:'area',createSeriesMarkers:(series,markers)=>{series.markers=markers;return {setMarkers:rows=>{series.markers=rows}}}}
  const path=resolve(root,'src/strategy-lab/ResearchCharts.tsx'),output=ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText
  const module={exports:{}}
  new Function('require','module','exports',output)(name=>name==='react'?mockReact:name==='lightweight-charts'?lwc:name.startsWith('.')?load(resolve(dirname(path),name)+'.ts'):require(name),module,module.exports)
  try{
    globalThis.getComputedStyle=()=>({getPropertyValue:()=>color})
    globalThis.document={documentElement:{}}
    globalThis.MutationObserver=class{constructor(cb){this.cb=cb;this.disconnected=false;observers.push(this)}observe(){}disconnect(){this.disconnected=true}}
    module.exports.default({candles:f.candles.slice(52),data:f.data,initialCapital:'10000',selected:at(53),onSelect:time=>selected.push(time)})
    const cleanup=effects[0]();effects[1]()
    assert.deepEqual(errors,['']);assert.equal(created.length,3)
    assert.equal(created[0].series[0].kind,'candles');assert.equal(created[0].series[0].rows[0].open,152)
    assert.equal(created[1].series[0].kind,'line');assert.equal(created[1].series[0].lines[0].price,10000)
    assert.equal(created[2].series[0].kind,'area');assert.equal(created[2].series[0].rows[0].value,undefined)
    assert.equal(created[0].series[0].markers.length,7)
    assert.equal(created[0].crosshair.time,Date.parse(at(53))/1000)
    assert.equal(created[1].crosshair.time,Date.parse(close(53))/1000)
    created[0].move({time:Date.parse(at(54))/1000});assert.equal(selected.at(-1),at(54))
    created[1].move({time:Date.parse(close(55))/1000});assert.equal(selected.at(-1),at(55))
    created[0].rangeCallback({from:1,to:3});assert.deepEqual(created[1].range,{from:1,to:3});assert.deepEqual(created[2].range,{from:1,to:3})
    const before=JSON.stringify(created.map(c=>c.series[0].rows));color='theme-two';observers[0].cb()
    assert.equal(created[1].series[0].options.color,'theme-two');assert.equal(JSON.stringify(created.map(c=>c.series[0].rows)),before)
    cleanup();assert.ok(created.every(c=>c.removed));assert.ok(observers.every(o=>o.disconnected))
  }finally{globalThis.getComputedStyle=oldStyle;globalThis.MutationObserver=oldObserver;globalThis.document=oldDocument}
})
