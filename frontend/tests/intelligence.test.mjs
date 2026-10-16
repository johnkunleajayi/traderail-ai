import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const load = async (path, replacements = []) => {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  let output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX } }).outputText
  for (const [pattern,value] of replacements) output=output.replace(pattern,value)
  const url=`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`
  return { module:await import(url),url }
}
const intelligence=await load('../src/history/intelligence.ts', [
  [/import \{ apiBase \} from ['"]..\/auth\/api['"];?/, "const apiBase='http://127.0.0.1:8000';"],
  [/import \{ historyLimit \} from ['"].\/api['"];?/, 'const historyLimit=100;'],
])
const charts=await load('../src/history/intelligenceChart.ts', [[/from ['"]lightweight-charts['"]/g,`from '${import.meta.resolve('lightweight-charts')}'`]])
const formatting=await load('../src/markets/format.ts')
const regimeApi=await load('../src/history/regimes.ts', [
  [/import \{ apiBase \} from ['"]..\/auth\/api['"];?/, "const apiBase='http://127.0.0.1:8000';"],
  [/import \{ historyLimit \} from ['"].\/api['"];?/, 'const historyLimit=100;'],
])
const regimeCharts=await load('../src/history/regimeChart.ts', [[/from ['"]lightweight-charts['"]/g,`from '${import.meta.resolve('lightweight-charts')}'`]])
const regimeView=await load('../src/components/RegimeSummary.tsx', [
  [/from ['"]react\/jsx-runtime['"]/g,`from '${import.meta.resolve('react/jsx-runtime')}'`],
  [/from ['"]..\/markets\/format['"]/g,`from '${formatting.url}'`],
])
const component=await load('../src/components/HistoryChart.tsx', [
  [/from ['"]..\/history\/regimes['"]/g,`from '${regimeApi.url}'`],
  [/from ['"]..\/history\/regimeChart['"]/g,`from '${regimeCharts.url}'`],
  [/from ['"].\/RegimeSummary['"]/g,`from '${regimeView.url}'`],
  [/from ['"]..\/markets\/format['"]/g,`from '${formatting.url}'`],
  [/from ['"]react['"]/g,`from '${import.meta.resolve('react')}'`],
  [/from ['"]react\/jsx-runtime['"]/g,`from '${import.meta.resolve('react/jsx-runtime')}'`],
  [/from ['"]lightweight-charts['"]/g,`from '${import.meta.resolve('lightweight-charts')}'`],
  [/from ['"]..\/history\/intelligence['"]/g,`from '${intelligence.url}'`],
  [/from ['"]..\/history\/intelligenceChart['"]/g,`from '${charts.url}'`],
])
const fields=intelligence.module.measurementFields
const row=(index=0)=>({open_time:new Date(Date.UTC(2026,9,15,index)).toISOString(),close_time:new Date(Date.UTC(2026,9,15,index+1)-1).toISOString(),close:'11',...Object.fromEntries(fields.map(field=>[field,index===0?null:'1.12345678901234567890123456789']))})
const dataset=(symbol='BTC/USDT',timeframe='1h')=>({canonical_symbol:symbol,provider:'BINANCE',timeframe,candle_count:2,first_open_time:row(0).open_time,latest_close_time:row(1).close_time,feature_configuration:{engine_version:'1.0.0',annualization:false,realized_volatility_window:20},features:[row(0),row(1)]})
const candles=data=>data.features.map(row=>({...row,canonical_symbol:data.canonical_symbol,provider:data.provider,timeframe:data.timeframe,open:'10',high:'12',low:'9',volume:'1',retrieved_at:'2026-10-16T00:00:00Z'}))
const originalFetch=globalThis.fetch
const response=(data,status=200)=>new Response(JSON.stringify(data),{status})
test.afterEach(()=>{globalThis.fetch=originalFetch})

for (const symbol of ['BTC/USDT','ETH/USDT']) for (const timeframe of ['15m','1h','4h','1d']) {
  test(`TradeRail intelligence request ${symbol} ${timeframe}`,async()=>{
    const data=dataset(symbol,timeframe)
    globalThis.fetch=async(url,options)=>{
      const parsed=new URL(url)
      assert.equal(parsed.origin,'http://127.0.0.1:8000')
      assert.equal(decodeURIComponent(parsed.pathname),`/api/market-intelligence/${symbol}`)
      assert.equal(parsed.searchParams.get('timeframe'),timeframe)
      assert.equal(parsed.searchParams.get('provider'),'BINANCE')
      assert.equal(parsed.searchParams.get('limit'),'100')
      assert.equal(options.credentials,'omit');assert.equal(options.cache,'no-store')
      return response(data)
    }
    const actual=await intelligence.module.readIntelligence(symbol,timeframe)
    assert.deepEqual(actual,data)
    assert.equal(actual.features[0].ema_50,null)
    assert.equal(actual.features[1].ema_20,'1.12345678901234567890123456789')
  })
}
for(const status of [404,409,422,503]) test(`controlled intelligence error ${status}`,async()=>{
  globalThis.fetch=async()=>response({},status)
  await assert.rejects(intelligence.module.readIntelligence('BTC/USDT','1h'),/unavailable|could not be loaded/)
})
for(const mutate of [data=>data.features=[],data=>data.features[1].rsi_14=1.1,data=>data.provider='QUIDAX',data=>data.features[1].atr_14='NaN',data=>data.features[1].open_time=data.features[0].open_time,data=>data.latest_close_time=data.first_open_time]) {
  test('malformed/empty/wrong-source intelligence is not converted to invented data',async()=>{
    const data=dataset();mutate(data);globalThis.fetch=async()=>response(data)
    await assert.rejects(intelligence.module.readIntelligence('BTC/USDT','1h'))
  })
}
test('alignment joins canonical timestamps and retains original decimal strings',()=>{
  const data=dataset();const stored=candles(data)
  const result=intelligence.module.alignIntelligence(stored,data)
  assert.equal(result.get(Date.parse(stored[1].open_time)).ema_20,data.features[1].ema_20)
  stored[0].open_time=stored[0].open_time.replace('Z','+00:00')
  assert.equal(intelligence.module.alignIntelligence(stored,data).size,2)
})
for (const mutate of [stored=>stored.pop(),stored=>stored[1].open_time='2026-10-15T02:00:00Z',stored=>stored[1].close='99',stored=>stored[1].close_time=stored[0].close_time,stored=>stored[1].provider='QUIDAX']) {
  test('alignment failure is controlled and never shifts measurements',()=>{
    const data=dataset();const stored=candles(data);mutate(stored)
    assert.throws(()=>intelligence.module.alignIntelligence(stored,data),/does not align/)
  })
}
test('render points retain warm-up whitespace and reject non-renderable numbers',()=>{
  const rows=dataset().features
  const result=charts.module.measurementPoints(rows,'ema_50')
  assert.deepEqual(result[0],{time:Date.parse(rows[0].open_time)/1000})
  assert.equal(result[1].value,Number(rows[1].ema_50))
  assert.equal(rows[0].ema_50,null)
  assert.throws(()=>charts.module.measurementPoints([{...rows[0],ema_50:'9'.repeat(400)}],'ema_50'),/cannot be rendered/)
})
const mockChart=()=>{
  const added=[];const stretches=[]
  return {added,stretches,addSeries(definition,options,pane){const series={options,pane,data:null,refs:[],setData(data){this.data=data},applyOptions(next){this.options={...this.options,...next}},createPriceLine(options){const ref={options,applyOptions(next){this.options={...this.options,...next}}};this.refs.push(ref);return ref}};added.push(series);return series},panes(){return [0,1,2].map(index=>({setStretchFactor(value){stretches[index]=value}}))}}}
test('actual chart wiring renders EMA20/50, RSI references and all MACD series on shared panes',()=>{
  const chart=mockChart();const rows=dataset().features
  const theme=charts.module.addIntelligence(chart,rows,{ema20:true,ema50:true,rsi:true,macd:true},name=>`dark:${name}`)
  assert.deepEqual(chart.added.map(item=>[item.options.title,item.pane]),[['EMA20',0],['EMA50',0],['RSI14',1],['MACD histogram',2],['MACD',2],['MACD signal',2]])
  for(const series of chart.added){assert.equal(series.data[0].value,undefined);assert.equal(series.data[1].time,Date.parse(rows[1].open_time)/1000)}
  assert.deepEqual(chart.added[2].refs.map(item=>[item.options.price,item.options.title]),[[30,'30 reference'],[70,'70 reference']])
  assert.deepEqual(chart.stretches,[3,1,1]);theme()
})
test('toggles hide/show only series with no fetch or financial recalculation; theme reuses values',()=>{
  globalThis.fetch=()=>{throw Error('toggle must not fetch')}
  for(const key of ['ema20','ema50','rsi','macd']){
    const chart=mockChart();let mode='dark'
    const apply=charts.module.addIntelligence(chart,dataset().features,{ema20:false,ema50:false,rsi:false,macd:false,[key]:true},token=>`${mode}:${token}`)
    assert.equal(chart.added.length,key==='macd'?3:1)
    const data=chart.added.map(item=>item.data);mode='light';apply()
    assert.ok(chart.added.every(item=>item.options.color.startsWith('light:')))
    assert.deepEqual(chart.added.map(item=>item.data),data)
  }
  const empty=mockChart();charts.module.addIntelligence(empty,[],{ema20:false,ema50:false,rsi:false,macd:false},String)
  assert.equal(empty.added.length,0)
})
test('exact ATR, unannualized volatility, version and warm-up inspection render accessibly',()=>{
  const data=dataset();const html=renderToStaticMarkup(React.createElement(component.module.default,{candles:candles(data),intelligence:data,intelligenceError:''}))
  assert.match(html,/Engine v1.0.0/);assert.match(html,/Latest available ATR14/);assert.match(html,/Unannualized/)
  assert.match(html,/1.12345678901234567890123456789/);assert.match(html,/30 \/ 70 reference levels only/)
  assert.match(html,/aria-pressed="true"/);assert.match(html,/exact backend values/)
  data.features.forEach(row=>fields.forEach(field=>row[field]=null))
  const warmup=renderToStaticMarkup(React.createElement(component.module.default,{candles:candles(data),intelligence:data,intelligenceError:''}))
  assert.match(warmup,/insufficient stored history/);assert.match(warmup,/Unavailable · warm-up/)
  assert.equal(intelligence.module.latestAvailable(data.features,'atr_14'),null)
})
test('API error leaves candle inspection and controlled error text visible',()=>{
  const data=dataset();const html=renderToStaticMarkup(React.createElement(component.module.default,{candles:candles(data),intelligence:null,intelligenceError:'Intelligence unavailable'}))
  assert.match(html,/Intelligence unavailable/);assert.match(html,/Inspect candle/);assert.match(html,/disabled/)
  assert.doesNotMatch(html,/Engine v1.0.0/)
})


test('duplicate stored timestamps and render-boundary underflow fail clearly',()=>{
  const data=dataset();const stored=candles(data);stored[1]={...stored[0]}
  assert.throws(()=>intelligence.module.alignIntelligence(stored,data),/does not align/)
  assert.throws(()=>charts.module.measurementPoints([{...row(),ema_20:'0.'+'0'.repeat(400)+'1'}],'ema_20'),/cannot be rendered/)
})
test('alignment failure leaves real OHLC available and disables overlays',()=>{
  const data=dataset();const stored=candles(data);stored[1].close='99'
  const html=renderToStaticMarkup(React.createElement(component.module.default,{candles:stored,intelligence:data,intelligenceError:''}))
  assert.match(html,/does not align/);assert.match(html,/disabled/);assert.match(html,/Inspect candle/)
  assert.doesNotMatch(html,/Latest available ATR14/)
})


test('formatted compact displays preserve exact OHLC and indicator inspection strings',()=>{
  const data=dataset();data.features[1].atr_14='423.35733606088409620180230675811334041567187877481'
  data.features[1].realized_volatility='0.00259252'
  const stored=candles(data);stored[1].close='83415.32000000';data.features[1].close=stored[1].close
  const html=renderToStaticMarkup(React.createElement(component.module.default,{candles:stored,intelligence:data,intelligenceError:''}))
  assert.match(html,/83,415.32/);assert.match(html,/423.36/);assert.match(html,/0.2593%/)
  assert.match(html,/83415.32000000/);assert.match(html,/Exact: 423.35733606088409620180230675811334041567187877481/)
  assert.equal(data.features[1].realized_volatility,'0.00259252')
})


test('misaligned regimes leave the actual candle chart and intelligence usable',()=>{
 const d=dataset();const stored=candles(d)
 const regimeRows=d.features.map((r,i)=>({open_time:r.open_time,close_time:r.close_time,regime:'UNKNOWN',candidate_regime:'UNKNOWN',confirmation:{count:0,required:0,previous_regime:null,retained_previous:false,available_candles:i+1,candidate_history_required:53},evidence:{},reason_codes:['WARMUP_REQUIRED']}))
 regimeRows[1].open_time='2026-10-17T00:00:00Z'
 const regimeData={canonical_symbol:'BTC/USDT',provider:'BINANCE',timeframe:'1h',candle_count:2,regimes:regimeRows,dataset_start:stored[0].open_time,dataset_end:stored[1].close_time,latest_regime:regimeRows[1],regime_engine_version:'1.0.0'}
 const html=renderToStaticMarkup(React.createElement(component.module.default,{candles:stored,intelligence:d,intelligenceError:'',regimes:regimeData,regimeError:''}))
 assert.match(html,/Regimes do not align/);assert.match(html,/Inspect candle/);assert.match(html,/Latest available ATR14/)
 assert.doesNotMatch(html,/categorical regime strip/);assert.doesNotMatch(html,/Selected candle regime/)
})
