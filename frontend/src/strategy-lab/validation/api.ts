import {apiBase} from '../../auth/api'
import type {StoredCandle,Timeframe} from '../../history/api'
import {record,timeKey,decimal} from '../research'
import {parseValidation} from './schema'
import type {ValidationRequest} from './types'
export class ValidationApiError extends Error{
  constructor(message:string,public status:number){super(message);this.name='ValidationApiError'}
}
export async function runValidation(request:ValidationRequest,signal?:AbortSignal){
  let response:Response
  try{response=await fetch(`${apiBase}/api/strategy-lab/validations`,{method:'POST',credentials:'include',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify(request),signal})}
  catch(error){if(signal?.aborted)throw error;throw new ValidationApiError('TradeRail could not be reached. No validation evidence was substituted.',0)}
  if(!response.ok){
    const messages:Record<number,string>={401:'Your session has expired. Sign in again to run validation.',403:'This frontend origin is not permitted to run validation. Check the configured TradeRail frontend URL.',404:'This canonical instrument is unavailable.',409:'Validation could not complete for this stored dataset and research assumptions. Check exact history availability, dataset quality, capital and quantity.',422:'Validation inputs were rejected. Check chronological UTC boundaries, exact candle count, warm-up, folds and execution assumptions.',503:'Validation storage is temporarily unavailable. Please retry.'}
    let message=messages[response.status]??'Validation is temporarily unavailable. No replacement evidence was created.'
    if(response.status===409){let code='';try{code=(await response.json()).detail?.code??''}catch{/* Only safe fixed messages. */}
      if(code==='insufficient_stored_history')message='The exact instrument/provider/timeframe has insufficient stored history. No data was ingested.'
      if(code==='invalid_candle_dataset')message='Stored candles failed canonical data validation. No gaps were repaired or hidden.'}
    throw new ValidationApiError(message,response.status)
  }
  try{return parseValidation(await response.json(),request)}catch(error){if(signal?.aborted)throw error;throw new ValidationApiError('The validation response could not be read or matched to this request. No evidence was substituted.',502)}
}
export async function readValidationHistory(symbol:string,timeframe:Timeframe,signal?:AbortSignal):Promise<StoredCandle[]>{
  const response=await fetch(`${apiBase}/api/market-data/stored-candles/${encodeURIComponent(symbol)}?timeframe=${timeframe}&provider=BINANCE&limit=1000`,{credentials:'omit',cache:'no-store',signal})
  if(!response.ok)throw new Error('Stored history could not be read. Enter known stored UTC boundaries manually; no data was ingested.')
  try{
    const data:unknown=await response.json();if(!Array.isArray(data)||data.length>1000)throw new Error()
    let prior=''
    for(const item of data){const c=record(item),key=timeKey(c.open_time)
      if(c.canonical_symbol!==symbol||c.provider!=='BINANCE'||c.timeframe!==timeframe||key<=prior||!['open','high','low','close','volume'].every(k=>decimal(c[k])))throw new Error()
      prior=key;timeKey(c.close_time);timeKey(c.retrieved_at)}
    return data
  }catch{throw new Error('Stored history could not be verified. No boundaries or evidence were invented.')}
}
