import { useEffect, useRef, useState } from 'react'
import { AreaSeries, CandlestickSeries, ColorType, LineSeries, createChart, createSeriesMarkers,
  type IChartApi, type ISeriesApi, type SeriesType, type Time, type LogicalRange } from 'lightweight-charts'
import type { StoredCandle } from '../history/api'
import type { ResearchData } from './research'
import { researchEvents, timeKey } from './research'
import { candleCoordinates, chartNumber, drawdownAxisLabel, markerCoordinates, performanceCoordinates } from './chartBoundary'

type Cursor = { chart:IChartApi; series:ISeriesApi<SeriesType>; points:{time:Time;value?:number}[] }
export default function ResearchCharts({ candles, data, initialCapital, selected, onSelect }: {
  candles:StoredCandle[] | null; data:ResearchData; initialCapital:string; selected:string; onSelect:(openTime:string)=>void
}) {
  const priceHost=useRef<HTMLDivElement>(null), equityHost=useRef<HTMLDivElement>(null), drawdownHost=useRef<HTMLDivElement>(null)
  const cursors=useRef<Cursor[]>([]), reset=useRef(()=>{}), [error,setError]=useState('')
  useEffect(()=>{
    const charts:IChartApi[]=[];const disposers:(()=>void)[]=[];cursors.current=[];setError('')
    const token=(name:string)=>getComputedStyle(equityHost.current!).getPropertyValue(name).trim()
    const themed:(()=>void)[]=[]
    const make=(host:HTMLDivElement)=>{
      const chart=createChart(host,{autoSize:true,layout:{attributionLogo:true},timeScale:{timeVisible:true,secondsVisible:false},
        localization:{locale:'en-GB',timeFormatter:(time:Time)=>typeof time==='number' ? new Date(time*1000).toISOString().replace('T',' ').slice(0,19)+' UTC':String(time)}})
      charts.push(chart);return chart
    }
    try {
      if(candles?.length && priceHost.current){
        const chart=make(priceHost.current),series=chart.addSeries(CandlestickSeries,{borderVisible:false})
        const points=candleCoordinates(candles);series.setData(points)
        const markers=createSeriesMarkers(series,[])
        themed.push(()=>{series.applyOptions({upColor:token('--color-success'),downColor:token('--color-risk'),wickUpColor:token('--color-success'),wickDownColor:token('--color-risk')});markers.setMarkers(markerCoordinates(researchEvents(data),token))})
        cursors.current.push({chart,series,points:points.map(p=>({time:p.time,value:p.close}))})
      }
      const points=performanceCoordinates(data)
      if(equityHost.current){
        const chart=make(equityHost.current),series=chart.addSeries(LineSeries,{lineWidth:2,priceLineVisible:false})
        series.setData(points.equity)
        const reference=series.createPriceLine({price:chartNumber(initialCapital),color:token('--muted'),lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'Initial capital'})
        themed.push(()=>{series.applyOptions({color:token('--color-primary')});reference.applyOptions({color:token('--muted')})})
        cursors.current.push({chart,series,points:points.equity})
      }
      if(drawdownHost.current){
        const chart=make(drawdownHost.current),series=chart.addSeries(AreaSeries,{lineWidth:1,priceLineVisible:false,
          priceFormat:{type:'custom',formatter:drawdownAxisLabel}})
        series.setData(points.drawdown)
        series.createPriceLine({price:0,color:token('--muted'),lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'Zero drawdown'})
        themed.push(()=>series.applyOptions({lineColor:token('--color-risk'),topColor:'transparent',bottomColor:token('--soft')}))
        cursors.current.push({chart,series,points:points.drawdown})
      }
      const applyTheme=()=>{
        charts.forEach(chart=>chart.applyOptions({layout:{background:{type:ColorType.Solid,color:token('--surface')},textColor:token('--muted'),fontFamily:'Inter, Segoe UI, sans-serif'},
          grid:{vertLines:{visible:false},horzLines:{color:token('--line')}},rightPriceScale:{borderColor:token('--line')},timeScale:{borderColor:token('--line')},
          crosshair:{vertLine:{color:token('--brand-text')},horzLine:{color:token('--brand-text')}}}))
        themed.forEach(update=>update())
      }
      applyTheme();const observer=new MutationObserver(applyTheme)
      observer.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});disposers.push(()=>observer.disconnect())
      for(const cursor of cursors.current){
        const indices=new Map(cursor.points.map((point,i)=>[point.time,i]))
        cursor.chart.subscribeCrosshairMove(event=>{
          const i=event.time === undefined ? undefined : indices.get(event.time)
          if(i!==undefined) onSelect(data.decisions[i].open_time)
        })
        cursor.chart.timeScale().subscribeVisibleLogicalRangeChange((range:LogicalRange|null)=>{
          if(!range)return
          charts.filter(c=>c!==cursor.chart).forEach(chart=>{
            const prior=chart.timeScale().getVisibleLogicalRange()
            if(!prior || Math.abs(prior.from-range.from)>0.00001 || Math.abs(prior.to-range.to)>0.00001) chart.timeScale().setVisibleLogicalRange(range)
          })
        })
      }
      reset.current=()=>charts.forEach(chart=>chart.timeScale().fitContent());reset.current()
    }catch{disposers.forEach(dispose=>dispose());disposers.length=0;setError('Chart rendering is unavailable for these values. Exact backend inspection and the trade table remain available.');cursors.current=[];charts.forEach(c=>c.remove());charts.length=0}
    return()=>{disposers.forEach(dispose=>dispose());reset.current=()=>{};cursors.current=[];charts.forEach(chart=>chart.remove())}
  },[candles,data,initialCapital,onSelect])
  useEffect(()=>{
    const index=data.decisions.findIndex(d=>timeKey(d.open_time)===timeKey(selected))
    if(index<0)return
    cursors.current.forEach(cursor=>{
      const point=cursor.points[index]
      if(point?.value!==undefined)cursor.chart.setCrosshairPosition(point.value,point.time,cursor.series)
      else cursor.chart.clearCrosshairPosition()
    })
  },[selected,data,candles])
  return <div className="lab-chart-stack">
    <div className="lab-subheading"><h3>Evaluation timeline</h3><button type="button" className="lab-text-button" onClick={()=>reset.current()}>Reset chart views</button></div>
    <p className="lab-caption">Candles use open timestamps; equity and drawdown use completed close timestamps. Views share candle indices, not shifted event timestamps. Hover a point or use the inspection selector.</p>
    {error && <p className="lab-error" role="status">{error}</p>}
    {candles && <><h4>Stored candlesticks · Binance</h4><div className="lab-chart lab-candle-chart" ref={priceHost} role="img" aria-label="Real stored evaluation candles with separate strategy-decision and simulated-fill markers. Use the keyboard-accessible inspection below for exact values."/>
      <p className="lab-caption">D +L / −L / +S / −S: long/short entry/exit decision arrows. F circles: simulated fills at their eligible candle open and actual fill price. Squares marked unfilled have no execution marker.</p></>}
    <h4>Total equity · backend observations</h4><div className="lab-chart" ref={equityHost} role="img" aria-label="Backend evaluation equity at completed candle closes. Dashed reference is initial research capital. Exact values are available in the inspector."/>
    <h4>Drawdown · backend magnitude plotted downward</h4><div className="lab-chart lab-drawdown-chart" ref={drawdownHost} role="img" aria-label="Backend drawdown observations, zero at the reference and larger magnitudes downward. Undefined observations are gaps, not zero."/>
    <p className="lab-caption">Scroll to zoom · drag to pan · no warm-up performance observations. TradingView Lightweight Charts™ · <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView, Inc.</a></p>
  </div>
}
