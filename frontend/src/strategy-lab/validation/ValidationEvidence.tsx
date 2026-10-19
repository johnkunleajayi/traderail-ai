import {Card} from '../../components/ui'
import {utcLabel} from '../TechnicalDetails'
import type {Boundaries,ValidationResult,WireObject} from './types'
import {EvidenceDetails,ExactMetrics,MetricGrid,Values,exact,label} from './presentation'
function Window({window}:{window:Boundaries}){return <dl className="lab-validation-window">{Object.entries(window).map(([key,value])=><div key={key}><dt>{label(key)} · UTC</dt><dd>{utcLabel(value)}</dd></div>)}</dl>}
const descriptions={
  INSUFFICIENT_EVIDENCE:'Required historical evidence quantity or coverage is insufficient. This is not a failed-validation verdict.',
  FAILED_VALIDATION:'The backend found sufficient evidence coverage, but one or more declared policy criteria failed.',
  VALIDATION_CANDIDATE:'The available historical research evidence is sufficient to justify consideration for the next controlled validation stage.',
}
export default function ValidationEvidence({result}:{result:ValidationResult}){
  const v=result.validation,e=result.verdict_explanation
  const reasonList=(codes:readonly string[])=> <ul>{codes.map(code=><li key={code}><code>{code}</code>{result.criteria.filter(c=>c.code===code||c.reason_code===code).map(c=><p key={c.code}>{c.explanation}</p>)}</li>)}</ul>
  return <Card className="lab-validation-report">
    <div className="lab-subheading"><div><p className="section-label">BACKEND RESEARCH VERDICT</p><h2>Formal validation evidence</h2></div><span className="lab-tag">Policy v{v.policy_version}</span></div>
    <p className="lab-caption">Adaptive Trend v{v.strategy_version} · {v.canonical_symbol} · {v.provider} · {v.timeframe} · {v.candle_count} stored candles</p>
    <p className={`lab-verdict verdict-${v.verdict}`} role="status">{v.verdict}</p><p className="lab-caption">{descriptions[v.verdict]}</p>
    <p className="lab-note">This research verdict does not authorize paper, shadow or live trading, change strategy lifecycle, or automatically promote a strategy.</p>
    <Window window={v.primary_boundaries}/>
    <section aria-labelledby="validation-why"><h3 id="validation-why">What drove this verdict?</h3>
      {([['Insufficiency reasons',e.insufficiency_reasons],['Failure reasons',e.failure_reasons],['Passed criteria',e.passed_criteria]] as const).map(([title,codes])=><div className="lab-validation-reasons" key={title}><h4>{title}</h4>{!codes.length?<p className="lab-caption">None reported.</p>:title==='Passed criteria'?<EvidenceDetails title={`Inspect ${codes.length} passed criteria`}>{reasonList(codes)}</EvidenceDetails>:<>{reasonList(codes.slice(0,3))}{codes.length>3&&<EvidenceDetails title={`All ${codes.length} backend reasons · returned order`}>{reasonList(codes)}</EvidenceDetails>}</>}</div>)}
      <p className="lab-caption">Inspect coverage and policy explanations below, then review independent folds, parameter stability, benchmark context and execution-cost resilience. The frontend does not determine the verdict.</p>
    </section>
    <section aria-labelledby="validation-oos"><h3 id="validation-oos">Primary out-of-sample evidence</h3><p className="lab-caption">OOS: {utcLabel(v.primary_boundaries.out_of_sample_start)} → {utcLabel(v.primary_boundaries.end)} (exclusive). IS and warm-up performance are excluded.</p><MetricGrid metrics={result.primary_oos.metrics}/><ExactMetrics metrics={result.primary_oos.metrics}/></section>
    <p className="lab-caption">Detailed evidence keeps exact API units: returns and drawdowns are fractions; exposure is already a percentage; turnover is a ratio; monetary values use the instrument quote currency.</p>
    <EvidenceDetails title="Walk-forward · independent folds">
      <p className="lab-caption">{result.walk_forward.active_folds} active folds returned. Profitable, losing, breakeven and zero-trade counts below are backend summaries. No continuous portfolio is fabricated and folds are not ranked.</p>
      <Values values={result.walk_forward.summary}/>
      {result.walk_forward.folds.map(fold=><EvidenceDetails key={fold.fold_identity} title={`Fold ${fold.index+1} · chronological observation`}><Window window={fold.boundaries}/><MetricGrid metrics={fold.metrics}/><ExactMetrics metrics={fold.metrics}/><Values values={{fold_identity:fold.fold_identity,validation_identity:fold.validation_identity,backtest_identity:fold.backtest_identity}}/></EvidenceDetails>)}
    </EvidenceDetails>
    <EvidenceDetails title="Sensitivity · baseline and fixed probes">
      <p className="lab-caption">Explicit backend OFAT research probes; no parameter recommendation, ranking or automatic replacement.</p>
      <EvidenceDetails title="Baseline · unchanged Adaptive Trend configuration"><MetricGrid metrics={result.primary_oos.metrics}/><p className="lab-caption">Baseline metrics are the primary OOS evidence above.</p><Values values={{configuration_identity:result.sensitivity_baseline_configuration_identity,validation_identity:result.primary_oos.validation_identity}}/></EvidenceDetails>
      {result.sensitivity.map(probe=><EvidenceDetails key={probe.configuration_identity} title={`Probe · ${probe.changed_fields.map(label).join(', ')||'No changed fields returned'}`}><Values values={probe.changed_parameters}/><MetricGrid metrics={probe.metrics}/>{probe.differences.map((diff,i)=><div key={i}><p>Return-sign changed: {exact(diff.return_sign_changed)}</p><div className="lab-table-scroll" role="region" tabIndex={0} aria-label="Sensitivity evidence differences"><table className="lab-validation-table"><caption>Backend baseline / variant differences · exact values</caption><thead><tr><th scope="col">Metric</th><th scope="col">Baseline</th><th scope="col">Variant</th><th scope="col">Variant minus baseline</th></tr></thead><tbody>{diff.metrics.map(m=><tr key={m.metric}><th scope="row">{label(m.metric)}</th><td>{exact(m.baseline)}</td><td>{exact(m.variant)}</td><td>{exact(m.variant_minus_baseline)}</td></tr>)}</tbody></table></div></div>)}<ExactMetrics metrics={probe.metrics}/><Values values={{configuration_identity:probe.configuration_identity,validation_identity:probe.validation_identity}}/></EvidenceDetails>)}
    </EvidenceDetails>
    <EvidenceDetails title="Benchmarks · CASH and BUY_AND_HOLD">
      <p className="lab-caption">Comparison context for the same primary OOS window, dataset and initial capital. Benchmark underperformance only affects the verdict when the backend policy makes it mandatory.</p>
      <Values values={{cash_treatment:v.policy_configuration.cash_treatment??null,buy_and_hold_treatment:v.policy_configuration.buy_and_hold_treatment??null}}/>
      {result.benchmarks.map(b=><EvidenceDetails key={b.result_identity} title={`${b.kind} · comparison context`}><Values values={{initial_capital:b.initial_capital,ending_equity:b.ending_equity,total_return:b.total_return,maximum_drawdown:b.maximum_drawdown,exposure_percentage:b.exposure_percentage,turnover:b.turnover,fees:b.fees,spread_cost:b.spread_cost,slippage_cost:b.slippage_cost}}/><h4>Strategy minus benchmark · backend differences</h4><Values values={b.strategy_minus_benchmark}/><Values values={{dataset_identity:b.dataset_identity,window_identity:b.window_identity,comparison_identity:b.comparison_identity,result_identity:b.result_identity}}/></EvidenceDetails>)}
    </EvidenceDetails>
    <EvidenceDetails title="Stress · all returned execution scenarios">
      <p className="lab-caption">BASELINE anchors every difference. Research assumptions only; no venue fee claim or scenario ranking. Unaffordable scenarios stay visible.</p>
      {result.stress.map(s=><EvidenceDetails key={s.scenario_identity} title={s.code}><h4>Exact execution assumptions</h4><Values values={s.assumptions as unknown as WireObject}/>
        {s.execution_failure!==null?<p className="lab-error" role="status">Execution failure: {s.execution_failure}. Performance metrics: Unavailable. Quantity was not silently resized.</p>:s.metrics&&<><MetricGrid metrics={s.metrics}/><ExactMetrics metrics={s.metrics}/>{s.differences?.map((diff,i)=><div key={i}><p>Return-sign changed: {exact(diff.return_sign_changed)}</p><Values values={{total_cost_change:diff.total_cost_change,...Object.fromEntries(diff.metric_changes)}}/></div>)}</>}
        <Values values={{scenario_identity:s.scenario_identity,validation_identity:s.validation_identity,result_identity:s.result_identity,failure_source_identity:s.failure_source_identity}}/></EvidenceDetails>)}
    </EvidenceDetails>
    <EvidenceDetails title="Validation criteria · observed evidence and declared requirements">
      <p className="lab-caption">All checks, categories, statuses and thresholds come from the backend. Fractional values and counts below retain their exact API units; no client policy evaluation occurs.</p>
      <div className="lab-table-scroll" tabIndex={0} role="region" aria-label="Backend validation criteria"><table className="lab-validation-table"><caption>Backend research-policy criteria · exact observations and thresholds</caption><thead><tr>{['Category / criterion','Observed','Required','Status','Reason / explanation'].map(h=><th scope="col" key={h}>{h}</th>)}</tr></thead><tbody>{result.criteria.map(c=><tr key={c.code}><th scope="row">{c.category}<br/>{c.code}</th><td>{exact(c.observed)}</td><td>{exact(c.required)}</td><td><span className={`lab-tag criterion-${c.status}`}>{c.status}</span></td><td><code>{c.reason_code}</code><p>{c.explanation}</p></td></tr>)}</tbody></table></div>
    </EvidenceDetails>
    <EvidenceDetails title="Technical identities, policy and source evidence">
      <Values values={{validation_identity:v.validation_identity,report_identity:v.report_identity,evidence_identity:v.evidence_identity,dataset_identity:v.dataset_identity,strategy_configuration_identity:v.strategy_configuration_identity,policy_identity:v.policy_identity,policy_version:v.policy_version,initial_capital:v.initial_capital,quantity:v.quantity,primary_validation_identity:result.primary_oos.validation_identity,primary_backtest_identity:result.primary_oos.backtest_identity,walk_forward_identity:result.walk_forward.validation_identity,sensitivity_identity:result.sensitivity_identity,stress_evidence_identity:result.stress_evidence_identity}}/>
      <h4>Source evidence identities</h4><Values values={v.source_evidence_identities}/><h4>Backend policy configuration · research hypotheses</h4><Values values={v.policy_configuration}/><h4>Baseline execution assumptions</h4><Values values={v.baseline_execution as unknown as WireObject}/>
    </EvidenceDetails>
  </Card>
}
