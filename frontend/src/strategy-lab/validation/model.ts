import {defaultForm,buildRequest,type ExperimentFormValues} from '../model'
import type {StoredCandle} from '../../history/api'
import type {Boundaries,ValidationRequest} from './types'
export const duration={ '15m':900000,'1h':3600000,'4h':14400000,'1d':86400000 }
export const emptyWindow=():Boundaries=>({warmup_start:'',in_sample_start:'',out_of_sample_start:'',end:''})
export type ValidationFormValues=Omit<ExperimentFormValues,'evaluation_start'|'evaluation_end'>&{primary:Boundaries;walk_forward_folds:Boundaries[]}
export const initialValidationForm=():ValidationFormValues=>{
  const {evaluation_start:_,evaluation_end:__,...common}=defaultForm
  return {...common,primary:emptyWindow(),walk_forward_folds:[emptyWindow()]}
}
function utc(value:string){
  if(!/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?$/.test(value))throw new Error('Choose explicit UTC timestamps for every boundary.')
  const full=value.length===16?value+':00':value,date=new Date(full+'Z')
  if(!Number.isFinite(date.getTime())||date.toISOString().slice(0,19)!==full)throw new Error('A UTC boundary is invalid.')
  return date.toISOString()
}
function windowRequest(window:Boundaries,interval:number):Boundaries{
  const result=Object.fromEntries(Object.entries(window).map(([key,value])=>[key,utc(value)])) as unknown as Boundaries
  const times=[result.warmup_start,result.in_sample_start,result.out_of_sample_start,result.end].map(Date.parse)
  if(times.some((time,i)=>i>0&&time<=times[i-1]))throw new Error('Each window must progress from warm-up to IS to OOS to exclusive end.')
  if(times[1]-times[0]<52*interval)throw new Error('Each window requires at least 52 analytical warm-up candles.')
  if(times.some(time=>(time-times[0])%interval!==0))throw new Error('All boundaries must align to whole candles in the declared window.')
  return result
}
export function buildValidationRequest(form:ValidationFormValues):ValidationRequest{
  // Reuse Part 1 decimal/count/cost input validation; no financial calculations.
  const common=buildRequest({...form,evaluation_start:form.primary.in_sample_start,evaluation_end:form.primary.end})
  if(common.requested_candle_count<54)throw new Error('Formal validation requires at least 54 requested candles.')
  const interval=duration[common.timeframe],primary=windowRequest(form.primary,interval)
  if(Date.parse(primary.end)-Date.parse(primary.warmup_start)!==common.requested_candle_count*interval)throw new Error('Primary history duration must match the exact requested candle count.')
  if(form.walk_forward_folds.length<1||form.walk_forward_folds.length>4)throw new Error('Declare one to four independent walk-forward folds.')
  const folds=form.walk_forward_folds.map(w=>windowRequest(w,interval))
  folds.forEach((fold,i)=>{
    if(Date.parse(fold.warmup_start)<Date.parse(primary.warmup_start)||Date.parse(fold.end)>Date.parse(primary.end)
      ||(i>0&&Date.parse(fold.warmup_start)<Date.parse(folds[i-1].end)))throw new Error('Folds must be chronological, inside primary history, and cannot overlap, including warm-up.')
    if((Date.parse(fold.warmup_start)-Date.parse(primary.warmup_start))%interval!==0)throw new Error('Folds must align with the primary candle timestamps.')
  })
  const {evaluation_start:_,evaluation_end:__,...base}=common
  return {...base,primary,walk_forward_folds:folds}
}
export function suggestValidationWindows(candles:StoredCandle[],form:ValidationFormValues){
  if(!/^\d+$/.test(form.requested_candle_count))throw new Error('Choose a whole-number candle count first.')
  const count=Number(form.requested_candle_count),folds=form.walk_forward_folds.length
  if(count<54||count>300)throw new Error('Choose 54–300 stored candles.')
  if(candles.length<count)throw new Error('This exact instrument/provider/timeframe has fewer stored candles than requested. No history will be ingested.')
  if(folds<1||folds>4||Math.floor(count/folds)<54)throw new Error('Each suggested disjoint fold needs 52 warm-up candles plus IS and OOS observations. Use fewer folds or more stored history.')
  const selected=candles.slice(-count),interval=duration[form.timeframe]
  selected.forEach((c,i)=>{if(i>0&&Date.parse(c.open_time)-Date.parse(selected[i-1].open_time)!==interval)throw new Error('Stored history contains a gap; no boundaries were suggested.')})
  const timestamp=(index:number)=>new Date(index===selected.length?Date.parse(selected[index-1].open_time)+interval:selected[index].open_time).toISOString().slice(0,19)
  const window=(start:number,end:number):Boundaries=>({warmup_start:timestamp(start),in_sample_start:timestamp(start+52),
    out_of_sample_start:timestamp(start+52+Math.floor((end-start-52)/2)),end:timestamp(end)})
  const size=Math.floor(count/folds)
  return {primary:window(0,count),walk_forward_folds:Array.from({length:folds},(_,i)=>window(i*size,i===folds-1?count:(i+1)*size))}
}
