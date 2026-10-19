import {decimal,timeKey} from '../research'
import {moneyMetrics,verdicts,type ValidationRequest,type ValidationResult} from './types'
type Row=Record<string,unknown>
const bad=()=>{throw new Error('The validation response could not be read or matched to this request. No evidence was substituted.')}
function obj(value:unknown):Row{if(!value||typeof value!=='object'||Array.isArray(value))return bad();return value as Row}
function list(value:unknown):unknown[]{if(!Array.isArray(value)||value.length>2000)return bad();return value}
const text=(v:unknown):v is string=>typeof v==='string'&&v.length>0
const integer=(v:unknown):v is number=>Number.isSafeInteger(v)
const count=(v:unknown)=>integer(v)&&v>=0
const d=(v:unknown)=>v===null||decimal(v)
function check(value:unknown):asserts value{if(!value)bad()}
function strings(value:unknown){check(list(value).every(text))}
function ids(row:Row,keys:string[]){check(keys.every(key=>text(row[key])))}
function wire(value:unknown,depth=0):void{
  if(depth>12)bad()
  if(value===null||typeof value==='boolean'||typeof value==='string')return
  if(typeof value==='number'){check(integer(value));return}
  if(Array.isArray(value)){list(value).forEach(v=>wire(v,depth+1));return}
  Object.values(obj(value)).forEach(v=>wire(v,depth+1))
}
function equivalent(value:unknown,expected:string){
  if(!decimal(value))return false
  const normalize=(s:string)=>{const [whole,tail='']=s.split('.');return (whole||'0').replace(/^0+(?=\d)/,'')+'.'+tail.replace(/0+$/,'')}
  return normalize(value)===normalize(expected)
}
function bounds(value:unknown,expected:ValidationRequest['primary']){
  const row=obj(value)
  for(const key of ['warmup_start','in_sample_start','out_of_sample_start','end'] as const)check(timeKey(row[key])===timeKey(expected[key]))
}
function metrics(value:unknown){
  const row=obj(value);check(count(row.trade_count)&&moneyMetrics.every(key=>d(row[key])))
  check(['total_fees','total_spread_cost','total_slippage_cost'].every(key=>decimal(row[key])))
}
function assumptions(value:unknown,expected?:ValidationRequest['execution']){
  const row=obj(value);check(['fee_rate','full_spread_rate','slippage_rate'].every(key=>decimal(row[key]))&&count(row.delay_candles))
  ids(row,['timing','cost_model_identity','execution_configuration_identity'])
  if(expected){check(['fee_rate','full_spread_rate','slippage_rate'].every(key=>equivalent(row[key],expected[key as keyof typeof expected] as string)));check(row.delay_candles===expected.delay_candles)}
}
function benchmark(value:unknown,meta:Row){
  const b=obj(value);check(['CASH','BUY_AND_HOLD'].includes(b.kind as string))
  ids(b,['dataset_identity','window_identity','canonical_symbol','quote_currency','comparison_identity','result_identity'])
  check(b.dataset_identity===meta.dataset_identity&&b.canonical_symbol===meta.canonical_symbol
    &&b.quote_currency===(meta.canonical_symbol as string).split('/')[1]&&equivalent(b.initial_capital,meta.initial_capital as string))
  check(['ending_equity','fees','spread_cost','slippage_cost'].every(k=>decimal(b[k])))
  check(['total_return','maximum_drawdown','exposure_percentage','turnover'].every(k=>d(b[k])))
  check(Object.values(obj(b.strategy_minus_benchmark)).every(d));return b
}
const categories=['EVIDENCE_COVERAGE','RETURN_QUALITY','DRAWDOWN_CONTROL','WALK_FORWARD_CONSISTENCY','PARAMETER_STABILITY','COST_RESILIENCE','BENCHMARK_CONTEXT']
export function parseValidation(value:unknown,request:ValidationRequest):ValidationResult{
  wire(value);const body=obj(value),v=obj(body.validation),primary=obj(body.primary_oos),walk=obj(body.walk_forward),explanation=obj(body.verdict_explanation)
  ids(v,['api_version','validation_identity','report_identity','evidence_identity','strategy_configuration_identity','dataset_identity','policy_identity','policy_version'])
  check(v.canonical_symbol===request.canonical_symbol&&v.provider===request.provider&&v.timeframe===request.timeframe
    &&v.strategy_family==='ADAPTIVE_TREND'&&v.strategy_version==='1.0.0'&&v.candle_count===request.requested_candle_count
    &&verdicts.includes(v.verdict as typeof verdicts[number])&&equivalent(v.initial_capital,request.initial_capital)&&equivalent(v.quantity,request.quantity))
  bounds(v.primary_boundaries,request.primary);assumptions(v.baseline_execution,request.execution);obj(v.policy_configuration)
  check(Object.keys(obj(v.source_evidence_identities)).length>0&&Object.values(obj(v.source_evidence_identities)).every(text))
  ids(primary,['validation_identity','backtest_identity']);metrics(primary.metrics)
  ids(walk,['validation_identity']);check(count(walk.active_folds))
  const folds=list(walk.folds);check(folds.length===request.walk_forward_folds.length)
  folds.forEach((item,i)=>{const f=obj(item);check(f.index===i);bounds(f.boundaries,request.walk_forward_folds[i]);ids(f,['fold_identity','validation_identity','backtest_identity']);metrics(f.metrics)})
  const summary=obj(walk.summary)
  check(['fold_count','profitable_folds','losing_folds','breakeven_folds','undefined_return_folds','defined_drawdown_folds','total_completed_trades','zero_trade_folds','defined_expectancy_folds','defined_profit_factor_folds'].every(k=>count(summary[k])))
  check(summary.fold_count===folds.length&&(walk.active_folds as number)<=folds.length)
  check(['mean_net_return','median_net_return','worst_net_return','best_net_return','median_maximum_drawdown','worst_maximum_drawdown','mean_expectancy','mean_profit_factor'].every(k=>d(summary[k])))
  check(['total_fees','total_spread_cost','total_slippage_cost'].every(k=>decimal(summary[k])))
  check(summary.return_sign_consistency===null||['POSITIVE','NEGATIVE','ZERO','MIXED'].includes(summary.return_sign_consistency as string))
  ids(body,['sensitivity_identity','sensitivity_baseline_configuration_identity','stress_evidence_identity'])
  check(body.sensitivity_baseline_configuration_identity===v.strategy_configuration_identity)
  const probes=list(body.sensitivity),probeIds=new Set()
  probes.forEach(item=>{const p=obj(item);ids(p,['configuration_identity','validation_identity']);check(!probeIds.has(p.configuration_identity));probeIds.add(p.configuration_identity)
    strings(p.changed_fields);check(Object.values(obj(p.changed_parameters)).every(decimal));check((p.changed_fields as string[]).every(k=>Object.hasOwn(obj(p.changed_parameters),k)))
    metrics(p.metrics)
    list(p.differences).forEach(item=>{const difference=obj(item);check(count(difference.fold_index)&&(difference.return_sign_changed===null||typeof difference.return_sign_changed==='boolean'))
      list(difference.metrics).forEach(item=>{const m=obj(item);check(text(m.metric));check(['baseline','variant','variant_minus_baseline'].every(k=>m[k]===null||(m.metric==='trade_count'?integer(m[k]):decimal(m[k]))))})})
  })
  const benchmarks=list(body.benchmarks).map(item=>benchmark(item,v));check(benchmarks.length===2&&new Set(benchmarks.map(b=>b.kind)).size===2)
  const scenarioCodes=new Set();list(body.stress).forEach(item=>{
    const s=obj(item);ids(s,['code','scenario_identity']);check(!scenarioCodes.has(s.code));scenarioCodes.add(s.code);assumptions(s.assumptions)
    if(s.execution_failure!==null){check(text(s.execution_failure)&&text(s.failure_source_identity)&&s.metrics===null&&s.differences===null&&s.validation_identity===null&&s.result_identity===null)}
    else{ids(s,['validation_identity','result_identity']);check(s.failure_source_identity===null);metrics(s.metrics)
      list(s.differences).forEach(item=>{const diff=obj(item);check(count(diff.fold_index)&&decimal(diff.total_cost_change)&&(diff.return_sign_changed===null||typeof diff.return_sign_changed==='boolean'))
        list(diff.metric_changes).forEach(item=>{const pair=list(item);check(pair.length===2&&text(pair[0])&&(pair[1]===null||(pair[0]==='trade_count'?integer(pair[1]):decimal(pair[1]))))})})}
    if(s.code==='BASELINE'){check(s.execution_failure===null);assumptions(s.assumptions,request.execution)}
  });check(scenarioCodes.has('BASELINE'))
  const criterionCodes=new Set();check(list(body.criteria).length>0)
  list(body.criteria).forEach(item=>{const c=obj(item);ids(c,['code','reason_code','explanation']);check(!criterionCodes.has(c.code));criterionCodes.add(c.code)
    check(categories.includes(c.category as string)&&['SUFFICIENT','INSUFFICIENT','PASS','FAIL','NOT_APPLICABLE'].includes(c.status as string))
    for(const key of ['observed','required'])check(c[key]===null||typeof c[key]==='string'||typeof c[key]==='boolean'||integer(c[key]))
  })
  for(const key of ['insufficiency_reasons','failure_reasons','passed_criteria'])strings(explanation[key])
  const contexts=list(explanation.benchmark_context).map(item=>benchmark(item,v))
  check(contexts.length===benchmarks.length&&contexts.every(c=>benchmarks.some(b=>b.kind===c.kind&&b.result_identity===c.result_identity&&b.comparison_identity===c.comparison_identity)))
  // Preserve server order, exact decimals and the server verdict. No policy evaluation here.
  return value as ValidationResult
}
