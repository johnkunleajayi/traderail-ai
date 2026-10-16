import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import ts from 'typescript'
import React from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
const load=async(path,replacements=[])=>{
 const source=await readFile(new URL(path,import.meta.url),'utf8')
 let output=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX}}).outputText
 for(const [pattern,value] of replacements)output=output.replace(pattern,value)
 const url=`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`
 return {module:await import(url),url}
}
const api=await load('../src/history/regimes.ts',[
 [/import \{ apiBase \} from ['"]..\/auth\/api['"];?/,"const apiBase='http://127.0.0.1:8000';"],
 [/import \{ historyLimit \} from ['"].\/api['"];?/,'const historyLimit=100;'],
])
const format=await load('../src/markets/format.ts')
const view=await load('../src/components/RegimeSummary.tsx',[
 [/from ['"]react\/jsx-runtime['"]/g,`from '${import.meta.resolve('react/jsx-runtime')}'`],
 [/from ['"]..\/markets\/format['"]/g,`from '${format.url}'`],
])
const strip=await load('../src/history/regimeChart.ts',[[/from ['"]lightweight-charts['"]/g,`from '${import.meta.resolve('lightweight-charts')}'`]])
const row=(i=0,regime='UNKNOWN',candidate='UNKNOWN')=>{
 const confirmation={count:i?2:0,required:i?3:0,previous_regime:null,retained_previous:false,available_candles:i+1,candidate_history_required:53}
 const reason_codes=i?['RANGE_STRUCTURE','CONFIRMATION_PENDING']:['WARMUP_REQUIRED','INSUFFICIENT_EVIDENCE']
 return {open_time:new Date(Date.UTC(2026,9,16,i)).toISOString(),close_time:new Date(Date.UTC(2026,9,16,i+1)-1).toISOString(),regime,candidate_regime:candidate,confirmation,reason_codes,
 evidence:{...Object.fromEntries(api.module.regimeEvidenceFields.map(field=>[field,i?'0.0012345678901234567890123456789':null])),candidate,reason_codes,confirmation_count:confirmation.count,confirmation_required:confirmation.required,retained_previous:false,previous_regime:null,available_candles:i+1,candidate_history_required:53,breakout_direction:null}}
}
const dataset=(symbol='BTC/USDT',timeframe='1h')=>{
 const regimes=[row(),row(1,'UNKNOWN','RANGE')]
 return {canonical_symbol:symbol,base_asset:symbol.split('/')[0],quote_asset:symbol.split('/')[1],provider:'BINANCE',timeframe,candle_count:2,dataset_start:regimes[0].open_time,dataset_end:regimes[1].close_time,regime_engine_version:'1.0.0',feature_engine_version:'1.0.0',regime_configuration:{engine_version:'1.0.0',persistence_window:3,trend_spread:'0.0015'},regimes,latest_regime:regimes[1]}
}
const candles=d=>d.regimes.map(row=>({...row,canonical_symbol:d.canonical_symbol,provider:d.provider,timeframe:d.timeframe,open:'10',high:'12',low:'9',close:'11',volume:'1',retrieved_at:'2026-10-17T00:00:00Z'}))
const response=(data,status=200)=>new Response(JSON.stringify(data),{status})
const originalFetch=globalThis.fetch
test.afterEach(()=>globalThis.fetch=originalFetch)
for(const symbol of ['BTC/USDT','ETH/USDT'])for(const timeframe of ['15m','1h','4h','1d'])test(`regime API uses canonical TradeRail route ${symbol} ${timeframe}`,async()=>{
 const d=dataset(symbol,timeframe)
 globalThis.fetch=async(url,options)=>{
  const parsed=new URL(url)
  assert.equal(parsed.origin,'http://127.0.0.1:8000');assert.equal(parsed.pathname,`/api/market-regimes/${symbol}`)
  assert.equal(parsed.searchParams.get('provider'),'BINANCE');assert.equal(parsed.searchParams.get('limit'),'100');assert.equal(parsed.searchParams.get('timeframe'),timeframe)
  assert.equal(options.credentials,'omit');assert.equal(options.cache,'no-store')
  return response(d)
 }
 const result=await api.module.readRegimes(symbol,timeframe)
 assert.deepEqual(result,d);assert.equal(result.regimes[1].evidence.normalized_atr,'0.0012345678901234567890123456789')
 assert.equal(api.module.alignRegimes(candles(d),result).size,2)
})
for(const status of [404,409,422,503])test(`regime HTTP ${status} is unavailable, not inferred UNKNOWN`,async()=>{
 globalThis.fetch=async()=>response({},status)
 await assert.rejects(api.module.readRegimes('BTC/USDT','1h'),/unavailable|could not be loaded/)
})
for(const mutate of [d=>d.regimes.reverse(),d=>d.provider='QUIDAX',d=>d.canonical_symbol='ETH/USDT',d=>d.timeframe='4h',d=>d.candle_count=3,d=>d.regimes[1].evidence.rsi_14=1.1,d=>d.regimes[1].regime='HOLD',d=>d.latest_regime={...d.latest_regime,regime:'RANGE'},d=>d.regimes[1].confirmation.count=999,d=>d.regimes=[],d=>d.regimes[1].open_time=d.regimes[0].open_time])test('malformed regime response fails closed',async()=>{
 const d=dataset();mutate(d);globalThis.fetch=async()=>response(d)
 await assert.rejects(api.module.readRegimes('BTC/USDT','1h'),/could not be read/)
})
for(const mutate of [c=>c.pop(),c=>c[1].open_time='2026-10-16T02:00:00Z',c=>c[1].close_time=c[0].close_time,c=>c[1].canonical_symbol='ETH/USDT',c=>c[1].timeframe='4h',c=>c[1].provider='QUIDAX',c=>c[1]={...c[0]}])test('canonical count/context/timestamp mismatch hides bands without shifting data',()=>{
 const d=dataset(),c=candles(d);mutate(c)
 assert.throws(()=>api.module.alignRegimes(c,d),/do not align/)
})
test('latest summary is the API final row, including UNKNOWN candidate and progress',()=>{
 const html=renderToStaticMarkup(React.createElement(view.module.default,{data:dataset(),error:''}))
 assert.match(html,/Market Regime/);assert.match(html,/UNKNOWN/);assert.match(html,/RANGE/);assert.match(html,/2 \/ 3/)
 assert.match(html,/Engine v1.0.0/);assert.match(html,/RANGE_STRUCTURE/);assert.match(html,/CONFIRMATION_PENDING/)
 assert.doesNotMatch(html,/HOLD|BUY|SELL/)
})
test('UNSAFE and retention are transported honestly with readable exact evidence',()=>{
 const d=dataset();const r=d.latest_regime;r.regime='UNSAFE';r.candidate_regime='UNSAFE';r.evidence.candidate='UNSAFE'
 r.reason_codes=['DATA_UNSAFE'];r.evidence.reason_codes=r.reason_codes
 const html=renderToStaticMarkup(React.createElement(view.module.default,{data:d,error:''}))
 assert.match(html,/UNSAFE/);assert.match(html,/DATA_UNSAFE/)
 r.regime='BULL_TREND';r.candidate_regime='BEAR_TREND';r.confirmation.retained_previous=true;r.confirmation.previous_regime='BULL_TREND'
 const inspected=renderToStaticMarkup(React.createElement(view.module.RegimeInspection,{row:r}))
 assert.match(inspected,/Retained previous regime/);assert.match(inspected,/BEAR_TREND/)
 assert.match(inspected,/Exact: 0.0012345678901234567890123456789/);assert.doesNotMatch(inspected,/\{&quot;/)
})
test('warm-up UNKNOWN stays visible, not zero or a renamed category',()=>{
 const html=renderToStaticMarkup(React.createElement(view.module.RegimeInspection,{row:row()}))
 assert.match(html,/UNKNOWN/);assert.match(html,/Confirmation: Unavailable/);assert.match(html,/WARMUP_REQUIRED/);assert.match(html,/Unavailable/)
})
test('loading and error are explicit and contain no invented classification',()=>{
 const loading=renderToStaticMarkup(React.createElement(view.module.default,{data:null,error:'',loading:true}))
 assert.match(loading,/Loading market regimes/);assert.doesNotMatch(loading,/UNKNOWN|BULL_TREND/)
 const error=renderToStaticMarkup(React.createElement(view.module.default,{data:null,error:'Regimes do not align'}))
 assert.match(error,/Regimes do not align/);assert.doesNotMatch(error,/UNKNOWN/)
})
test('categorical strip retains exact candle times, all seven categories, and supports theme-only updates',()=>{
 const rows=api.module.regimeNames.map((regime,i)=>row(i,regime,regime));let data;let mode='dark';let added=0
 const chart={panes:()=>[{setStretchFactor(){}},{setStretchFactor(){}}],addSeries(definition,options,index){added++;assert.equal(index,2);return {setData(value){data=value},priceScale:()=>({applyOptions(){}})}}}
 // Return the newly created pane after addSeries, just as Lightweight Charts does.
 chart.panes=()=>Array.from({length:added?3:2},()=>({setStretchFactor(){}}))
 const theme=strip.module.addRegimeStrip(chart,rows,token=>`${mode}:${token}`)
 assert.deepEqual(data.map(p=>p.time),rows.map(r=>Date.parse(r.open_time)/1000))
 assert.ok(data.every(p=>p.value===1));assert.equal(new Set(data.map(p=>p.color)).size,7)
 mode='light';theme();assert.ok(data.every(p=>p.color.startsWith('light:')))
})
test('toggle and lifecycle are local presentation state, with no duplicated regime rules or provider requests',async()=>{
 const history=await readFile(new URL('../src/components/MarketHistory.tsx',import.meta.url),'utf8')
 const chart=await readFile(new URL('../src/components/HistoryChart.tsx',import.meta.url),'utf8')
 const client=await readFile(new URL('../src/history/regimes.ts',import.meta.url),'utf8')
 assert.equal((history.match(/readRegimes\(symbol, timeframe, signal\)/g)||[]).length,1)
 assert.match(chart,/setShowRegimes\(value=>!value\)/);assert.match(chart,/showRegimes/)
 assert.doesNotMatch(chart,/fetch\(|readRegimes\(/)
 assert.doesNotMatch(client,/https:\/\/|Math\.(log|sqrt)|trend_spread\s*[<>]/)
 assert.match(history,/await refresh\(request.signal\)/)
})


test('impossible confirmation metadata and sub-millisecond timestamp drift are rejected',async()=>{
 for(const patch of [d=>{d.regimes[1].confirmation.count=999;d.regimes[1].evidence.confirmation_count=999},d=>{d.regimes[1].open_time='2026-10-16T01:00:00.000001Z'},d=>{d.regimes[1].confirmation.retained_previous=true;d.regimes[1].evidence.retained_previous=true}]){
  const d=dataset();patch(d);globalThis.fetch=async()=>response(d)
  await assert.rejects(api.module.readRegimes('BTC/USDT','1h'),/could not be read/)
 }
})
