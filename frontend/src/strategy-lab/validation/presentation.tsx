import type {ReactNode} from 'react'
import {formatMarketValue,formatVolatility} from '../../markets/format'
import type {Metrics,Wire,WireObject} from './types'
export const label=(value:string)=>value.replaceAll('_',' ')
export function exact(value:Wire):ReactNode{
  if(value===null)return 'Unavailable'
  if(typeof value==='boolean')return value?'Yes':'No'
  if(typeof value!=='object')return String(value)
  if(Array.isArray(value))return value.length?<ul>{value.map((v,i)=><li key={i}>{exact(v)}</li>)}</ul>: 'None returned'
  return <Values values={value}/>
}
export function Values({values}:{values:WireObject}){return <dl className="lab-validation-values">{Object.entries(values).map(([key,value])=><div key={key}><dt>{label(key)}</dt><dd>{exact(value)}</dd></div>)}</dl>}
export function MetricGrid({metrics}:{metrics:Metrics}){
  const display:[keyof Metrics,string,('fraction'|'percent')?][]=[['total_return','Total return','fraction'],['net_return','Net return','fraction'],['maximum_drawdown','Max drawdown','fraction'],['trade_count','Completed trades'],['win_rate','Win rate','fraction'],['profit_factor','Profit factor'],['expectancy','Expectancy'],['exposure_percentage','Exposure','percent'],['turnover','Turnover · ratio'],['total_fees','Fees'],['total_spread_cost','Spread cost'],['total_slippage_cost','Slippage cost']]
  return <dl className="lab-metrics">{display.map(([key,title,unit])=>{const value=metrics[key];return <div className="lab-metric" key={key}><dt>{title}</dt><dd title={value===null?undefined:String(value)}>{value===null?'Unavailable':typeof value==='number'?String(value):unit==='fraction'?formatVolatility(value):unit==='percent'?formatMarketValue(value)+'%':formatMarketValue(value)}</dd></div>})}</dl>
}
export function EvidenceDetails({title,children}:{title:string;children:ReactNode}){return <details className="lab-validation-section"><summary>{title}</summary>{children}</details>}
export function ExactMetrics({metrics}:{metrics:Metrics}){return <EvidenceDetails title="Exact backend metric values"><Values values={metrics}/></EvidenceDetails>}
